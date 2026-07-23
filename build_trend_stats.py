"""
Builds the small summary tables that replace live nested-loop queries over the raw
draft_picks table (13M rows) with indexed lookups over a compact aggregate (tens of
thousands of rows at most). Source data (draft_picks, rosters, leagues,
player_weekly_projections) is never modified or deleted by this script - it only reads
from them and writes two new small summary tables, so this can be re-run anytime the
bucketing/weighting formula changes.

Two tables:
  - round1_trend_stats: draft_slot + league_size + league_type + te_premium -> which
    round-1 position correlates with success. Draft slot matters a lot in round 1
    (pick 1 vs pick 12 is a completely different situation) and nothing has been
    drafted yet, so there's no roster profile to bucket.
  - draft_trend_stats: league_size + league_type + te_premium + round + a bucketed
    "how much has this team already invested in each position, weighted by how early
    those picks were" profile -> which position drafted next correlates with success.
    The general (no-profile) fallback trend is just this same table with the bucket
    columns ignored in the query, so no third table is needed.

Bucketing: each position's cumulative weighted score (same round-weighting formula as
compute_position_weight_scores.py: round 1 of an N-round draft = N points, round N = 1
point) is bucketed into NONE / LIGHT / MODERATE / HEAVY. Round is already a separate
grouping dimension, so buckets don't need to be normalized by round - "heavy RB
investment by round 4" and "heavy RB investment by round 10" are naturally different
rows already.
"""
import json
from advisor import get_db, normalize_name, bucket_score

SEASON_MIN, SEASON_MAX = 2020, 2025
POSITIONS = ("QB", "RB", "WR", "TE")

def parse_te_premium(val):
    return 1 if val else 0

