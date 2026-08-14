# compute_sustained_success.py
#
# Dynasty-only. A single season's top_pct_optimal doesn't mean much for a dynasty roster
# on its own - dynasty is a multi-year game (you draft for future value, rosters carry
# over via build_lineages.py's lineage_id/season_index chains). This computes, for every
# dynasty roster-season, how well that team has actually sustained success across the
# lineage so far:
#
#   sustained_success_rate = (# seasons so far, including this one, where the roster
#                              finished top 20% of its league by optimal points)
#                             / (# seasons so far with a computed outcome)
#
# Written to every roster-season row in the lineage (not just the latest), so
# build_trend_stats.py can pick whichever season_index it needs - in practice it wants the
# *latest* available value per (lineage_id, owner_id), the fullest history available, as
# the success label for that team's startup-draft picks.
#
# Resumable in the sense that it always recomputes from scratch off current
# top_pct_optimal/lineage data - cheap (dynasty is a small slice of the full dataset), so a
# full rewrite each run is simpler than tracking incremental staleness.

from collections import defaultdict
from advisor import get_db

def load_dynasty_outcomes(cursor):
    cursor.execute("""
        SELECT l.lineage_id, l.season_index, l.league_id, r.roster_id, r.owner_id, r.top_pct_optimal
        FROM rosters r
        JOIN leagues l ON r.league_id = l.league_id
        WHERE l.league_format = 'dynasty' AND l.lineage_id IS NOT NULL AND l.season_index IS NOT NULL
        AND r.top_pct_optimal IS NOT NULL AND r.owner_id IS NOT NULL AND r.owner_id != 'unknown'
        ORDER BY l.lineage_id, r.owner_id, l.season_index
    """)
    return cursor.fetchall()

def main():
    db = get_db()
    cursor = db.cursor()

    print("Loading dynasty roster-season outcomes...")
    rows = load_dynasty_outcomes(cursor)
    print(f"  {len(rows)} outcomes loaded")

    by_team = defaultdict(list)
    for lineage_id, season_index, league_id, roster_id, owner_id, top_pct in rows:
        by_team[(lineage_id, owner_id)].append((season_index, league_id, roster_id, top_pct))

    print(f"Computing sustained success across {len(by_team)} dynasty teams...")
    updates = []
    for entries in by_team.values():
        entries.sort(key=lambda e: e[0])
        successes = 0
        for i, (season_index, league_id, roster_id, top_pct) in enumerate(entries, start=1):
            successes += top_pct
            rate = successes / i
            updates.append((rate, league_id, roster_id))

    print(f"Writing sustained_success_rate for {len(updates)} roster-seasons...")
    for i in range(0, len(updates), 5000):
        cursor.executemany(
            "UPDATE rosters SET sustained_success_rate = %s WHERE league_id = %s AND roster_id = %s",
            updates[i:i + 5000]
        )
    db.commit()

    print("Done!")
    cursor.close()
    db.close()

if __name__ == "__main__":
    main()
