import re
import random
import mysql.connector
from config import DB_CONFIG

MIN_SAMPLE_SIZE = 20

SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


NAME_ALIASES = {
    "hollywood brown": "marquise brown",
}

def get_db():
    return mysql.connector.connect(**DB_CONFIG)

def input_int(prompt, default=None):
    while True:
        try:
            raw = input(prompt).strip()
            if not raw and default is not None:
                return default
            return int(raw)
        except (ValueError, EOFError):
            print("  Please enter a valid number.")

def normalize_name(name):
    if not name:
        return ""
    name = name.strip().lower()
    name = name.replace(".", "").replace("'", "")
    name = name.replace(".", "").replace("'", "").replace("-", " ")
    name = re.sub(r"\s+", " ", name)
    tokens = [t for t in name.split() if t not in SUFFIXES]
    normalized = " ".join(tokens)
    return NAME_ALIASES.get(normalized, normalized)

def derive_league_type(qb_slots, sflex_slots):
    if sflex_slots and sflex_slots > 0:
        return "qb_premium"
    if qb_slots and qb_slots >= 2:
        return "qb_premium"
    return "standard"

def pick_order_for_round(round_num, league_size):
    if round_num % 2 == 1:
        return range(1, league_size + 1)
    return range(league_size, 0, -1)

def global_pick_number(round_num, pick_slot, league_size):
    slot_in_round = pick_slot if round_num % 2 == 1 else league_size - pick_slot + 1
    return (round_num - 1) * league_size + slot_in_round

def lookup_position(cursor, player_name, season):
    target = normalize_name(player_name)
    if not target:
        return None, None

    cursor.execute("""
        SELECT player_name, position FROM adp WHERE season = %s
    """, (season,))
    rows = cursor.fetchall()
    for name, position in rows:
        if normalize_name(name) == target:
            return name, position

    cursor.execute("""
        SELECT player_name, position, season FROM adp
        ORDER BY season DESC
    """)
    rows = cursor.fetchall()
    for name, position, _ in rows:
        if normalize_name(name) == target:
            return name, position

    return None, None

def search_players(cursor, query, season, limit=10):
    target = normalize_name(query)
    if not target:
        return []

    cursor.execute("""
        SELECT player_name, position FROM adp WHERE season = %s
    """, (season,))

    results = []
    seen = set()
    for name, position in cursor.fetchall():
        normalized = normalize_name(name)
        if target in normalized and normalized not in seen:
            seen.add(normalized)
            results.append({"name": name, "position": position})
        if len(results) >= limit:
            break

    return results

def get_player_input(cursor, season, all_picks, prompt_label="Who was drafted?"):
    taken_normalized = {normalize_name(name) for _, name in all_picks if name}

    while True:
        player_name = input(f"\n{prompt_label} ").strip()

        if not player_name:
            print("  Name can't be blank, try again.")
            continue

        if normalize_name(player_name) in taken_normalized:
            print(f"  {player_name} was already drafted — pick someone else.")
            continue

        matched_name, position = lookup_position(cursor, player_name, season)

        if position:
            if normalize_name(matched_name) in taken_normalized:
                print(f"  {matched_name} was already drafted — pick someone else.")
                continue
            confirm = input(f"  Found: {matched_name} ({position}) — correct? (y/n): ").strip().lower()
            if confirm != "n":
                return matched_name, position
        else:
            print(f"  No match found for '{player_name}' in ADP data.")

        choice = input("  Try a different spelling (s), or enter position manually (m)? ").strip().lower()
        if choice == "m":
            manual_position = input("  Position (QB/RB/WR/TE): ").strip().upper()
            if normalize_name(player_name) in taken_normalized:
                print(f"  {player_name} was already drafted — pick someone else.")
                continue
            return player_name, manual_position

