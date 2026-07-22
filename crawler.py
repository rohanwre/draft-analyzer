import mysql.connector
import time
from config import DB_CONFIG
from fetch_data import (
    get_db, get_user_leagues, get_rosters,
    get_drafts, get_draft_picks, insert_league,
    insert_rosters, insert_draft_picks, derive_league_type
)

TARGET_PER_BUCKET = 2000
TARGET_SEASONS = [2020, 2021, 2022, 2023, 2024, 2025]
# sizes outside this range are still collected (no size filter), just not bucket-capped/targeted —
# this is a bias towards 6-14 team leagues, not a hard restriction
TARGET_SIZES = list(range(6, 15))
LEAGUE_TYPES = ["standard", "qb_premium"]

def get_all_owner_ids(cursor):
    cursor.execute("SELECT DISTINCT owner_id FROM rosters WHERE owner_id != 'unknown'")
    return [row[0] for row in cursor.fetchall()]

def get_processed_leagues(cursor):
    cursor.execute("SELECT league_id FROM leagues")
    return set(row[0] for row in cursor.fetchall())

def get_bucket_counts(cursor):
    cursor.execute("""
        SELECT season, league_size, league_type, COUNT(*)
        FROM leagues
        WHERE season_type = 'regular'
        AND league_size BETWEEN 6 AND 14
        AND season BETWEEN 2020 AND 2025
        AND league_type IS NOT NULL
        GROUP BY season, league_size, league_type
    """)
    return {(season, size, ltype): count for season, size, ltype, count in cursor.fetchall()}

def is_bucket_capped(counts, season, size, league_type):
    return counts.get((season, size, league_type), 0) >= TARGET_PER_BUCKET

def all_buckets_capped(counts, exhausted):
    return all(
        is_bucket_capped(counts, season, size, ltype) or (season, size, ltype) in exhausted
        for season in TARGET_SEASONS
        for size in TARGET_SIZES
        for ltype in LEAGUE_TYPES
    )

def mark_exhausted_buckets(counts, counts_before, exhausted):
    """A bucket that gets zero new leagues across a full crawl level is treated as
    tapped out (the real-world population for that season/size/type is likely smaller
    than TARGET_PER_BUCKET) so it stops blocking overall completion."""
    newly_exhausted = set()
    for season in TARGET_SEASONS:
        for size in TARGET_SIZES:
            for ltype in LEAGUE_TYPES:
                key = (season, size, ltype)
                if key in exhausted or is_bucket_capped(counts, season, size, ltype):
                    continue
                if counts.get(key, 0) <= counts_before.get(key, 0):
                    exhausted.add(key)
                    newly_exhausted.add(key)
    return newly_exhausted

def print_bucket_coverage(counts, exhausted):
    for season in TARGET_SEASONS:
        for size in TARGET_SIZES:
            for ltype in LEAGUE_TYPES:
                c = counts.get((season, size, ltype), 0)
                key = (season, size, ltype)
                status = "exhausted" if key in exhausted else ""
                print(f"  {season} {size}-team {ltype}: {c}/{TARGET_PER_BUCKET} {status}")

def load_seed_users():
    try:
        with open("seed_users.txt", "r") as f:
            return [line.strip() for line in f if line.strip() and line.strip() != "NULL"]
    except FileNotFoundError:
        return []

def is_valid_league(league):
    scoring = league.get("scoring_settings", {})

    if league.get("season_type") != "regular":
        return False

    if float(scoring.get("rec", 0)) != 1.0:
        return False

    return True

