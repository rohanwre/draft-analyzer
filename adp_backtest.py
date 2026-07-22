import mysql.connector
from collections import defaultdict

from config import DB_CONFIG
from advisor import normalize_name

MIN_SAMPLE_SIZE = 15

def get_db():
    return mysql.connector.connect(**DB_CONFIG)

def get_actual_pick_stats(cursor, season, league_type):
    cursor.execute("""
        SELECT dp.player_name, dp.position, dp.pick_no
        FROM draft_picks dp
        JOIN leagues l ON dp.league_id = l.league_id
        WHERE l.season = %s
        AND l.league_type = %s
        AND l.season_type = 'regular'
        AND dp.position IN ('QB', 'RB', 'WR', 'TE')
    """, (season, league_type))

    stats = defaultdict(lambda: {"position": None, "display_name": None, "picks": []})
    for player_name, position, pick_no in cursor.fetchall():
        key = normalize_name(player_name)
        if not key:
            continue
        stats[key]["position"] = position
        stats[key]["display_name"] = player_name
        stats[key]["picks"].append(pick_no)

    return stats

def get_adp_lookup(cursor, season, league_type):
    cursor.execute("""
        SELECT player_name, adp FROM adp
        WHERE season = %s AND league_type = %s
    """, (season, league_type))

    return {normalize_name(name): adp for name, adp in cursor.fetchall() if normalize_name(name)}

def pearson_correlation(xs, ys):
    n = len(xs)
    if n < 2:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x == 0 or var_y == 0:
        return None
    return cov / (var_x ** 0.5 * var_y ** 0.5)

def analyze(season, league_type, min_sample=MIN_SAMPLE_SIZE, top_n=10):
    db = get_db()
    cursor = db.cursor()

    actual = get_actual_pick_stats(cursor, season, league_type)
    adp_lookup = get_adp_lookup(cursor, season, league_type)

    cursor.close()
    db.close()

    rows = []
    for key, data in actual.items():
        picks = data["picks"]
        if len(picks) < min_sample:
            continue
        adp = adp_lookup.get(key)
        if adp is None:
            continue
        avg_pick = sum(picks) / len(picks)
        rows.append({
            "name": data["display_name"],
            "position": data["position"],
            "adp": adp,
            "avg_pick": avg_pick,
            "delta": avg_pick - adp,
            "n": len(picks),
        })

    if not rows:
        print(f"No overlapping players found for season {season} ({league_type}). "
              f"Check that both draft_picks and adp have data for this season/league_type.")
        return

    xs = [r["adp"] for r in rows]
    ys = [r["avg_pick"] for r in rows]
    corr = pearson_correlation(xs, ys)
    mae = sum(abs(r["delta"]) for r in rows) / len(rows)

    print(f"\n{'=' * 60}")
    print(f"ADP vs Actual Picks — Season {season} ({league_type})")
    print(f"{'=' * 60}")
    print(f"Players compared: {len(rows)} (min {min_sample} picks each)")
    if corr is not None:
        print(f"Correlation (ADP vs avg actual pick): {corr:.3f}")
    print(f"Mean absolute error: {mae:.1f} picks")

    rows.sort(key=lambda r: r["delta"])

    print(f"\nBiggest reaches (drafted earlier than ADP suggested):")
    for r in rows[:top_n]:
        print(f"  {r['name']} ({r['position']}) - ADP {r['adp']:.1f}, avg pick {r['avg_pick']:.1f}, "
              f"reached by {abs(r['delta']):.1f} picks (n={r['n']})")

    print(f"\nBiggest values (fell past ADP):")
    for r in reversed(rows[-top_n:]):
        print(f"  {r['name']} ({r['position']}) - ADP {r['adp']:.1f}, avg pick {r['avg_pick']:.1f}, "
              f"fell {r['delta']:.1f} picks (n={r['n']})")

if __name__ == "__main__":
    season = int(input("Season to analyze (e.g. 2024): ").strip())
    league_type = input("League type (standard/qb_premium, default standard): ").strip() or "standard"
    analyze(season, league_type)
