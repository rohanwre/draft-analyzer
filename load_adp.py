# load_adp.py

import csv
import mysql.connector
from config import DB_CONFIG

def get_db():
    return mysql.connector.connect(**DB_CONFIG)

def clean_position(pos):
    # strip the number from position (e.g. RB1 -> RB, WR12 -> WR)
    if not pos:
        return None
    for p in ["QB", "RB", "WR", "TE", "K", "DST"]:
        if pos.startswith(p):
            return p
    return None

def load_adp_csv(cursor, filepath, season):
    with open(filepath, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        inserted = 0
        skipped = 0
        for row in reader:
            try:
                name = row.get("Player", "").strip()
                pos = clean_position(row.get("POS", "").strip())
                adp = row.get("AVG", "").strip()

                if not name or not pos or not adp:
                    skipped += 1
                    continue

                # skip DST and K for now
                if pos in ["DST", "K"]:
                    skipped += 1
                    continue

                adp = float(adp)

                cursor.execute("""
                    INSERT INTO adp (player_name, position, adp, season)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE adp = %s
                """, (name, pos, adp, season, adp))
                inserted += 1

            except Exception as e:
                skipped += 1
                continue

        print(f"Season {season}: inserted {inserted}, skipped {skipped}")

def load_all():
    db = get_db()
    cursor = db.cursor()
    # files, add when applicable 
    files = [
        ("fp_adp_2023.csv", 2023),
        ("fp_adp_2024.csv", 2024),
        ("fp_adp_2025.csv", 2025),
        ("fp_adp_2026.csv", 2026)

    ]

    for filepath, season in files:
        try:
            print(f"Loading {filepath}...")
            load_adp_csv(cursor, filepath, season)
            db.commit()
        except FileNotFoundError:
            print(f"File not found: {filepath}, skipping")
            continue

    cursor.close()
    db.close()
    print("Done!")

if __name__ == "__main__":
    load_all()