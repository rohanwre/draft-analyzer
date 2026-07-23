import time
import requests

from advisor import get_db

SEASONS = [2020, 2021, 2022, 2023, 2024, 2025]
WEEKS = range(1, 18)
SKILL_POSITIONS = {"QB", "RB", "WR", "TE"}

def fetch_week(season, week):
    url = f"https://api.sleeper.app/projections/nfl/{season}/{week}?season_type=regular"
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    return resp.json()

def insert_week(cursor, season, week, entries):
    inserted = 0
    for entry in entries:
        player = entry.get("player") or {}
        position = player.get("position")
        if position not in SKILL_POSITIONS:
            continue

        player_id = entry.get("player_id")
        first = player.get("first_name") or ""
        last = player.get("last_name") or ""
        name = f"{first} {last}".strip()
        if not player_id or not name:
            continue

        stats = entry.get("stats") or {}
        cursor.execute("""
            INSERT INTO player_weekly_projections
                (player_id, player_name, position, season, week, pts_ppr, pts_half_ppr, pts_std)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                player_name = %s, position = %s,
                pts_ppr = %s, pts_half_ppr = %s, pts_std = %s
        """, (
            player_id, name, position, season, week,
            stats.get("pts_ppr"), stats.get("pts_half_ppr"), stats.get("pts_std"),
            name, position,
            stats.get("pts_ppr"), stats.get("pts_half_ppr"), stats.get("pts_std"),
        ))
        inserted += 1
    return inserted

def fetch_all():
    db = get_db()
    cursor = db.cursor()

    for season in SEASONS:
        for week in WEEKS:
            try:
                entries = fetch_week(season, week)
            except requests.RequestException as e:
                print(f"  season={season} week={week}: FAILED ({e})")
                continue

            inserted = insert_week(cursor, season, week, entries)
            db.commit()
            print(f"  season={season} week={week}: {inserted} skill-position projections")
            time.sleep(0.3)

    cursor.close()
    db.close()
    print("Done!")

if __name__ == "__main__":
    fetch_all()