def record_pick(cursor, all_picks, season, player_name, manual_position=None):
    player_name = (player_name or "").strip()
    if not player_name:
        return {"status": "invalid", "message": "Name can't be blank, try again."}

    taken_normalized = {normalize_name(name) for _, name in all_picks if name}

    if normalize_name(player_name) in taken_normalized:
        return {"status": "already_taken", "message": f"{player_name} was already drafted — pick someone else."}

    if manual_position:
        return {"status": "manual", "name": player_name, "position": manual_position.strip().upper()}

    matched_name, position = lookup_position(cursor, player_name, season)

    if position:
        if normalize_name(matched_name) in taken_normalized:
            return {"status": "already_taken", "message": f"{matched_name} was already drafted — pick someone else."}
        return {"status": "matched", "name": matched_name, "position": position}

    return {"status": "not_found", "message": f"No match found for '{player_name}' in ADP data.", "query": player_name}

def _run_first_pick_query(cursor, draft_slot, league_size, league_type, te_premium, season):
    query = """
        SELECT r.top_two_seed, dp.position
        FROM rosters r
        JOIN leagues l ON r.league_id = l.league_id
        JOIN draft_picks dp ON r.owner_id = dp.owner_id
            AND r.league_id = dp.league_id
            AND dp.round = 1
            AND dp.draft_slot = %s
        WHERE l.league_size = %s
        AND l.league_type = %s
        AND l.season_type = 'regular'
    """
    params = [draft_slot, league_size, league_type]

    if te_premium is not None:
        query += " AND l.te_premium = %s"
        params.append(te_premium)

    cursor.execute(query, params)
    return cursor.fetchall()

def _run_sequence_query(cursor, league_size, league_type, te_premium, position_sequence, current_round, season):
    # cap sequence to avoid slow nested queries in later rounds
    position_sequence = position_sequence[-3:]
    start_round = current_round - len(position_sequence)

    sequence_query = """
        SELECT r.owner_id, r.league_id, r.top_two_seed
        FROM rosters r
        JOIN leagues l ON r.league_id = l.league_id
        WHERE l.league_size = %s
        AND l.league_type = %s
        AND l.season_type = 'regular'
    """
    params = [league_size, league_type]

    if te_premium is not None:
        sequence_query += " AND l.te_premium = %s"
        params.append(te_premium)

    for i, position in enumerate(position_sequence, 1):
        actual_round = start_round + i - 1
        sequence_query += f"""
            AND EXISTS (
                SELECT 1 FROM draft_picks dp{i}
                WHERE dp{i}.owner_id = r.owner_id
                AND dp{i}.league_id = r.league_id
                AND dp{i}.round = {actual_round}
                AND dp{i}.position = %s
            )
        """
        params.append(position)

    cursor.execute(sequence_query, params)
    matching_rosters = cursor.fetchall()

    if not matching_rosters:
        return []

    results = []
    for owner_id, league_id, top_two_seed in matching_rosters:
        cursor.execute("""
            SELECT position FROM draft_picks
            WHERE owner_id = %s AND league_id = %s AND round = %s
            ORDER BY pick_no
            LIMIT 1
        """, (owner_id, league_id, current_round))
        row = cursor.fetchone()
        if row:
            results.append((top_two_seed, row[0]))

    return results

def find_similar_drafts(cursor, draft_slot, league_size, league_type, te_premium,
                         position_sequence, current_round, season=None):
    if not position_sequence:
        results = _run_first_pick_query(cursor, draft_slot, league_size, league_type, te_premium, season)
        if len(results) < MIN_SAMPLE_SIZE and te_premium is not None:
            results = _run_first_pick_query(cursor, draft_slot, league_size, league_type, None, season)
        return results

    results = _run_sequence_query(cursor, league_size, league_type, te_premium, position_sequence, current_round, season)
    if len(results) < MIN_SAMPLE_SIZE and te_premium is not None:
        results = _run_sequence_query(cursor, league_size, league_type, None, position_sequence, current_round, season)
    return results

