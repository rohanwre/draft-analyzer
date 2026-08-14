import json
import time

from advisor import get_db, normalize_name

SCORING_FIELD = {"1.0": "pts_ppr", "0.5": "pts_half_ppr", "0.0": "pts_std"}
WEEKS = range(1, 18)
SEASON_MIN, SEASON_MAX = 2020, 2025

def parse_requirements(roster_positions_json):
    """Counts QB/RB/WR/TE/FLEX/REC_FLEX/SFLEX slots from a league's roster_positions JSON,
    ignoring K/DEF/IDP/BN slots (this app never recommends/tracks those positions).
    WRRB_FLEX (Sleeper's RB/WR-only flex) is folded into FLEX since this app already
    treats plain FLEX as RB/WR-only; REC_FLEX (WR/TE-only flex) gets its own bucket
    since its eligibility doesn't match FLEX or SFLEX."""
    try:
        positions = json.loads(roster_positions_json) if roster_positions_json else []
    except (TypeError, ValueError):
        positions = []
    req = {"QB": 0, "RB": 0, "WR": 0, "TE": 0, "FLEX": 0, "REC_FLEX": 0, "SFLEX": 0}
    for p in positions:
        if p in req:
            req[p] += 1
        elif p == "WRRB_FLEX":
            req["FLEX"] += 1
        elif p == "SUPER_FLEX":
            req["SFLEX"] += 1
    return req

def compute_week_points(drafted_pts_by_position, req):
    """Optimal-lineup points for one week: best players fill dedicated slots first,
    then FLEX (RB/WR only - matches this app's FLEX rule everywhere else), then
    REC_FLEX (WR/TE only), then SFLEX (any position) from whatever's left over."""
    total = 0.0
    leftover = []

    for pos in ("QB", "RB", "WR", "TE"):
        vals = sorted(drafted_pts_by_position.get(pos, []), reverse=True)
        take = req.get(pos, 0)
        total += sum(vals[:take])
        leftover.extend((pos, v) for v in vals[take:])

    leftover.sort(key=lambda x: x[1], reverse=True)
    used = set()

    flex_take = req.get("FLEX", 0)
    flex_count = 0
    for i, (pos, v) in enumerate(leftover):
        if flex_count >= flex_take:
            break
        if pos in ("RB", "WR"):
            total += v
            used.add(i)
            flex_count += 1

    recflex_take = req.get("REC_FLEX", 0)
    recflex_count = 0
    for i, (pos, v) in enumerate(leftover):
        if recflex_count >= recflex_take:
            break
        if i in used:
            continue
        if pos in ("WR", "TE"):
            total += v
            used.add(i)
            recflex_count += 1

    sflex_take = req.get("SFLEX", 0)
    sflex_count = 0
    for i, (pos, v) in enumerate(leftover):
        if sflex_count >= sflex_take:
            break
        if i in used:
            continue
        total += v
        used.add(i)
        sflex_count += 1

    return total

def get_incomplete_league_ids(cursor):
    print("Finding leagues not yet computed...")
    cursor.execute("""
        SELECT DISTINCT r.league_id
        FROM rosters r JOIN leagues l ON r.league_id = l.league_id
        WHERE l.season BETWEEN %s AND %s AND l.season_type = 'regular'
        AND r.optimal_points_total IS NULL
    """, (SEASON_MIN, SEASON_MAX))
    ids = [row[0] for row in cursor.fetchall()]
    print(f"  {len(ids)} leagues remaining")
    return ids

def stage_incomplete_leagues(cursor, db, league_ids):
    """Stages the remaining league_ids in a temp table so every subsequent load can
    join against it, instead of pulling the full 2020-2025 dataset (~10M draft picks)
    every time this script runs — that mismatch (rosters resumed correctly, but picks
    always reloaded everything) is the likely cause of repeated kills on resume."""
    cursor.execute("DROP TEMPORARY TABLE IF EXISTS incomplete_leagues")
    cursor.execute("CREATE TEMPORARY TABLE incomplete_leagues (league_id VARCHAR(20) PRIMARY KEY)")
    batch = [(lid,) for lid in league_ids]
    for i in range(0, len(batch), 5000):
        cursor.executemany("INSERT IGNORE INTO incomplete_leagues (league_id) VALUES (%s)", batch[i:i + 5000])
    db.commit()

