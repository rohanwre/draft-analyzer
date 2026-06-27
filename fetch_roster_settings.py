import requests
import mysql.connector
import json
import time
from config import DB_CONFIG

def get_db():
    return mysql.connector.connect(**DB_CONFIG)

def get_league_settings(league_id):
    url = f"https://api.sleeper.app/v1/league/{league_id}"
    response = requests.get(url)
    response.raise_for_status()
    return response.json()

def update_league(cursor, league_id, positions, league_size, season_type):
    cursor.execute("""
        UPDATE leagues
        SET roster_positions = %s,
            league_size = %s,
            season_type = %s
        WHERE league_id = %s
    """, (json.dumps(positions), league_size, season_type, league_id))

def fetch_all_settings():
    db = get_db()
    cursor = db.cursor()

    # target all leagues since we need to backfill season_type everywhere
    cursor.execute("SELECT league_id FROM leagues")
    league_ids = [row[0] for row in cursor.fetchall()]

    print(f"Fetching settings for {len(league_ids)} leagues...")

    fixed, errors = 0, 0
    for i, league_id in enumerate(league_ids):
        try:
            data = get_league_settings(league_id)
            positions = data.get("roster_positions", [])
            league_size = data.get("total_rosters")
            season_type = data.get("season_type", None)

            if not positions or league_size is None:
                print(f"  League {league_id}: incomplete data, skipping")
                errors += 1
                continue

            update_league(cursor, league_id, positions, league_size, season_type)
            fixed += 1

            if i % 100 == 0:
                db.commit()
                print(f"  Processed {i}/{len(league_ids)} ({fixed} fixed, {errors} errors)")

            time.sleep(0.05)

        except requests.exceptions.RequestException as e:
            print(f"  Request error on league {league_id}: {e}")
            errors += 1
            continue
        except Exception as e:
            print(f"  Error on league {league_id}: {e}")
            errors += 1
            continue

    db.commit()
    cursor.close()
    db.close()
    print(f"Done! Fixed {fixed}, errored {errors}")

if __name__ == "__main__":
    fetch_all_settings()