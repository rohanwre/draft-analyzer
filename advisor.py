import re
import random
import mysql.connector
from config import DB_CONFIG

MIN_SAMPLE_SIZE = 20

SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


NAME_ALIASES = {
    "hollywood brown": "marquise brown",
    "cam ward": "cameron ward",
    "cam skattebo": "cameron skattebo",
    "kenny gainwell": "kenneth gainwell",
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

def round_weight(round_num, total_rounds):
    """Round 1 of an N-round draft is worth N points, round N is worth 1 point - earlier
    picks carry more weight since they represent more draft capital/talent invested.
    Shared by build_trend_stats.py (historical aggregation) and the live scoring below -
    both must bucket identically or live lookups won't match the historical buckets."""
    return max(1, total_rounds - round_num + 1)

def bucket_score(score):
    """Buckets a cumulative round-weighted position score into a small number of tiers.
    Round is already a separate grouping/lookup dimension wherever this is used, so
    buckets don't need to be normalized by round - "heavy RB by round 4" and "heavy RB
    by round 10" are naturally different lookups already."""
    if score <= 0:
        return "NONE"
    if score <= 10:
        return "LIGHT"
    if score <= 25:
        return "MODERATE"
    return "HEAVY"

def compute_weighted_buckets(my_picks, total_rounds):
    """Cumulative round-weighted score per position from a live draft's picks so far,
    bucketed the same way as the historical draft_trend_stats table."""
    cumulative = {"QB": 0, "RB": 0, "WR": 0, "TE": 0}
    for round_num, position, _ in my_picks:
        if position in cumulative:
            cumulative[position] += round_weight(round_num, total_rounds)
    return {pos: bucket_score(score) for pos, score in cumulative.items()}

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

def _query_round1_stats(cursor, draft_slot, league_size, league_type, te_premium):
    """Reads the pre-aggregated round1_trend_stats table (built by build_trend_stats.py)
    instead of live-joining rosters/leagues/draft_picks - same data, computed once."""
    query = """
        SELECT position, SUM(total_count), SUM(success_count)
        FROM round1_trend_stats
        WHERE draft_slot = %s AND league_size = %s AND league_type = %s
    """
    params = [draft_slot, league_size, league_type]

    if te_premium is not None:
        query += " AND te_premium = %s"
        params.append(te_premium)

    query += " GROUP BY position"
    cursor.execute(query, params)
    # MySQL SUM() returns Decimal, not int/float - cast here so downstream scoring math
    # (which does plain float arithmetic) doesn't choke on mixed Decimal/float operands.
    return {position: {"total": int(total), "success": int(success)} for position, total, success in cursor.fetchall()}

def _query_trend_stats(cursor, league_size, league_type, te_premium, current_round, buckets):
    """Reads the pre-aggregated draft_trend_stats table using the live draft's own
    round-weighted position buckets (see compute_weighted_buckets) instead of live-joining
    draft_picks for a "last 3 positions" sequence match."""
    qb_b, rb_b, wr_b, te_b = buckets["QB"], buckets["RB"], buckets["WR"], buckets["TE"]
    query = """
        SELECT position, SUM(total_count), SUM(success_count)
        FROM draft_trend_stats
        WHERE league_size = %s AND league_type = %s AND round = %s
        AND qb_bucket = %s AND rb_bucket = %s AND wr_bucket = %s AND te_bucket = %s
    """
    params = [league_size, league_type, current_round, qb_b, rb_b, wr_b, te_b]

    if te_premium is not None:
        query += " AND te_premium = %s"
        params.append(te_premium)

    query += " GROUP BY position"
    cursor.execute(query, params)
    return {position: {"total": int(total), "success": int(success)} for position, total, success in cursor.fetchall()}

def find_similar_drafts(cursor, draft_slot, league_size, league_type, te_premium,
                         current_round, buckets):
    if current_round == 1:
        results = _query_round1_stats(cursor, draft_slot, league_size, league_type, te_premium)
        if sum(v["total"] for v in results.values()) < MIN_SAMPLE_SIZE and te_premium is not None:
            results = _query_round1_stats(cursor, draft_slot, league_size, league_type, None)
        return results

    results = _query_trend_stats(cursor, league_size, league_type, te_premium, current_round, buckets)
    if sum(v["total"] for v in results.values()) < MIN_SAMPLE_SIZE and te_premium is not None:
        results = _query_trend_stats(cursor, league_size, league_type, None, current_round, buckets)
    return results

def calculate_recommendation(position_stats):
    """position_stats: {position: {"total": N, "success": M}} from find_similar_drafts.
    top_two_pct here means "of the successful teams we saw, what share picked this
    position" (share of success, not per-position success rate) - matches the
    original semantics exactly, just computed from a pre-aggregated dict now instead
    of counting raw per-roster rows."""
    total_success = sum(v["success"] for v in position_stats.values())

    recommendations = []
    for position, stats in position_stats.items():
        top_two_pct = round(stats["success"] / total_success * 100, 1) if total_success > 0 else 0
        recommendations.append({
            "position": position,
            "top_two_pct": top_two_pct,
            "sample_size": stats["total"]
        })

    recommendations.sort(key=lambda x: x["top_two_pct"], reverse=True)
    return recommendations

def get_general_round_trends(cursor, league_size, league_type, current_round):
    """Same draft_trend_stats table as _query_trend_stats, but ignoring the bucket
    columns entirely (aggregating across every profile) and ignoring te_premium -
    matches the original fallback's behavior of a completely unconditioned round trend."""
    cursor.execute("""
        SELECT position, SUM(success_count) as top_two_count, SUM(total_count) as total_count,
            ROUND(SUM(success_count) * 100.0 / NULLIF(SUM(SUM(success_count)) OVER (), 0), 1) as top_two_pct
        FROM draft_trend_stats
        WHERE league_size = %s AND round = %s AND league_type = %s
        GROUP BY position
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
    # FLEX is RB/WR-eligible only (not TE), so its demand splits two ways, not three.
    flex_share = flex / 2

    demand = {
        "QB": (league_settings.get("qb", 1) + sflex * 0.25) * size,
        "RB": (league_settings.get("rb", 2) + flex_share) * size,
        "WR": (league_settings.get("wr", 2) + flex_share) * size,
        "TE": league_settings.get("te", 1) * size,
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
    the bench) taper gently so late-round RB/WR depth still happens.
    QB's requirement extends past its base starter count to cover SFLEX slots too (same
    fix as get_position_fill_status) — otherwise simulated teams in superflex leagues
    treated a 2nd QB as immediate surplus, tapering it hard even though SFLEX makes a
    2nd QB a legitimate starter, not bench depth. That was pushing backup-tier QBs
    (Bryce Young, Tua, etc.) later than their real ADP in simulated superflex drafts."""
    required = league_settings.get(REQUIRED_STARTERS.get(position, ""), 1)
    if position == "QB":
        required += league_settings.get("sflex", 0)
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
    # SFLEX is overwhelmingly filled by a 2nd/3rd QB in practice, so QB's real "full
    # roster" requirement extends past its base starter count to cover SFLEX too —
    # otherwise a team with exactly one QB reads as fully staffed at QB even with an
    # empty superflex slot sitting right there.
    qb_required = league_settings.get("qb", 1) + league_settings.get("sflex", 0)
    needed = []

    if counts.get("QB", 0) < qb_required:
        needed.append(("QB", "urgent" if rounds_left <= 4 else "need"))

    if counts.get("TE", 0) < league_settings.get("te", 1):
        needed.append(("TE", "urgent" if rounds_left <= 3 else "need"))

    if counts.get("RB", 0) < league_settings.get("rb", 2):
        needed.append(("RB", "need"))

    if counts.get("WR", 0) < league_settings.get("wr", 2):
        needed.append(("WR", "need"))

    return needed

SFLEX_SOFT_NEED_BONUS = 6

def get_position_fill_status(my_picks, league_settings, current_round):
    """Per-position roster state used for score bonuses/penalties.
    QB gets a second, softer requirement tier for SFLEX: once the base QB starter slot
    is covered, an unfilled SFLEX slot still counts as a real (if milder) need rather than
    vanishing entirely — a team with one QB and an empty superflex slot was previously
    reading as fully "filled" at QB, which is wrong.
    RB/WR use a much gentler surplus penalty than QB/TE once "filled": bench RB/WR depth
    (bye weeks, injuries) is normal and valuable, a 3rd/4th QB or 2nd/3rd TE almost never
    is — this mirrors position_pick_multiplier's same RB/WR-lenient, QB/TE-strict split
    used for CPU mock-draft picks, which the human-facing score didn't previously match."""
    counts = {}
    for _, pos, _ in my_picks:
        counts[pos] = counts.get(pos, 0) + 1

    total_rounds = league_settings.get("total_rounds", 15)
    rounds_left = total_rounds - current_round
    base_required = {
        "QB": league_settings.get("qb", 1),
        "RB": league_settings.get("rb", 2),
        "WR": league_settings.get("wr", 2),
        "TE": league_settings.get("te", 1),
    }
    extended_required = dict(base_required)
    extended_required["QB"] += league_settings.get("sflex", 0)
    urgent_thresholds = {"QB": 4, "TE": 3}

    status = {}
    for position in base_required:
        count = counts.get(position, 0)
        base_deficit = base_required[position] - count
        extended_deficit = extended_required[position] - count

        if base_deficit > 0:
            urgent = rounds_left <= urgent_thresholds.get(position, 0)
            status[position] = {"state": "urgent" if urgent else "need", "need_bonus": 30 if urgent else 10}
        elif extended_deficit > 0:
            status[position] = {"state": "need", "need_bonus": SFLEX_SOFT_NEED_BONUS}
        else:
            over = count - extended_required[position]
            state = "filled" if over == 0 else "surplus"
            if position in ("QB", "TE"):
                need_bonus = -20 - 15 * over
            else:
                need_bonus = -5 - 5 * over
            status[position] = {"state": state, "need_bonus": need_bonus}
    return status

VALUE_BONUS_PER_PICK = 1.5
ADP_QUALITY_SCALE = 1.0
ADP_QUALITY_HALFLIFE = 15
TREND_WEIGHT = 0.6
POSITIONAL_CLIFF_SCALE = 25
CLIFF_MIN_SAMPLE = 2
# A cliff only matters if you'd actually want more of that position — full urgency when
# you need it, a little residual value once you're exactly at requirement (an extra
# starter-quality body is still mild insurance), essentially none once you're already
# stacked there.
CLIFF_NEED_MULTIPLIER = {
    "urgent": 1.0,
    "need": 1.0,
    "filled": 0.3,
    "surplus": 0.0,
}
RANKED_PLAYERS_POOL_PER_POSITION = 50

def get_next_turn_picks(current_round, draft_slot, league_size, count=2):
    """Global pick numbers of the user's next `count` turns after the current one."""
    return [
        global_pick_number(current_round + i, draft_slot, league_size)
        for i in range(1, count + 1)
    ]

def get_positional_cliff_bonus(players_by_position, current_pick, next_turn_pick, next_next_turn_pick):
    """Detects a positional run/cliff RELATIVE to the other positions, not in isolation.
    Compares how densely available players are packed in the window before the user's
    next turn (tier1) versus the window between the next turn and the one after (tier2),
    normalized by each window's pick-count so a tier2 window twice as wide as tier1
    doesn't get an unfair head start. A position whose density falls off much more than
    the SAFEST other position's (the one that'll still be deepest at your next turn)
    gets the bonus — comparing a position against itself in isolation was the bug: TE's
    ADP is naturally lumpy (small elite tier, then a big gap), so it looked like a cliff
    on almost every query regardless of whether anything unusual was actually happening,
    while a real short-term run on a denser position (e.g. RB drying up while QB/WR stay
    deep) didn't stand out since nothing was comparing it to the alternatives."""
    tier1_width = max(1, next_turn_pick - current_pick)
    tier2_width = max(1, next_next_turn_pick - next_turn_pick)

    densities = {}
    for position, players in players_by_position.items():
        tier1 = sum(1 for _, adp, _ in players if current_pick <= adp < next_turn_pick)
        tier2 = sum(1 for _, adp, _ in players if next_turn_pick <= adp < next_next_turn_pick)
        if tier1 < CLIFF_MIN_SAMPLE:
            densities[position] = None
            continue
        rate1 = tier1 / tier1_width
        rate2 = tier2 / tier2_width
        densities[position] = (rate2 / rate1) if rate1 > 0 else None

    valid = [d for d in densities.values() if d is not None]
    if len(valid) < 2:
        return {position: 0 for position in players_by_position}

    safest = max(valid)

    bonus = {}
    for position, density in densities.items():
        if density is None or safest <= 0:
            bonus[position] = 0
            continue
        relative_dropoff = max(0.0, 1 - (density / safest))
        bonus[position] = POSITIONAL_CLIFF_SCALE * relative_dropoff
    return bonus

def get_ranked_players(cursor, all_picks, my_picks, league_settings, current_round, current_pick,
                        season, position_pct_lookup, rank_lookup, draft_slot, adp_league_type="standard", limit=10):
    """Single blended score per available player, mixing five signals:
      - trend_pct * TREND_WEIGHT: historical top-2-seed rate for this position at this
        point in similar drafts, damped to 0.6x. Full-strength trend_pct swings ~30 points
        between positions (e.g. WR ~49% vs QB ~19%) on its own, which is bigger than most
        individual ADP gaps between two available players — so undamped, position alone
        could out-rank a much better player at a "colder" position (a rank-40 WR out-scoring
        a rank-16 QB). Damping it keeps position trend an input, not the deciding one.
      - need_bonus: roster-fit adjustment (need/urgent add, an already-filled position subtracts)
      - value_bonus: uncapped bonus for how far the player has fallen past their own ADP
      - adp_quality_bonus: baseline player quality from raw ADP, on a curve that flattens
        out rather than a straight line — 100 / (1 + adp / 15). A 19-pick ADP gap near the
        top of the draft (e.g. rank 1.5 vs 21.5) is a real talent cliff and should swing the
        score a lot; the same 19-pick gap in round 7 (e.g. rank 68 vs 87) is mostly noise and
        shouldn't swamp a real roster need — fantasy value genuinely flattens out like this
        in the middle rounds, so a straight-line bonus was overweighting small ADP gaps deep
        into the draft. Without this term at all, every player at a position was tied
        whenever value_bonus was 0 (i.e. anytime nobody there had actually fallen yet).
      - cliff_bonus: rewards a position that's about to dry up before your next turn (see
        get_positional_cliff_bonus) — this is what makes an early RB score higher than an
        equivalent WR when RB depth is about to fall off a cliff and WR depth isn't. Scaled
        by CLIFF_NEED_MULTIPLIER based on need state: a cliff only matters if you'd actually
        want more of that position. Without this, a position you're already stacked at
        (surplus) got the same "grab it now" urgency as a real need, which is backwards —
        if you already have enough RBs, RB depth drying up isn't a reason to draft another.
    value_bonus is uncapped and grows with how far a player has fallen, so a big enough
    fall (e.g. 20+ picks) outweighs a roster-need penalty (like already having a TE)
    without any special-case logic — it just falls out of the math."""
    fill_status = get_position_fill_status(my_picks, league_settings, current_round)
    league_size = league_settings.get("league_size", 12)
    next_turn_pick, next_next_turn_pick = get_next_turn_picks(current_round, draft_slot, league_size)

    players_by_position = {
        position: get_available_players(
            cursor, all_picks, position, season, rank_lookup,
            adp_league_type=adp_league_type, limit=RANKED_PLAYERS_POOL_PER_POSITION,
        )
        for position in ["QB", "RB", "WR", "TE"]
    }
    cliff_bonus_by_position = get_positional_cliff_bonus(
        players_by_position, current_pick, next_turn_pick, next_next_turn_pick
    )

    candidates = []
    for position, players in players_by_position.items():
        trend_pct = position_pct_lookup.get(position, 0)
        need_info = fill_status.get(position, {"state": None, "need_bonus": 0})
        cliff_bonus = cliff_bonus_by_position.get(position, 0) * CLIFF_NEED_MULTIPLIER.get(need_info["state"], 1.0)
        for name, adp, rank in players:
            value = max(0, current_pick - adp)
            value_bonus = value * VALUE_BONUS_PER_PICK
            adp_quality_bonus = (100 / (1 + adp / ADP_QUALITY_HALFLIFE)) * ADP_QUALITY_SCALE
            score = (trend_pct * TREND_WEIGHT) + need_info["need_bonus"] + value_bonus + adp_quality_bonus + cliff_bonus
            candidates.append({
                "name": name,
                "position": position,
                "adp": adp,
                "rank": rank,
                "score": round(score, 1),
                "trend_pct": trend_pct,
                "need": need_info["state"],
                "value": round(value, 1),
                "cliff_bonus": round(cliff_bonus, 1),
            })

    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates[:limit]

def build_recommendation(cursor, draft_slot, league_size, league_type, te_premium,
                          position_sequence, current_round, all_picks, my_picks, season, league_settings, current_pick):
    total_rounds = league_settings.get("total_rounds", 15)
    buckets = compute_weighted_buckets(my_picks, total_rounds)
    similar = find_similar_drafts(
        cursor, draft_slot, league_size, league_type, te_premium,
        current_round, buckets
    )
    adp_league_type = resolve_adp_league_type(cursor, season, league_type)
    rank_lookup = get_adp_rank_lookup(cursor, season, adp_league_type)

    position_pct_lookup = {}
    position_order = []

    if similar:
        trend_source = "similar_drafts"
        sample_size = sum(v["total"] for v in similar.values())
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

    # position_order is already ranked by trend % descending
    TOP_AVAILABLE_LIMIT = 5

    top_available_by_position = []
    for position in position_order:
        if position in urgent_positions:
            need_tag = "urgent"
        elif position in need_positions:
            need_tag = "need"
        else:
            need_tag = None
        players = get_available_players(
            cursor, all_picks, position, season, rank_lookup,
            adp_league_type=adp_league_type, limit=TOP_AVAILABLE_LIMIT,
        )
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
        season, position_pct_lookup, rank_lookup, draft_slot, adp_league_type=adp_league_type,
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
    print("Ranked picks (blended score: trend + roster need + ADP-fall value + positional cliff):")
    for rp in rec["ranked_players"]:
        tag = f" [{rp['need'].upper()}]" if rp["need"] in ("urgent", "need", "surplus") else ""
        cliff_tag = " [SCARCE SOON]" if rp["cliff_bonus"] >= 10 else ""
        print(f"  {rp['score']:5.1f}  {rp['name']} ({rp['position']}) - ADP {rp['rank']}{tag}{cliff_tag}")

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