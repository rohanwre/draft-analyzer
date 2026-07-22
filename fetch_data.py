# fetch_data.py

import json
import requests
import mysql.connector
from config import DB_CONFIG

# connects to sqbdb
def get_db():
    return mysql.connector.connect(**DB_CONFIG)

# pulls user id from username
def get_user(username):
    url = f"https://api.sleeper.app/v1/user/{username}"
    response = requests.get(url)
    data = response.json()
    if not data:
        return None, None
    return data["user_id"], data["display_name"]

# pulls leagues, roster, draft, individual picks of each user
def get_user_leagues(user_id, season):
    url = f"https://api.sleeper.app/v1/user/{user_id}/leagues/nfl/{season}"
    response = requests.get(url)
    return response.json()

def get_rosters(league_id):
    url = f"https://api.sleeper.app/v1/league/{league_id}/rosters"
    response = requests.get(url)
    return response.json()

def get_drafts(league_id):
    url = f"https://api.sleeper.app/v1/league/{league_id}/drafts"
    response = requests.get(url)
    return response.json()

def get_draft_picks(draft_id):
    url = f"https://api.sleeper.app/v1/draft/{draft_id}/picks"
    response = requests.get(url)
    return response.json()

# derives standard vs qb_premium from roster_positions, same rule advisor.py uses at draft-setup time
def derive_league_type(roster_positions):
    qb_count = sum(1 for p in roster_positions if p == "QB")
    sflex_count = sum(1 for p in roster_positions if p == "SUPER_FLEX")
    if sflex_count > 0 or qb_count >= 2:
        return "qb_premium"
    return "standard"

# adds league to db, skipping if alr added
# roster_positions/scoring_settings/season_type already come back on the user-leagues list call,
# so league_type/te_premium/league_size are captured here instead of needing a separate backfill pass
def insert_league(cursor, league):
    roster_positions = league.get("roster_positions", [])
    scoring = league.get("scoring_settings", {})
    te_bonus = scoring.get("bonus_rec_te", 0)
    te_premium = 1 if te_bonus and te_bonus > 0 else 0
    league_type = derive_league_type(roster_positions)

    cursor.execute("""
        INSERT IGNORE INTO leagues
        (league_id, name, season, scoring_type, total_rosters, status,
         league_size, roster_positions, league_type, te_premium, season_type)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        league["league_id"],
        league["name"],
        league["season"],
        scoring.get("rec", 0),
        league["total_rosters"],
        league["status"],
        league["total_rosters"],
        json.dumps(roster_positions),
        league_type,
        te_premium,
        league.get("season_type")
    ))

# adds individual rosters to each, skipping if alr added
def insert_rosters(cursor, rosters, league_id):
    sorted_rosters = sorted(rosters, key=lambda x: x["settings"].get("wins", 0), reverse=True)
    for i, roster in enumerate(sorted_rosters):
        settings = roster.get("settings", {})
        top_two = i < 2
        cursor.execute("""
            INSERT IGNORE INTO rosters 
            (roster_id, league_id, owner_id, wins, losses, points_for, final_seed, made_playoffs, top_two_seed)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            roster["roster_id"],
            league_id,
            roster.get("owner_id", "unknown"),
            settings.get("wins", 0),
            settings.get("losses", 0),
            settings.get("fpts", 0),
            i + 1,
            settings.get("wins", 0) > settings.get("losses", 0),
            top_two
        ))

# adds invidiual draft picks to each, skipping if alr added
# draft_slot is computed inline (same formula as the old manual backfill SQL) since total_rosters is already known
def insert_draft_picks(cursor, picks, league_id, total_rosters):
    for pick in picks:
        metadata = pick.get("metadata", {})
        pick_no = pick["pick_no"]
        draft_slot = ((pick_no - 1) % total_rosters) + 1
        cursor.execute("""
            INSERT IGNORE INTO draft_picks
            (pick_id, draft_id, league_id, owner_id, round, pick_no, position, player_name, draft_slot)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            f"{pick['draft_id']}_{pick_no}",
            pick["draft_id"],
            league_id,
            pick.get("picked_by", "unknown"),
            pick["round"],
            pick_no,
            metadata.get("position", "UNK"),
            metadata.get("first_name", "") + " " + metadata.get("last_name", ""),
            draft_slot
        ))

# if user is valid, if user is in leagues, if ppr, add roster, draft, and picks
def process_username(username, season):
    db = get_db()
    cursor = db.cursor()

    user_id, display_name = get_user(username)
    if not user_id:
        print(f"User '{username}' not found on Sleeper")
        return

    print(f"Found user: {display_name} (ID: {user_id})")
    leagues = get_user_leagues(user_id, season)

    if not leagues:
        print(f"No leagues found for {display_name} in {season}")
        return

# filter for ppr
    for league in leagues:
        scoring = league.get("scoring_settings", {})
        rec = scoring.get("rec", 0)
        if rec != 1:
            print(f"Skipping non-PPR league: {league['name']} (rec={rec})")
            continue

        print(f"Processing league: {league['name']}")
        insert_league(cursor, league)

        rosters = get_rosters(league["league_id"])
        insert_rosters(cursor, rosters, league["league_id"])

        drafts = get_drafts(league["league_id"])
        for draft in drafts:
            picks = get_draft_picks(draft["draft_id"])
            insert_draft_picks(cursor, picks, league["league_id"], league["total_rosters"])

        db.commit()

    cursor.close()
    db.close()
    print("Done!")

if __name__ == "__main__":
    username = input("Enter Sleeper username: ").strip()
    season = input("Enter season year (e.g. 2024): ").strip()
    process_username(username, season)