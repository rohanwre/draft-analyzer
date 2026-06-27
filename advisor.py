import re
import mysql.connector
from config import DB_CONFIG

MIN_SAMPLE_SIZE = 20

SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


NAME_ALIASES = {
    "hollywood brown": "marquise brown",
}

ADP_WEIGHT = 0.6
TREND_WEIGHT = 0.4

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

def get_player_input(cursor, season, prompt_label="Who was drafted?"):
    while True:
        player_name = input(f"\n{prompt_label} ").strip()

        if not player_name:
            print("  Name can't be blank, try again.")
            continue

        matched_name, position = lookup_position(cursor, player_name, season)

        if position:
            confirm = input(f"  Found: {matched_name} ({position}) — correct? (y/n): ").strip().lower()
            if confirm != "n":
                return matched_name, position
        else:
            print(f"  No match found for '{player_name}' in ADP data.")

        choice = input("  Try a different spelling (s), or enter position manually (m)? ").strip().lower()
        if choice == "m":
            manual_position = input("  Position (QB/RB/WR/TE): ").strip().upper()
            return player_name, manual_position

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
    position_sequence = position_sequence[-5:]
    
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

    for round_num, position in enumerate(position_sequence, 1):
        sequence_query += f"""
            AND EXISTS (
                SELECT 1 FROM draft_picks dp{round_num}
                WHERE dp{round_num}.owner_id = r.owner_id
                AND dp{round_num}.league_id = r.league_id
                AND dp{round_num}.round = {round_num}
                AND dp{round_num}.position = '{position}'
            )
        """

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

def get_scarcity(cursor, all_picks, current_pick_number, season, league_settings):
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
            WHERE season = %s AND position = %s AND adp <= %s
        """, (season, position, current_pick_number))
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

def get_available_players(cursor, all_picks, position, season, limit=5):
    taken_normalized = {normalize_name(name) for _, name in all_picks if name}

    cursor.execute("""
        SELECT player_name, adp FROM adp
        WHERE season = %s AND position = %s
        ORDER BY adp
    """, (season, position))

    available = [
        (name, adp) for name, adp in cursor.fetchall()
        if normalize_name(name) not in taken_normalized
    ]
    return available[:limit]

def get_top_recommended_players(cursor, all_picks, season, position_pct_lookup, needs=None, pool_size=15, limit=5):
    taken_normalized = {normalize_name(n) for _, n in all_picks if n}

    cursor.execute("""
        SELECT player_name, position, adp FROM adp
        WHERE season = %s AND position IN ('QB', 'RB', 'WR', 'TE')
        ORDER BY adp
    """, (season,))

    candidates = [
        (name, position, adp) for name, position, adp in cursor.fetchall()
        if normalize_name(name) not in taken_normalized
    ][:pool_size]

    if not candidates:
        return []

    best_adp = min(adp for _, _, adp in candidates)
    worst_adp = max(adp for _, _, adp in candidates)
    adp_range = (worst_adp - best_adp) or 1

    need_positions = {pos for pos, _ in needs} if needs else set()
    urgent_positions = {pos for pos, urg in needs if urg == "urgent"} if needs else set()

    scored = []
    for name, position, adp in candidates:
        adp_score = 100 * (1 - (adp - best_adp) / adp_range)
        trend_score = position_pct_lookup.get(position, 25.0)
        combined = (ADP_WEIGHT * adp_score) + (TREND_WEIGHT * trend_score)

        if position in urgent_positions:
            combined = min(100, combined + 20)
        elif position in need_positions:
            combined = min(100, combined + 10)

        scored.append({
            "name": name,
            "position": position,
            "adp": adp,
            "score": round(combined, 1)
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:limit]

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

def show_recommendation(cursor, draft_slot, league_size, league_type, te_premium,
                         position_sequence, current_round, all_picks, my_picks, season, league_settings, current_pick):
    similar = find_similar_drafts(
        cursor, draft_slot, league_size, league_type, te_premium,
        position_sequence, current_round, season
    )

    print(f"\n{'='*50}")
    print(f"Round {current_round} Recommendation (Slot {draft_slot})")
    print(f"Your picks so far: {' → '.join(position_sequence) if position_sequence else 'None'}")
    print(f"{'='*50}")

    position_pct_lookup = {}

    if similar:
        print(f"Based on {len(similar)} similar historical drafts:")
        recommendations = calculate_recommendation(similar)
        for rec in recommendations:
            if rec["position"] in ["QB", "RB", "WR", "TE"]:
                bar = "█" * int(rec["top_two_pct"] / 5)
                print(f"  {rec['position']}: {rec['top_two_pct']:5.1f}% of top 2 seeds  {bar}")
                position_pct_lookup[rec["position"]] = rec["top_two_pct"]
    else:
        print(f"General round {current_round} trends (all {league_size}-team, {league_type} leagues):")
        rows = get_general_round_trends(cursor, league_size, league_type, current_round)
        for position, top_two_count, total_count, top_two_pct in rows:
            top_two_pct = float(top_two_pct or 0)
            bar = "█" * int(top_two_pct / 5)
            print(f"  {position}: {top_two_pct:5.1f}% of top 2 seeds  {bar}")
            position_pct_lookup[position] = top_two_pct

    # positional needs
    needs = get_positional_need(my_picks, league_settings, current_round)
    if needs:
        print("\nPositional needs:")
        for position, urgency in needs:
            if urgency == "urgent":
                print(f"  !! {position} — URGENT, running out of rounds")
            else:
                print(f"  -> {position} — not yet filled")

    print()
    print(f"Top recommended picks (ADP + historical trend score):")
    top_players = get_top_recommended_players(cursor, all_picks, season, position_pct_lookup, needs=needs, limit=5)
    for player in top_players:
        print(f"  {player['name']} ({player['position']}) - ADP {player['adp']:.1f}, score {player['score']}%")

    print()
    print(f"Value picks (players available past their ADP):")
    for position in ["RB", "WR", "TE", "QB"]:
        candidates = get_available_players(cursor, all_picks, position, season, limit=50)
        shown = 0
        for name, adp in candidates:
            diff = adp - current_pick
            if diff < -3:
                print(f"  {name} ({position}) - ADP {adp}, still available (value: +{abs(diff):.0f} picks)")
                shown += 1
            if shown >= 2:
                break

    alerts = get_scarcity(cursor, all_picks, current_pick, season, league_settings)
    if alerts:
        print("\nScarcity alerts:")
        for alert in alerts:
            print(f"  {alert['position']} going {alert['ratio']}x faster than ADP suggests "
                  f"({alert['taken']} taken, expected {alert['expected']}, "
                  f"league demand: {alert['demand_pct']}% of starts)")

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

        if current_round % 2 == 1:
            pick_order = range(1, league_size + 1)
        else:
            pick_order = range(league_size, 0, -1)

        for pick_slot in pick_order:
            global_pick = (current_round - 1) * league_size + (pick_slot if current_round % 2 == 1 else league_size - pick_slot + 1)

            if pick_slot == draft_slot:
                position_sequence = [p for _, p, _ in my_picks]
                show_recommendation(
                    cursor, draft_slot, league_size, league_type, te_premium,
                    position_sequence, current_round,
                    all_picks, my_picks, season, league_settings, global_pick
                )

                player_name, position = get_player_input(cursor, season, "Who did you draft?")
                my_picks.append((current_round, position, player_name))
                all_picks.append((position, player_name))
                print(f"✓ {player_name} ({position}) added to your roster")

            else:
                player_name, position = get_player_input(cursor, season, f"  Pick {global_pick} - Slot {pick_slot} (name):")
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