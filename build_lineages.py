# build_lineages.py
#
# Chains dynasty leagues across seasons via previous_league_id (a pure in-DB graph walk,
# no API calls) so compute_optimal_lineups.py can accumulate a roster's full draft history
# (startup draft + every rookie draft since) instead of just one season's picks, and so
# compute_sustained_success.py can measure success across the whole lineage rather than one
# season. Keeper leagues are deliberately left out - the sustained-success/lineage treatment
# is dynasty-only for now (see plan notes); keeper leagues keep the existing single-season
# top_pct_optimal metric untouched.
#
# lineage_id = the league_id of the root (season_index 1, i.e. the startup draft - the
# earliest league in the chain with no crawled predecessor). season_index counts forward
# from there (1, 2, 3, ...). Resumable/idempotent - safe to rerun any time the crawler adds
# new leagues, since it always recomputes from scratch off previous_league_id pointers.

from fetch_data import get_db

def load_all_links(cursor):
    """league_id -> previous_league_id for every league (any format) - the graph itself is
    format-agnostic, we only *write* lineage columns for dynasty rows below."""
    cursor.execute("SELECT league_id, previous_league_id FROM leagues")
    return {league_id: prev for league_id, prev in cursor.fetchall()}

def load_dynasty_league_ids(cursor):
    cursor.execute("SELECT league_id FROM leagues WHERE league_format = 'dynasty'")
    return [row[0] for row in cursor.fetchall()]

def resolve_chain(league_id, links, cache):
    """Returns (root_league_id, season_index) for league_id, memoized. Walks backward
    through previous_league_id until hitting a league with no crawled predecessor."""
    if league_id in cache:
        return cache[league_id]

    chain = []
    node = league_id
    seen = set()
    while node is not None and node not in seen:
        if node in cache:
            root, index = cache[node]
            # extend the cached result backward across the chain we just walked
            for i, n in enumerate(reversed(chain)):
                cache[n] = (root, index + i + 1)
            result = (root, index + len(chain))
            cache[league_id] = result
            return result
        chain.append(node)
        seen.add(node)
        prev = links.get(node)
        if prev is None or prev not in links:
            break  # no crawled predecessor - node is the root
        node = prev

    # chain[-1] is the root (no previous_league_id, or predecessor was never crawled)
    root = chain[-1]
    for i, n in enumerate(reversed(chain)):
        cache[n] = (root, i + 1)
    return cache[league_id]

def main():
    db = get_db()
    cursor = db.cursor()

    print("Loading league graph...")
    links = load_all_links(cursor)
    print(f"  {len(links)} leagues loaded")

    dynasty_ids = load_dynasty_league_ids(cursor)
    print(f"{len(dynasty_ids)} dynasty leagues to chain")

    cache = {}
    updates = []
    for league_id in dynasty_ids:
        root, season_index = resolve_chain(league_id, links, cache)
        updates.append((root, season_index, league_id))

    print("Writing lineage_id/season_index...")
    for i in range(0, len(updates), 5000):
        cursor.executemany(
            "UPDATE leagues SET lineage_id = %s, season_index = %s WHERE league_id = %s",
            updates[i:i + 5000]
        )
    db.commit()

    lineage_count = len(set(root for root, _, _ in updates))
    print(f"Done! {len(updates)} dynasty leagues assigned to {lineage_count} distinct lineages")

    cursor.close()
    db.close()

if __name__ == "__main__":
    main()
