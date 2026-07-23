from advisor import get_db

def compute_top_pct_optimal():
    """Re-ranks rosters using the already-computed optimal_points_total, this time
    with a success bar that scales with league size (top 20%, min 1) instead of a
    flat top-2 - being 2nd in an 8-team league is a much higher bar than 2nd in a
    14-team league, so a fixed count isn't a fair comparison across league sizes.
    Doesn't touch draft_picks/projections at all since optimal_points_total is
    already stored per roster - this is just a re-rank + re-flag pass."""
    db = get_db()
    cursor = db.cursor()

    cursor.execute("""
        SELECT r.league_id, r.roster_id, r.optimal_points_total, l.league_size
        FROM rosters r JOIN leagues l ON r.league_id = l.league_id
        WHERE r.optimal_points_total IS NOT NULL
        ORDER BY r.league_id
    """)

    by_league = {}
    for league_id, roster_id, total, league_size in cursor.fetchall():
        by_league.setdefault(league_id, {"size": league_size, "rosters": []})
        by_league[league_id]["rosters"].append((roster_id, total))

    print(f"Re-ranking {len(by_league)} leagues...")
    updates = []
    for league_id, data in by_league.items():
        league_size = data["size"] or len(data["rosters"])
        top_n = max(1, round(league_size * 0.2))
        rosters = sorted(data["rosters"], key=lambda x: x[1], reverse=True)
        for i, (roster_id, total) in enumerate(rosters):
            is_top_pct = 1 if i < top_n else 0
            updates.append((is_top_pct, league_id, roster_id))

        if len(updates) >= 20000:
            cursor.executemany("""
                UPDATE rosters SET top_pct_optimal = %s WHERE league_id = %s AND roster_id = %s
            """, updates)
            db.commit()
            updates = []

    if updates:
        cursor.executemany("""
            UPDATE rosters SET top_pct_optimal = %s WHERE league_id = %s AND roster_id = %s
        """, updates)
        db.commit()

    print("Done!")
    cursor.close()
    db.close()

if __name__ == "__main__":
    compute_top_pct_optimal()