def calculate_recommendation(similar_drafts):
    position_stats = {}

    for top_two_seed, position in similar_drafts:
        if position not in ["QB", "RB", "WR", "TE"]:
            continue
        if position not in position_stats:
            position_stats[position] = {"top_two": 0, "total": 0}
        position_stats[position]["total"] += 1
        if top_two_seed:
            position_stats[position]["top_two"] += 1

    total_top_two = sum(v["top_two"] for v in position_stats.values())

    recommendations = []
    for position, stats in position_stats.items():
        top_two_pct = round(stats["top_two"] / total_top_two * 100, 1) if total_top_two > 0 else 0
        recommendations.append({
            "position": position,
            "top_two_pct": top_two_pct,
            "sample_size": stats["total"]
        })

    recommendations.sort(key=lambda x: x["top_two_pct"], reverse=True)
    return recommendations

def get_general_round_trends(cursor, league_size, league_type, current_round):
    cursor.execute("""
        SELECT dp.position,
            SUM(CASE WHEN r.top_two_seed = 1 THEN 1 ELSE 0 END) as top_two_count,
            COUNT(*) as total_count,
            ROUND(SUM(CASE WHEN r.top_two_seed = 1 THEN 1 ELSE 0 END) * 100.0 /
                NULLIF(SUM(SUM(CASE WHEN r.top_two_seed = 1 THEN 1 ELSE 0 END)) OVER (), 0), 1) as top_two_pct
        FROM draft_picks dp
        JOIN rosters r ON dp.owner_id = r.owner_id AND dp.league_id = r.league_id
        JOIN leagues l ON dp.league_id = l.league_id
        WHERE l.league_size = %s
        AND dp.round = %s
        AND dp.position IN ('QB', 'RB', 'WR', 'TE')
        AND l.league_type = %s
        AND l.season_type = 'regular'
        GROUP BY dp.position
        ORDER BY top_two_pct DESC
    """, (league_size, current_round, league_type))
    return cursor.fetchall()

def resolve_adp_league_type(cursor, season, league_type):
    if league_type == "standard":
        return league_type
    cursor.execute("""
        SELECT COUNT(*) FROM adp WHERE season = %s AND league_type = %s
    """, (season, league_type))
    if cursor.fetchone()[0] == 0:
        return "standard"
    return league_type

def get_scarcity(cursor, all_picks, current_pick_number, season, league_settings, adp_league_type="standard"):
    position_counts = {}
    for position, _ in all_picks:
        if position in ["QB", "RB", "WR", "TE"]:
            position_counts[position] = position_counts.get(position, 0) + 1

    flex = league_settings.get("flex", 1)
    sflex = league_settings.get("sflex", 0)
    size = league_settings.get("league_size", 12)
    flex_share = flex / 3

    demand = {
        "QB": (league_settings.get("qb", 1) + sflex * 0.25) * size,
        "RB": (league_settings.get("rb", 2) + flex_share) * size,
        "WR": (league_settings.get("wr", 2) + flex_share) * size,
        "TE": (league_settings.get("te", 1) + flex_share) * size,
    }

    total_demand = sum(demand.values())

    alerts = []
    for position, count in position_counts.items():
        cursor.execute("""
            SELECT COUNT(*) FROM adp
            WHERE season = %s AND position = %s AND adp <= %s AND league_type = %s
        """, (season, position, current_pick_number, adp_league_type))
        expected = cursor.fetchone()[0]

        if expected == 0:
            continue

        demand_ratio = demand.get(position, 0) / total_demand if total_demand > 0 else 0
        threshold = max(1.05, 1.2 - (demand_ratio * 0.5))

        actual_ratio = count / expected
        if actual_ratio > threshold:
            alerts.append({
                "position": position,
                "taken": count,
                "expected": expected,
                "ratio": round(actual_ratio, 2),
                "demand_pct": round(demand_ratio * 100, 1),
                "threshold": round(threshold, 2)
            })

    alerts.sort(key=lambda x: x["ratio"] / x["threshold"], reverse=True)
    return alerts

