# backfill_league_format.py
#
# One-time pass over leagues crawled before league_format/previous_league_id existed.
# For every leagues row with league_format IS NULL, fetches the single-league Sleeper
# endpoint and fills in league_format (redraft/keeper/dynasty, from settings.type) and
# previous_league_id. Resumable - only ever selects rows still missing league_format, so
# it can be killed and rerun safely. New leagues inserted by crawler.py after this point
# already set both columns at insert time and never show up here.
#
# Fetches run on a thread pool (I/O-bound HTTP calls, GIL isn't a bottleneck) since a
# sequential pass over ~86k leagues at Sleeper's per-request latency would take most of a
# day; DB writes are still batched and committed from the main thread only.

import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from fetch_data import get_db, derive_league_format

BATCH_SIZE = 500
WORKERS = 20

def get_pending_league_ids(cursor):
    cursor.execute("SELECT league_id FROM leagues WHERE league_format IS NULL")
    return [row[0] for row in cursor.fetchall()]

def fetch_league(league_id):
    url = f"https://api.sleeper.app/v1/league/{league_id}"
    response = requests.get(url, timeout=15)
    if response.status_code != 200:
        return None
    return response.json()

def fetch_one(league_id):
    """Runs on a worker thread - returns (league_id, update_row_or_None, error_or_None)."""
    try:
        league = fetch_league(league_id)
        if league is None:
            return league_id, None, "non-200 response"
        settings = league.get("settings", {}) or {}
        league_format = derive_league_format(settings)
        previous_league_id = league.get("previous_league_id")
        return league_id, (league_format, previous_league_id, league_id), None
    except Exception as e:
        return league_id, None, str(e)

def main():
    db = get_db()
    cursor = db.cursor()

    pending = get_pending_league_ids(cursor)
    print(f"{len(pending)} leagues need league_format backfilled ({WORKERS} workers)")

    updates = []
    processed = 0
    errors = 0
    start = time.time()

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(fetch_one, lid) for lid in pending]
        for future in as_completed(futures):
            league_id, update_row, error = future.result()
            if error:
                errors += 1
            else:
                updates.append(update_row)

            processed += 1
            if len(updates) >= BATCH_SIZE:
                cursor.executemany(
                    "UPDATE leagues SET league_format = %s, previous_league_id = %s WHERE league_id = %s",
                    updates
                )
                db.commit()
                updates = []

            if processed % 5000 == 0:
                elapsed = time.time() - start
                rate = processed / elapsed if elapsed else 0
                print(f"  {processed}/{len(pending)} backfilled ({errors} errors, "
                      f"{elapsed:.0f}s elapsed, {rate:.1f}/s)")

    if updates:
        cursor.executemany(
            "UPDATE leagues SET league_format = %s, previous_league_id = %s WHERE league_id = %s",
            updates
        )
        db.commit()

    elapsed = time.time() - start
    print(f"Done! Backfilled {processed} leagues ({errors} errors) in {elapsed:.0f}s")

    cursor.close()
    db.close()

if __name__ == "__main__":
    main()