def load_projections(cursor):
    print("Loading projections...")
    cursor.execute("""
        SELECT season, week, player_name, pts_ppr, pts_half_ppr, pts_std
        FROM player_weekly_projections
    """)
    proj = {}
    for season, week, name, ppr, half, std in cursor.fetchall():
        key = (season, week, normalize_name(name))
        proj[key] = (ppr, half, std)
    print(f"  {len(proj)} projection entries loaded")
    return proj

def load_leagues(cursor):
    print("Loading leagues (incomplete only)...")
    cursor.execute("""
        SELECT l.league_id, l.season, l.scoring_type, l.roster_positions,
               l.league_format, l.lineage_id, l.season_index
        FROM leagues l JOIN incomplete_leagues il ON l.league_id = il.league_id
    """)
    leagues = {}
    for league_id, season, scoring_type, roster_positions, league_format, lineage_id, season_index in cursor.fetchall():
        leagues[league_id] = {
            "season": season,
            "field_idx": {"1.0": 0, "0.5": 1, "0.0": 2}.get(scoring_type, 0),
            "req": parse_requirements(roster_positions),
            "league_format": league_format,
            "lineage_id": lineage_id,
            "season_index": season_index,
        }
    print(f"  {len(leagues)} leagues loaded")
    return leagues

def build_lineage_pools(cursor, leagues):
    """For every incomplete dynasty league, finds every league_id in the same lineage
    (startup draft + every rookie draft since) so its roster's optimal lineup can be
    computed from the cumulative pick pool, not just this one season's picks. Returns
    lineage_id -> [(season_index, league_id), ...] sorted ascending, for just the
    lineages that actually need it (keeper/redraft leagues never touch this)."""
    lineage_ids = {
        l["lineage_id"] for l in leagues.values()
        if l["league_format"] == "dynasty" and l["lineage_id"]
    }
    pools = {}
    if not lineage_ids:
        return pools
    lineage_ids = list(lineage_ids)
    for i in range(0, len(lineage_ids), 1000):
        batch = lineage_ids[i:i + 1000]
        placeholders = ",".join(["%s"] * len(batch))
        cursor.execute(f"""
            SELECT lineage_id, season_index, league_id
            FROM leagues
            WHERE lineage_id IN ({placeholders})
        """, batch)
        for lineage_id, season_index, league_id in cursor.fetchall():
            pools.setdefault(lineage_id, []).append((season_index, league_id))
    for lid in pools:
        pools[lid].sort()
    print(f"  {len(pools)} dynasty lineages need cumulative pick pools")
    return pools

def load_rosters(cursor):
    print("Loading rosters (incomplete only)...")
    cursor.execute("""
        SELECT r.league_id, r.roster_id, r.owner_id
        FROM rosters r JOIN incomplete_leagues il ON r.league_id = il.league_id
        WHERE r.optimal_points_total IS NULL
    """)
    rosters_by_league = {}
    for league_id, roster_id, owner_id in cursor.fetchall():
        rosters_by_league.setdefault(league_id, []).append((roster_id, owner_id))
    print(f"  {sum(len(v) for v in rosters_by_league.values())} rosters remaining")
    return rosters_by_league

def stage_pick_source_leagues(cursor, db, league_ids):
    """Broader than incomplete_leagues: includes every dynasty lineage-predecessor league
    too, even ones already fully computed, since their picks still feed into a later
    season's cumulative pool."""
    cursor.execute("DROP TEMPORARY TABLE IF EXISTS pick_source_leagues")
    cursor.execute("CREATE TEMPORARY TABLE pick_source_leagues (league_id VARCHAR(20) PRIMARY KEY)")
    batch = [(lid,) for lid in league_ids]
    for i in range(0, len(batch), 5000):
        cursor.executemany("INSERT IGNORE INTO pick_source_leagues (league_id) VALUES (%s)", batch[i:i + 5000])
    db.commit()