def get_adp_rank_lookup(cursor, season, adp_league_type="standard"):
    """Maps normalized player name -> overall ADP rank (1-based position across all
    positions, sorted adp then tiebreak_adp) for a season/league_type. This is what the
    UI shows instead of the raw decimal ADP — an integer rank is what people expect from
    a mock draft board, and ties in the averaged ADP are broken deterministically by
    Sleeper's own ranking rather than left to arbitrary SQL row order."""
    pool = fetch_adp_pool(cursor, season, adp_league_type)
    return {normalize_name(name): rank for rank, (name, position, adp) in enumerate(pool, start=1)}

def get_available_players(cursor, all_picks, position, season, rank_lookup, adp_league_type="standard", limit=5):
    taken_normalized = {normalize_name(name) for _, name in all_picks if name}

    cursor.execute("""
        SELECT player_name, adp FROM adp
        WHERE season = %s AND position = %s AND league_type = %s
        ORDER BY adp, tiebreak_adp IS NULL, tiebreak_adp
    """, (season, position, adp_league_type))

    available = [
        (name, adp, rank_lookup.get(normalize_name(name)))
        for name, adp in cursor.fetchall()
        if normalize_name(name) not in taken_normalized
    ]
    return available[:limit]

def fetch_adp_pool(cursor, season, adp_league_type="standard"):
    """Full ADP-sorted candidate pool for a season/league_type, fetched once. Passing
    this into simulate_pick() for every pick of a simulated draft (instead of having
    simulate_pick re-query the DB each time) is what makes simulate-to-user-turn fast —
    a 12-team/15-round draft was issuing 100+ full-table queries before this."""
    cursor.execute("""
        SELECT player_name, position, adp FROM adp
        WHERE season = %s AND position IN ('QB', 'RB', 'WR', 'TE') AND league_type = %s
        AND adp > 0
        ORDER BY adp, tiebreak_adp IS NULL, tiebreak_adp
    """, (season, adp_league_type))
    return cursor.fetchall()

def get_team_position_counts(all_picks, league_size, target_slot):
    """Reconstructs one team's roster-so-far from the flat all_picks sequence (which
    doesn't track team ownership directly) using the same round/slot math as
    compute_turn_state."""
    counts = {}
    for i, (position, _) in enumerate(all_picks):
        round_num = i // league_size + 1
        slot = pick_order_for_round(round_num, league_size)[i % league_size]
        if slot == target_slot and position in ("QB", "RB", "WR", "TE"):
            counts[position] = counts.get(position, 0) + 1
    return counts

REQUIRED_STARTERS = {"QB": "qb", "RB": "rb", "WR": "wr", "TE": "te"}

def position_pick_multiplier(position, team_counts, league_settings):
    """Down-weights a position once a simulated team already has its starters at it, so
    mock drafts round out a roster instead of stacking one position. QB/TE (single-need,
    rarely double-rostered early) taper off hard; RB/WR (flex-eligible, normal to stock
    the bench) taper gently so late-round RB/WR depth still happens."""
    required = league_settings.get(REQUIRED_STARTERS.get(position, ""), 1)
    count = team_counts.get(position, 0)
    if count < required:
        return 1.0
    surplus = count - required + 1
    if position in ("QB", "TE"):
        return max(0.08, 0.35 ** surplus)
    return max(0.35, 0.75 ** surplus)

SIMULATE_POOL_SIZE = 12
SIMULATE_RANK_DECAY = 0.6

