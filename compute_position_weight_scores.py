from advisor import get_db, DB_CONFIG

NEW_COLUMNS = ["qb_weight_score", "rb_weight_score", "wr_weight_score", "te_weight_score"]
COLUMN_BY_POSITION = {"QB": "qb_weight_score", "RB": "rb_weight_score", "WR": "wr_weight_score", "TE": "te_weight_score"}

def ensure_columns(cursor, db):
    for col in NEW_COLUMNS:
        cursor.execute("""
            SELECT COUNT(*) FROM information_schema.columns
            WHERE table_schema = %s AND table_name = 'rosters' AND column_name = %s
        """, (DB_CONFIG["database"], col))
        if cursor.fetchone()[0] == 0:
            cursor.execute(f"ALTER TABLE rosters ADD COLUMN {col} FLOAT NULL")
            db.commit()
            print(f"Added {col} column.")
        else:
            print(f"{col} already exists, skipping.")

def get_incomplete_league_ids(cursor):
    print("Finding leagues not yet computed...")
    cursor.execute("""
        SELECT DISTINCT r.league_id
        FROM rosters r
        WHERE r.qb_weight_score IS NULL
    """)
    ids = [row[0] for row in cursor.fetchall()]
    print(f"  {len(ids)} leagues remaining")
    return ids

def stage_incomplete_leagues(cursor, db, league_ids):
    cursor.execute("DROP TEMPORARY TABLE IF EXISTS incomplete_weight_leagues")
    cursor.execute("CREATE TEMPORARY TABLE incomplete_weight_leagues (league_id VARCHAR(20) PRIMARY KEY)")
    batch = [(lid,) for lid in league_ids]
    for i in range(0, len(batch), 5000):
        cursor.executemany("INSERT IGNORE INTO incomplete_weight_leagues (league_id) VALUES (%s)", batch[i:i + 5000])
    db.commit()

def load_max_rounds(cursor):
    print("Computing max round per league...")
    cursor.execute("""
        SELECT dp.league_id, MAX(dp.round)
        FROM draft_picks dp JOIN incomplete_weight_leagues il ON dp.league_id = il.league_id
        GROUP BY dp.league_id
    """)
    max_rounds = dict(cursor.fetchall())
    print(f"  {len(max_rounds)} leagues' max round loaded")
    return max_rounds

def load_rosters(cursor):
    print("Loading rosters (incomplete only)...")
    cursor.execute("""
        SELECT r.league_id, r.roster_id, r.owner_id
        FROM rosters r JOIN incomplete_weight_leagues il ON r.league_id = il.league_id
        WHERE r.qb_weight_score IS NULL
    """)
    rosters_by_league = {}
    for league_id, roster_id, owner_id in cursor.fetchall():
        rosters_by_league.setdefault(league_id, []).append((roster_id, owner_id))
    print(f"  {sum(len(v) for v in rosters_by_league.values())} rosters remaining")
    return rosters_by_league

def load_picks(cursor):
    print("Loading draft picks (incomplete leagues only)...")
    cursor.execute("""
        SELECT dp.league_id, dp.owner_id, dp.position, dp.round
        FROM draft_picks dp JOIN incomplete_weight_leagues il ON dp.league_id = il.league_id
        WHERE dp.position IN ('QB', 'RB', 'WR', 'TE')
    """)
    picks = {}
    for league_id, owner_id, position, round_num in cursor.fetchall():
        key = (league_id, owner_id)
        picks.setdefault(key, []).append((position, round_num))
    print(f"  {len(picks)} roster pick-lists loaded")
    return picks

def main():
    db = get_db()
    cursor = db.cursor()

    ensure_columns(cursor, db)

    incomplete_ids = get_incomplete_league_ids(cursor)
    if not incomplete_ids:
        print("Nothing to do - all leagues already computed.")
        cursor.close()
        db.close()
        return

    stage_incomplete_leagues(cursor, db, incomplete_ids)

    max_rounds = load_max_rounds(cursor)
    rosters_by_league = load_rosters(cursor)
    picks = load_picks(cursor)

    print("Computing round-weighted position scores...")
    updates = []
    processed = 0

    for league_id, rosters in rosters_by_league.items():
        max_round = max_rounds.get(league_id)
        if not max_round:
            continue

        for roster_id, owner_id in rosters:
            drafted = picks.get((league_id, owner_id), [])
            scores = {"QB": 0.0, "RB": 0.0, "WR": 0.0, "TE": 0.0}
            for position, round_num in drafted:
                # Round 1 of an N-round draft is worth N points, round N is worth 1.
                weight = max(1, max_round - round_num + 1)
                scores[position] += weight
            updates.append((scores["QB"], scores["RB"], scores["WR"], scores["TE"], league_id, roster_id))

        processed += 1
        if processed % 2000 == 0:
            print(f"  {processed}/{len(rosters_by_league)} leagues computed")

        if len(updates) >= 20000:
            cursor.executemany("""
                UPDATE rosters SET qb_weight_score = %s, rb_weight_score = %s,
                                   wr_weight_score = %s, te_weight_score = %s
                WHERE league_id = %s AND roster_id = %s
            """, updates)
            db.commit()
            updates = []

    if updates:
        cursor.executemany("""
            UPDATE rosters SET qb_weight_score = %s, rb_weight_score = %s,
                               wr_weight_score = %s, te_weight_score = %s
            WHERE league_id = %s AND roster_id = %s
        """, updates)
        db.commit()

    print(f"Done! Processed {processed} leagues")
    cursor.close()
    db.close()

if __name__ == "__main__":
    main()