def load_picks(cursor):
    print("Loading draft picks (incomplete + dynasty lineage-predecessor leagues)...")
    cursor.execute("""
        SELECT dp.league_id, dp.owner_id, dp.position, dp.player_name
        FROM draft_picks dp JOIN pick_source_leagues psl ON dp.league_id = psl.league_id
        WHERE dp.position IN ('QB', 'RB', 'WR', 'TE')
    """)
    picks = {}
    for league_id, owner_id, position, name in cursor.fetchall():
        key = (league_id, owner_id)
        picks.setdefault(key, []).append((position, normalize_name(name)))
    print(f"  {len(picks)} roster pick-lists loaded")
    return picks

def main():
    db = get_db()
    cursor = db.cursor()

    incomplete_ids = get_incomplete_league_ids(cursor)
    if not incomplete_ids:
        print("Nothing to do - all leagues already computed.")
        cursor.close()
        db.close()
        return

    stage_incomplete_leagues(cursor, db, incomplete_ids)

    proj = load_projections(cursor)
    leagues = load_leagues(cursor)
    rosters_by_league = load_rosters(cursor)
    lineage_pools = build_lineage_pools(cursor, leagues)

    pick_source_ids = set(incomplete_ids)
    for pool in lineage_pools.values():
        pick_source_ids.update(league_id for _, league_id in pool)
    stage_pick_source_leagues(cursor, db, pick_source_ids)

    picks = load_picks(cursor)

    print("Computing optimal lineups per roster...")
    start = time.time()
    updates = []
    processed_leagues = 0

    for league_id, league in leagues.items():
        season = league["season"]
        field_idx = league["field_idx"]
        req = league["req"]
        rosters = rosters_by_league.get(league_id, [])
        if not rosters:
            continue

        # dynasty rosters draw from every draft in the lineage so far (startup + rookie
        # drafts), not just this one season's picks - redraft/keeper leagues are unaffected
        source_league_ids = [league_id]
        if league["league_format"] == "dynasty" and league["lineage_id"] and league["season_index"]:
            pool = lineage_pools.get(league["lineage_id"], [])
            picked_from_pool = [lid for idx, lid in pool if idx <= league["season_index"]]
            if picked_from_pool:
                source_league_ids = picked_from_pool

        totals = []
        for roster_id, owner_id in rosters:
            drafted = []
            for lid in source_league_ids:
                drafted.extend(picks.get((lid, owner_id), []))
            season_total = 0.0
            for week in WEEKS:
                by_pos = {}
                for position, norm_name in drafted:
                    entry = proj.get((season, week, norm_name))
                    if entry is None:
                        continue
                    pts = entry[field_idx]
                    if pts is None:
                        continue
                    by_pos.setdefault(position, []).append(pts)
                season_total += compute_week_points(by_pos, req)
            totals.append((roster_id, season_total))

        totals.sort(key=lambda x: x[1], reverse=True)
        for i, (roster_id, total) in enumerate(totals):
            is_top_two = 1 if i < 2 else 0
            updates.append((round(total, 1), is_top_two, league_id, roster_id))

        processed_leagues += 1
        if processed_leagues % 500 == 0:
            elapsed = time.time() - start
            print(f"  {processed_leagues}/{len(leagues)} leagues computed ({elapsed:.0f}s elapsed)")

        if len(updates) >= 20000:
            cursor.executemany("""
                UPDATE rosters SET optimal_points_total = %s, top_two_optimal = %s
                WHERE league_id = %s AND roster_id = %s
            """, updates)
            db.commit()
            updates = []

    if updates:
        cursor.executemany("""
            UPDATE rosters SET optimal_points_total = %s, top_two_optimal = %s
            WHERE league_id = %s AND roster_id = %s
        """, updates)
        db.commit()

    elapsed = time.time() - start
    print(f"Done! Processed {processed_leagues} leagues in {elapsed:.0f}s")

    cursor.close()
    db.close()

if __name__ == "__main__":
    main()