def simulate_pick(pool, all_picks, league_size, league_settings, current_pick_slot):
    """Mock-draft-realistic random pick: candidates are the top SIMULATE_POOL_SIZE
    still-available players by ADP, weighted by geometric decay on rank within that
    pool (not raw ADP value or a flat 60-deep pool) so the best player left goes most
    of the time and big multi-round falls stay rare instead of routine. Weight is then
    scaled by position_pick_multiplier so a team already stocked at a position is less
    likely to double up, without hard-blocking it (bench depth at RB/WR is realistic;
    a third QB before the draft is nearly over is not)."""
    taken_normalized = {normalize_name(name) for _, name in all_picks if name}

    candidates = [
        (name, position, adp) for name, position, adp in pool
        if normalize_name(name) not in taken_normalized
    ][:SIMULATE_POOL_SIZE]

    if not candidates:
        return None

    team_counts = get_team_position_counts(all_picks, league_size, current_pick_slot)
    weights = [
        (SIMULATE_RANK_DECAY ** rank) * position_pick_multiplier(position, team_counts, league_settings)
        for rank, (name, position, adp) in enumerate(candidates)
    ]
    name, position, adp = random.choices(candidates, weights=weights, k=1)[0]
    return {"name": name, "position": position, "adp": adp}

def get_full_adp_list(cursor, season, adp_league_type="standard"):
    """Full ADP board for the watchlist sidebar — every drafted-eligible player at this
    season/league_type, sorted by ADP (ties broken by Sleeper ranking), no limit."""
    pool = fetch_adp_pool(cursor, season, adp_league_type)
    return [
        {"name": name, "position": position, "adp": adp, "rank": rank}
        for rank, (name, position, adp) in enumerate(pool, start=1)
    ]

def get_positional_need(my_picks, league_settings, current_round):
    counts = {}
    for _, pos, _ in my_picks:
        counts[pos] = counts.get(pos, 0) + 1

    total_rounds = league_settings.get("total_rounds", 15)
    rounds_left = total_rounds - current_round
    needed = []

    if counts.get("QB", 0) < league_settings.get("qb", 1):
        needed.append(("QB", "urgent" if rounds_left <= 4 else "need"))

    if counts.get("TE", 0) < league_settings.get("te", 1):
        needed.append(("TE", "urgent" if rounds_left <= 3 else "need"))

    if counts.get("RB", 0) < league_settings.get("rb", 2):
        needed.append(("RB", "need"))

    if counts.get("WR", 0) < league_settings.get("wr", 2):
        needed.append(("WR", "need"))

    return needed

def get_position_fill_status(my_picks, league_settings, current_round):
    """Per-position roster state used for score bonuses/penalties: how far below or
    above starter requirement each position sits. Deficits score positive (need/urgent),
    surpluses score negative and stack (-20 for exactly filled, -15 more per extra body)."""
    counts = {}
    for _, pos, _ in my_picks:
        counts[pos] = counts.get(pos, 0) + 1

    total_rounds = league_settings.get("total_rounds", 15)
    rounds_left = total_rounds - current_round
    requirements = {
        "QB": league_settings.get("qb", 1),
        "RB": league_settings.get("rb", 2),
        "WR": league_settings.get("wr", 2),
        "TE": league_settings.get("te", 1),
    }
    urgent_thresholds = {"QB": 4, "TE": 3}

    status = {}
    for position, required in requirements.items():
        count = counts.get(position, 0)
        deficit = required - count
        if deficit > 0:
            urgent = rounds_left <= urgent_thresholds.get(position, 0)
            status[position] = {"state": "urgent" if urgent else "need", "need_bonus": 30 if urgent else 10}
        else:
            over = count - required
            state = "filled" if over == 0 else "surplus"
            status[position] = {"state": state, "need_bonus": -20 - 15 * over}
    return status

VALUE_BONUS_PER_PICK = 1.5
ADP_QUALITY_SCALE = 1.0
TREND_WEIGHT = 0.6
RANKED_PLAYERS_POOL_PER_POSITION = 50