def process_league(cursor, db, league, processed_leagues, counts):
    if league["league_id"] in processed_leagues:
        return False

    if not is_valid_league(league):
        return False

    season = int(league["season"])
    size = league["total_rosters"]
    league_type = derive_league_type(league.get("roster_positions", []))
    in_target_range = size in TARGET_SIZES

    if in_target_range and is_bucket_capped(counts, season, size, league_type):
        return False

    if in_target_range:
        bucket_count = counts.get((season, size, league_type), 0) + 1
        print(f"  Adding: {league['name']} ({size}-team, {season}, {league_type}) — "
              f"bucket now {bucket_count}/{TARGET_PER_BUCKET}")
    else:
        print(f"  Adding: {league['name']} ({size}-team, {season}, {league_type}) — outside 6-14 bias, uncapped")

    insert_league(cursor, league)

    rosters = get_rosters(league["league_id"])
    insert_rosters(cursor, rosters, league["league_id"])

    drafts = get_drafts(league["league_id"])
    for draft in drafts:
        picks = get_draft_picks(draft["draft_id"])
        insert_draft_picks(cursor, picks, league["league_id"], size)

    db.commit()
    processed_leagues.add(league["league_id"])
    if in_target_range:
        counts[(season, size, league_type)] = bucket_count
    return True

def crawl_owners(cursor, db, owner_ids, processed_leagues, counts, exhausted, seen_owners, label):
    new_owner_ids = set()

    for i, owner_id in enumerate(owner_ids):
        if all_buckets_capped(counts, exhausted):
            print("All buckets capped or exhausted, stopping")
            return new_owner_ids, True

        print(f"[{label} {i + 1}/{len(owner_ids)}] Crawling user {owner_id}")

        for season in TARGET_SEASONS:
            try:
                leagues = get_user_leagues(owner_id, season)
                if not leagues:
                    continue

                for league in leagues:
                    if all_buckets_capped(counts, exhausted):
                        break

                    added = process_league(cursor, db, league, processed_leagues, counts)

                    if added:
                        rosters = get_rosters(league["league_id"])
                        for roster in rosters:
                            oid = roster.get("owner_id")
                            if oid and oid != "unknown" and oid not in seen_owners:
                                new_owner_ids.add(oid)
                                seen_owners.add(oid)

            except Exception as e:
                print(f"  Error on user {owner_id} season {season}: {e}")
                continue

        time.sleep(0.5)

    return new_owner_ids, False

def crawl():
    db = get_db()
    cursor = db.cursor()

    processed_leagues = get_processed_leagues(cursor)
    counts = get_bucket_counts(cursor)
    exhausted = set()

    seed_ids = load_seed_users()
    known_owner_ids = get_all_owner_ids(cursor)
    seen_owners = set(seed_ids) | set(known_owner_ids)
    to_crawl = seed_ids + [oid for oid in known_owner_ids if oid not in set(seed_ids)]

    print(f"Loaded {len(seed_ids)} seed users, {len(known_owner_ids)} known users")
    print("Starting bucket coverage:")
    print_bucket_coverage(counts, exhausted)

    level = 1
    while to_crawl:
        if all_buckets_capped(counts, exhausted):
            print("All buckets capped or exhausted, stopping")
            break

        print(f"\n=== Level {level}: crawling {len(to_crawl)} users ===")
        counts_before = dict(counts)
        new_owner_ids, capped = crawl_owners(cursor, db, to_crawl, processed_leagues, counts, exhausted, seen_owners, f"L{level}")

        if capped:
            break

        newly_exhausted = mark_exhausted_buckets(counts, counts_before, exhausted)
        if newly_exhausted:
            print(f"Marking {len(newly_exhausted)} bucket(s) exhausted (no growth this level):")
            for season, size, ltype in sorted(newly_exhausted):
                c = counts.get((season, size, ltype), 0)
                print(f"  {season} {size}-team {ltype}: stuck at {c}/{TARGET_PER_BUCKET}")

        print(f"Level {level} done. Found {len(new_owner_ids)} new users.")
        to_crawl = list(new_owner_ids)
        level += 1

    print(f"\nCrawl complete after {level} level(s). Final bucket coverage:")
    print_bucket_coverage(counts, exhausted)

    cursor.close()
    db.close()

if __name__ == "__main__":
    crawl()