def create_tables(cursor, db):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS round1_trend_stats (
            draft_slot INT NOT NULL,
            league_size INT NOT NULL,
            league_type VARCHAR(20) NOT NULL,
            te_premium TINYINT NOT NULL,
            position VARCHAR(5) NOT NULL,
            total_count INT NOT NULL,
            success_count INT NOT NULL,
            PRIMARY KEY (draft_slot, league_size, league_type, te_premium, position)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS draft_trend_stats (
            league_size INT NOT NULL,
            league_type VARCHAR(20) NOT NULL,
            te_premium TINYINT NOT NULL,
            round INT NOT NULL,
            qb_bucket VARCHAR(10) NOT NULL,
            rb_bucket VARCHAR(10) NOT NULL,
            wr_bucket VARCHAR(10) NOT NULL,
            te_bucket VARCHAR(10) NOT NULL,
            position VARCHAR(5) NOT NULL,
            total_count INT NOT NULL,
            success_count INT NOT NULL,
            PRIMARY KEY (league_size, league_type, te_premium, round,
                         qb_bucket, rb_bucket, wr_bucket, te_bucket, position)
        )
    """)
    db.commit()
    print("Tables ready (created if they didn't already exist).")

def load_leagues(cursor):
    print("Loading leagues...")
    cursor.execute("""
        SELECT league_id, league_size, league_type, te_premium
        FROM leagues WHERE season BETWEEN %s AND %s AND season_type = 'regular'
    """, (SEASON_MIN, SEASON_MAX))
    leagues = {}
    for league_id, league_size, league_type, te_premium in cursor.fetchall():
        leagues[league_id] = (league_size, league_type, parse_te_premium(te_premium))
    print(f"  {len(leagues)} leagues loaded")
    return leagues

def load_roster_outcomes(cursor):
    print("Loading roster outcomes (top_pct_optimal)...")
    cursor.execute("""
        SELECT league_id, owner_id, top_pct_optimal FROM rosters
        WHERE top_pct_optimal IS NOT NULL
    """)
    outcomes = {}
    for league_id, owner_id, top_pct in cursor.fetchall():
        outcomes[(league_id, owner_id)] = top_pct
    print(f"  {len(outcomes)} roster outcomes loaded")
    return outcomes

def load_max_rounds(cursor):
    print("Computing max round per league...")
    cursor.execute("""
        SELECT league_id, MAX(round) FROM draft_picks GROUP BY league_id
    """)
    max_rounds = dict(cursor.fetchall())
    print(f"  {len(max_rounds)} leagues' max round loaded")
    return max_rounds

def stream_picks(cursor):
    """Yields (league_id, owner_id, draft_slot, [(round, pick_no, position), ...]) per
    roster, ordered so all of one roster's picks arrive together in pick_no order."""
    print("Streaming draft picks (ordered by league/owner/pick_no)...")
    cursor.execute("""
        SELECT league_id, owner_id, draft_slot, round, pick_no, position
        FROM draft_picks
        WHERE position IN ('QB', 'RB', 'WR', 'TE')
        ORDER BY league_id, owner_id, pick_no
    """)
    current_key = None
    current_slot = None
    current_picks = []
    count = 0
    for league_id, owner_id, draft_slot, round_num, pick_no, position in cursor:
        key = (league_id, owner_id)
        if key != current_key:
            if current_key is not None:
                yield current_key[0], current_key[1], current_slot, current_picks
            current_key = key
            current_slot = draft_slot
            current_picks = []
        current_picks.append((round_num, pick_no, position))
        count += 1
        if count % 2000000 == 0:
            print(f"  ...{count} picks streamed so far")
    if current_key is not None:
        yield current_key[0], current_key[1], current_slot, current_picks

def main():
    db = get_db()
    # Buffered=False would stream row-by-row from the server instead of loading the
    # full 10M-row result at once - keeps memory bounded during the replay pass.
    cursor = db.cursor(buffered=False)

    create_tables(cursor, db)
    leagues = load_leagues(cursor)
    outcomes = load_roster_outcomes(cursor)
    max_rounds = load_max_rounds(cursor)

    round1_stats = {}
    trend_stats = {}
    rosters_processed = 0

    for league_id, owner_id, draft_slot, picks in stream_picks(cursor):
        league = leagues.get(league_id)
        if league is None:
            continue
        league_size, league_type, te_premium = league
        top_pct = outcomes.get((league_id, owner_id))
        if top_pct is None:
            continue
        max_round = max_rounds.get(league_id)
        if not max_round:
            continue

        picks.sort(key=lambda p: p[1])  # by pick_no, safe within one roster
        cumulative = {"QB": 0, "RB": 0, "WR": 0, "TE": 0}
        seen_round1 = False

        for round_num, pick_no, position in picks:
            if round_num == 1 and not seen_round1:
                key = (draft_slot, league_size, league_type, te_premium, position)
                entry = round1_stats.setdefault(key, [0, 0])
                entry[0] += 1
                entry[1] += top_pct
                seen_round1 = True
            else:
                buckets = tuple(bucket_score(cumulative[p]) for p in POSITIONS)
                key = (league_size, league_type, te_premium, round_num, *buckets, position)
                entry = trend_stats.setdefault(key, [0, 0])
                entry[0] += 1
                entry[1] += top_pct

            weight = max(1, max_round - round_num + 1)
            cumulative[position] += weight

        rosters_processed += 1
        if rosters_processed % 50000 == 0:
            print(f"  {rosters_processed} rosters replayed "
                  f"({len(trend_stats)} trend rows, {len(round1_stats)} round1 rows so far)")

    print(f"Replay done: {rosters_processed} rosters, "
          f"{len(trend_stats)} distinct trend rows, {len(round1_stats)} distinct round1 rows")

    cursor.close()
    cursor = db.cursor()
    cursor.execute("DELETE FROM round1_trend_stats")
    cursor.execute("DELETE FROM draft_trend_stats")
    db.commit()

    print("Writing round1_trend_stats...")
    batch = [
        (slot, size, ltype, tep, pos, total, success)
        for (slot, size, ltype, tep, pos), (total, success) in round1_stats.items()
    ]
    for i in range(0, len(batch), 5000):
        cursor.executemany("""
            INSERT INTO round1_trend_stats
                (draft_slot, league_size, league_type, te_premium, position, total_count, success_count)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, batch[i:i + 5000])
    db.commit()
    print(f"  {len(batch)} rows written")

    print("Writing draft_trend_stats...")
    batch = [
        (size, ltype, tep, rnd, qb_b, rb_b, wr_b, te_b, pos, total, success)
        for (size, ltype, tep, rnd, qb_b, rb_b, wr_b, te_b, pos), (total, success) in trend_stats.items()
    ]
    for i in range(0, len(batch), 5000):
        cursor.executemany("""
            INSERT INTO draft_trend_stats
                (league_size, league_type, te_premium, round, qb_bucket, rb_bucket,
                 wr_bucket, te_bucket, position, total_count, success_count)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, batch[i:i + 5000])
    db.commit()
    print(f"  {len(batch)} rows written")

    cursor.close()
    db.close()
    print("Done!")

if __name__ == "__main__":
    main()