def get_ranked_players(cursor, all_picks, my_picks, league_settings, current_round, current_pick,
                        season, position_pct_lookup, rank_lookup, adp_league_type="standard", limit=10):
    """Single blended score per available player, mixing four signals:
      - trend_pct * TREND_WEIGHT: historical top-2-seed rate for this position at this
        point in similar drafts, damped to 0.6x. Full-strength trend_pct swings ~30 points
        between positions (e.g. WR ~49% vs QB ~19%) on its own, which is bigger than most
        individual ADP gaps between two available players — so undamped, position alone
        could out-rank a much better player at a "colder" position (a rank-40 WR out-scoring
        a rank-16 QB). Damping it keeps position trend an input, not the deciding one.
      - need_bonus: roster-fit adjustment (need/urgent add, an already-filled position subtracts)
      - value_bonus: uncapped bonus for how far the player has fallen past their own ADP
      - adp_quality_bonus: baseline player quality from raw ADP (max(0, 100 - adp) * 1.0),
        so e.g. Josh Allen (ADP 1.5) still outranks Trevor Lawrence (ADP 21.5) even when
        neither has fallen — without this term every player at a position was tied
        whenever value_bonus was 0 (i.e. anytime nobody there had actually fallen yet).
    value_bonus is uncapped and grows with how far a player has fallen, so a big enough
    fall (e.g. 20+ picks) outweighs a roster-need penalty (like already having a TE)
    without any special-case logic — it just falls out of the math."""
    fill_status = get_position_fill_status(my_picks, league_settings, current_round)

    candidates = []
    for position in ["QB", "RB", "WR", "TE"]:
        trend_pct = position_pct_lookup.get(position, 0)
        need_info = fill_status.get(position, {"state": None, "need_bonus": 0})
        players = get_available_players(
            cursor, all_picks, position, season, rank_lookup,
            adp_league_type=adp_league_type, limit=RANKED_PLAYERS_POOL_PER_POSITION,
        )
        for name, adp, rank in players:
            value = max(0, current_pick - adp)
            value_bonus = value * VALUE_BONUS_PER_PICK
            adp_quality_bonus = max(0, 100 - adp) * ADP_QUALITY_SCALE
            score = (trend_pct * TREND_WEIGHT) + need_info["need_bonus"] + value_bonus + adp_quality_bonus
            candidates.append({
                "name": name,
                "position": position,
                "adp": adp,
                "rank": rank,
                "score": round(score, 1),
                "trend_pct": trend_pct,
                "need": need_info["state"],
                "value": round(value, 1),
            })

    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates[:limit]

