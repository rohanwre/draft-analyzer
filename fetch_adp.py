import requests
import mysql.connector
from config import DB_CONFIG

# connect to db
def get_db():
    return mysql.connector.connect(**DB_CONFIG)

# get nfl player data
def get_adp_data():
    url = "https://api.sleeper.app/v1/players/nfl"
    print("Fetching player data from Sleeper (this may take a moment)...")
    response = requests.get(url)
    return response.json()

# adds adp of skill positions to adp table, based on id, name, position, adp. skips if duplicate or no adp
def insert_adp(cursor, players, season):
    inserted = 0
    for player_id, player in players.items():
        position = player.get("position")
        if position not in ["QB", "RB", "WR", "TE"]:
            continue

        adp = player.get("search_rank")
        if not adp:
            continue

        first = player.get("first_name", "")
        last = player.get("last_name", "")
        name = f"{first} {last}".strip()

        cursor.execute("""
            INSERT INTO adp (player_name, position, adp, season, league_type)
            VALUES (%s, %s, %s, %s, 'standard')
            ON DUPLICATE KEY UPDATE adp = %s
        """, (name, position, adp, season, adp))
        inserted += 1

    return inserted

# call, commit, close
def fetch_adp(season):
    db = get_db()
    cursor = db.cursor()

    players = get_adp_data()
    inserted = insert_adp(cursor, players, season)
    db.commit()

    print(f"Inserted {inserted} players with ADP data")

    cursor.close()
    db.close()

if __name__ == "__main__":
    season = input("Enter season year (e.g. 2024): ").strip()
    fetch_adp(season)