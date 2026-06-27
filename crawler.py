import requests
import mysql.connector
import time
from config import DB_CONFIG
from fetch_data import (
    get_db, get_user, get_user_leagues, get_rosters,
    get_drafts, get_draft_picks, insert_league,
    insert_rosters, insert_draft_picks
)

# per league size
LEAGUE_CAP_PER_SIZE  = 2000

# 2025: Current counts — 8-team: 210, 10-team: 694, 12-team: 462

def get_all_owner_ids(cursor):
    cursor.execute("SELECT DISTINCT owner_id FROM rosters WHERE owner_id != 'unknown'")
    return [row[0] for row in cursor.fetchall()]

def get_processed_leagues(cursor):
    cursor.execute("SELECT league_id FROM leagues")
    return set(row[0] for row in cursor.fetchall())


def get_league_counts(cursor):
    cursor.execute("""
        SELECT league_size, COUNT(*) as count
        FROM leagues
        WHERE league_type = 'standard'
        AND season_type = 'regular'
        AND league_size IN (8, 10, 12)
        GROUP BY league_size
    """)
    rows = cursor.fetchall()
    return {size: count for size, count in rows}

def is_size_capped(league_counts, size):
    return league_counts.get(size, 0) >= LEAGUE_CAP_PER_SIZE

def is_valid_league(league):
    """Filter for 8, 10, or 12 team standard PPR redraft leagues only."""
    # PPR only
    scoring = league.get("scoring_settings", {})
    if scoring.get("rec", 0) != 1:
        return False

    # 8, 10, or 12 teams only
    total_rosters = league.get("total_rosters")
    if total_rosters not in [8, 10, 12]:
        return False

    # redraft only
    if league.get("season_type") != "regular":
        return False

    # no superflex
    roster_positions = league.get("roster_positions", [])
    if "SUPER_FLEX" in roster_positions:
        return False

    # no 2QB
    qb_count = sum(1 for p in roster_positions if p == "QB")
    if qb_count >= 2:
        return False

    return True

def process_league(cursor, db, league, processed_leagues, league_counts):
    if league["league_id"] in processed_leagues:
        return False

    if not is_valid_league(league):
        return False

    size = league.get("total_rosters")
    if is_size_capped(league_counts, size):
        return False

    print(f"  Processing league: {league['name']} ({size} teams)")
    insert_league(cursor, league)

    rosters = get_rosters(league["league_id"])
    insert_rosters(cursor, rosters, league["league_id"])

    drafts = get_drafts(league["league_id"])
    for draft in drafts:
        picks = get_draft_picks(draft["draft_id"])
        insert_draft_picks(cursor, picks, league["league_id"])

    db.commit()
    processed_leagues.add(league["league_id"])
    league_counts[size] = league_counts.get(size, 0) + 1
    return True

def all_capped(league_counts):
    return all(league_counts.get(size, 0) >= LEAGUE_CAP_PER_SIZE for size in [8, 10, 12])

def crawl(season):
    db = get_db()
    cursor = db.cursor()

    processed_leagues = get_processed_leagues(cursor)
    owner_ids = get_all_owner_ids(cursor)
    league_counts = get_league_counts(cursor)

    print(f"Starting crawl with {len(owner_ids)} users")
    print(f"Current counts — 8-team: {league_counts.get(8,0)}, 10-team: {league_counts.get(10,0)}, 12-team: {league_counts.get(12,0)}")
    print(f"Target: {LEAGUE_CAP_PER_SIZE} per size")

    new_owner_ids = set()

    # level 1
    for i, owner_id in enumerate(owner_ids):
        if all_capped(league_counts):
            print(f"All sizes capped, stopping")
            break

        print(f"[{i+1}/{len(owner_ids)}] Crawling user {owner_id} — 8:{league_counts.get(8,0)} 10:{league_counts.get(10,0)} 12:{league_counts.get(12,0)}")

        try:
            leagues = get_user_leagues(owner_id, season)
            if not leagues:
                continue

            for league in leagues:
                if all_capped(league_counts):
                    break

                added = process_league(cursor, db, league, processed_leagues, league_counts)

                if added:
                    rosters = get_rosters(league["league_id"])
                    for roster in rosters:
                        oid = roster.get("owner_id")
                        if oid and oid != "unknown" and oid not in owner_ids:
                            new_owner_ids.add(oid)

        except Exception as e:
            print(f"  Error processing user {owner_id}: {e}")
            continue

        time.sleep(0.5)

    # level 2
    print(f"\nLevel 1 done. Found {len(new_owner_ids)} new users. Starting level 2...")

    for i, owner_id in enumerate(new_owner_ids):
        if all_capped(league_counts):
            print(f"All sizes capped, stopping")
            break

        print(f"[{i+1}/{len(new_owner_ids)}] Crawling new user {owner_id} — 8:{league_counts.get(8,0)} 10:{league_counts.get(10,0)} 12:{league_counts.get(12,0)}")

        try:
            leagues = get_user_leagues(owner_id, season)
            if not leagues:
                continue

            for league in leagues:
                if all_capped(league_counts):
                    break

                added = process_league(cursor, db, league, processed_leagues, league_counts)

                if added:
                    rosters = get_rosters(league["league_id"])
                    for roster in rosters:
                        oid = roster.get("owner_id")
                        if oid and oid != "unknown" and oid not in owner_ids and oid not in new_owner_ids:
                            new_owner_ids.add(oid)

        except Exception as e:
            print(f"  Error processing user {owner_id}: {e}")
            continue

        time.sleep(0.5)

    print(f"\nCrawl complete.")
    print(f"Final counts — 8:{league_counts.get(8,0)} 10:{league_counts.get(10,0)} 12:{league_counts.get(12,0)}")

    cursor.close()
    db.close()

if __name__ == "__main__":
    season = input("Enter season year (e.g. 2024): ").strip()
    crawl(season)