def build_recommendation(cursor, draft_slot, league_size, league_type, te_premium,
                          position_sequence, current_round, all_picks, my_picks, season, league_settings, current_pick):
    similar = find_similar_drafts(
        cursor, draft_slot, league_size, league_type, te_premium,
        position_sequence, current_round, season
    )
    adp_league_type = resolve_adp_league_type(cursor, season, league_type)
    rank_lookup = get_adp_rank_lookup(cursor, season, adp_league_type)

    position_pct_lookup = {}
    position_order = []

    if similar:
        trend_source = "similar_drafts"
        sample_size = len(similar)
        recommendations = calculate_recommendation(similar)
        for rec in recommendations:
            if rec["position"] in ["QB", "RB", "WR", "TE"]:
                position_pct_lookup[rec["position"]] = rec["top_two_pct"]
                position_order.append(rec["position"])
        trends = [{"position": p, "top_two_pct": position_pct_lookup[p]} for p in position_order]
    else:
        trend_source = "general_trends"
        sample_size = None
        rows = get_general_round_trends(cursor, league_size, league_type, current_round)
        trends = []
        for position, top_two_count, total_count, top_two_pct in rows:
            top_two_pct = float(top_two_pct or 0)
            position_pct_lookup[position] = top_two_pct
            position_order.append(position)
            trends.append({"position": position, "top_two_pct": top_two_pct})

    needs = get_positional_need(my_picks, league_settings, current_round)
    need_positions = {pos for pos, _ in needs}
    urgent_positions = {pos for pos, urg in needs if urg == "urgent"}

    # position_order is already ranked by trend % descending; show more depth for higher-ranked positions
    RANK_LIMITS = [5, 4, 3, 2]

    top_available_by_position = []
    for idx, position in enumerate(position_order):
        if position in urgent_positions:
            need_tag = "urgent"
        elif position in need_positions:
            need_tag = "need"
        else:
            need_tag = None
        limit = RANK_LIMITS[idx] if idx < len(RANK_LIMITS) else 2
        players = get_available_players(cursor, all_picks, position, season, rank_lookup, adp_league_type=adp_league_type, limit=limit)
        top_available_by_position.append({
            "position": position,
            "top_two_pct": position_pct_lookup.get(position, 0),
            "need": need_tag,
            "players": [{"name": name, "adp": adp, "rank": rank} for name, adp, rank in players]
        })

    value_picks = []
    for position in ["RB", "WR", "TE", "QB"]:
        candidates = get_available_players(cursor, all_picks, position, season, rank_lookup, adp_league_type=adp_league_type, limit=50)
        shown = 0
        for name, adp, rank in candidates:
            diff = adp - current_pick
            if diff < -3:
                value_picks.append({"name": name, "position": position, "adp": adp, "rank": rank, "value": abs(diff)})
                shown += 1
            if shown >= 2:
                break

    scarcity_alerts = get_scarcity(cursor, all_picks, current_pick, season, league_settings, adp_league_type=adp_league_type)

    ranked_players = get_ranked_players(
        cursor, all_picks, my_picks, league_settings, current_round, current_pick,
        season, position_pct_lookup, rank_lookup, adp_league_type=adp_league_type,
    )

    return {
        "draft_slot": draft_slot,
        "current_round": current_round,
        "position_sequence": position_sequence,
        "trend_source": trend_source,
        "sample_size": sample_size,
        "league_size": league_size,
        "league_type": league_type,
        "trends": trends,
        "positional_needs": [{"position": pos, "urgency": urg} for pos, urg in needs],
        "top_available_by_position": top_available_by_position,
        "value_picks": value_picks,
        "scarcity_alerts": scarcity_alerts,
        "ranked_players": ranked_players,
    }

def show_recommendation(cursor, draft_slot, league_size, league_type, te_premium,
                         position_sequence, current_round, all_picks, my_picks, season, league_settings, current_pick):
    rec = build_recommendation(
        cursor, draft_slot, league_size, league_type, te_premium,
        position_sequence, current_round, all_picks, my_picks, season, league_settings, current_pick
    )

    print(f"\n{'='*50}")
    print(f"Round {current_round} Recommendation (Slot {draft_slot})")
    print(f"Your picks so far: {' → '.join(position_sequence) if position_sequence else 'None'}")
    print(f"{'='*50}")

    if rec["trend_source"] == "similar_drafts":
        print(f"Based on {rec['sample_size']} similar historical drafts:")
    else:
        print(f"General round {current_round} trends (all {league_size}-team, {league_type} leagues):")

    for t in rec["trends"]:
        bar = "█" * int(t["top_two_pct"] / 5)
        print(f"  {t['position']}: {t['top_two_pct']:5.1f}% of top 2 seeds  {bar}")

    if rec["positional_needs"]:
        print("\nPositional needs:")
        for need in rec["positional_needs"]:
            if need["urgency"] == "urgent":
                print(f"  !! {need['position']} — URGENT, running out of rounds")
            else:
                print(f"  -> {need['position']} — not yet filled")

    print()
    print(f"Top available by position (ranked by historical trend, more depth shown for higher-ranked positions):")
    for group in rec["top_available_by_position"]:
        if group["need"] == "urgent":
            tag = "  [URGENT NEED]"
        elif group["need"] == "need":
            tag = "  [NEED]"
        else:
            tag = ""
        print(f"  {group['position']} — {group['top_two_pct']:.1f}% of top 2 seeds{tag}")
        for player in group["players"]:
            print(f"    {player['name']} - ADP {player['rank']}")

    print()
    print(f"Value picks (players available past their ADP):")
    for vp in rec["value_picks"]:
        print(f"  {vp['name']} ({vp['position']}) - ADP {vp['rank']}, still available (value: +{vp['value']:.0f} picks)")

    if rec["scarcity_alerts"]:
        print("\nScarcity alerts:")
        for alert in rec["scarcity_alerts"]:
            print(f"  {alert['position']} going {alert['ratio']}x faster than ADP suggests "
                  f"({alert['taken']} taken, expected {alert['expected']}, "
                  f"league demand: {alert['demand_pct']}% of starts)")

    print()
    print("Ranked picks (blended score: trend + roster need + ADP-fall value):")
    for rp in rec["ranked_players"]:
        tag = f" [{rp['need'].upper()}]" if rp["need"] in ("urgent", "need", "surplus") else ""
        print(f"  {rp['score']:5.1f}  {rp['name']} ({rp['position']}) - ADP {rp['rank']}{tag}")

