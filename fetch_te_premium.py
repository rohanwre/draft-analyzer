# fetch_te_premium.py
import requests
import mysql.connector
import time
from config import DB_CONFIG

def get_db():
    return mysql.connector.connect(**DB_CONFIG)

def get_league_scoring(league_id):
    url = f"https://api.sleeper.app/v1/league/{league_id}"
    response = requests.get(url)
    response.raise_for_status()
    return response.json()

def update_te_premium(cursor, league_id, is_te_premium):
    cursor.execute("""
        UPDATE leagues
        SET te_premium = %s
        WHERE league_id = %s
    """, (is_te_premium, league_id))

def fetch_all_te_premium():
    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT league_id FROM leagues WHERE te_premium IS NULL")
    league_ids = [row[0] for row in cursor.fetchall()]

    print(f"Found {len(league_ids)} leagues missing te_premium")

    fixed, errors = 0, 0
    for i, league_id in enumerate(league_ids):
        try:
            data = get_league_scoring(league_id)
            scoring = data.get("scoring_settings", {})
            te_bonus = scoring.get("bonus_rec_te", 0)
            is_te_premium = 1 if te_bonus and te_bonus > 0 else 0

            update_te_premium(cursor, league_id, is_te_premium)
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
    fetch_all_te_premium()