def run_draft_advisor():
    db = get_db()
    cursor = db.cursor()

    print("=" * 50)
    print("   Welcome to the Fantasy Draft Advisor")
    print("=" * 50)

    league_size = input_int("\nHow many teams in your league? (8/10/12): ")
    draft_slot = input_int(f"What is your draft slot? (1-{league_size}): ")
    season = input_int("What season are you drafting for? (e.g. 2026): ")
    total_rounds = input_int("How many rounds in your draft? (default 15): ", default=15)

    print("\nLeague settings:")
    qb_slots = input_int("  QB starters (default 1): ", default=1)
    rb_slots = input_int("  RB starters (default 2): ", default=2)
    wr_slots = input_int("  WR starters (default 2): ", default=2)
    te_slots = input_int("  TE starters (default 1): ", default=1)
    flex_slots = input_int("  FLEX spots (default 1): ", default=1)
    sflex_slots = input_int("  SuperFlex spots (default 0): ", default=0)
    te_premium_input = input("  TE Premium scoring? (y/n): ").strip().lower() == "y"

    league_type = derive_league_type(qb_slots, sflex_slots)
    te_premium = 1 if te_premium_input else 0

    league_settings = {
        "qb": qb_slots, "rb": rb_slots, "wr": wr_slots,
        "te": te_slots, "flex": flex_slots, "sflex": sflex_slots,
        "te_premium": te_premium, "league_type": league_type,
        "league_size": league_size,
        "total_rounds": total_rounds
    }

    print(f"\nDetected league type: {league_type}" + (" (TE Premium)" if te_premium else ""))

    my_picks = []
    all_picks = []

    print("\n" + "=" * 50)
    print("Draft starting! Enter picks as they happen.")
    print("=" * 50)

    for current_round in range(1, total_rounds + 1):
        print(f"\n{'─'*50}")
        print(f"  ROUND {current_round}")
        print(f"{'─'*50}")

        pick_order = pick_order_for_round(current_round, league_size)

        for pick_slot in pick_order:
            global_pick = global_pick_number(current_round, pick_slot, league_size)

            if pick_slot == draft_slot:
                position_sequence = [p for _, p, _ in my_picks]
                show_recommendation(
                    cursor, draft_slot, league_size, league_type, te_premium,
                    position_sequence, current_round,
                    all_picks, my_picks, season, league_settings, global_pick
                )

                player_name, position = get_player_input(cursor, season, all_picks, "Who did you draft?")
                my_picks.append((current_round, position, player_name))
                all_picks.append((position, player_name))
                print(f"✓ {player_name} ({position}) added to your roster")

            else:
                player_name, position = get_player_input(cursor, season, all_picks, f"  Pick {global_pick} - Slot {pick_slot} (name):")
                all_picks.append((position, player_name))

    print("\n" + "=" * 50)
    print("Draft Complete! Your roster:")
    print("=" * 50)
    for round_num, position, player in my_picks:
        print(f"  Round {round_num:2d}: {player} ({position})")

    cursor.close()
    db.close()

if __name__ == "__main__":
    run_draft_advisor()