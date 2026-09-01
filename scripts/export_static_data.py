"""
One-off export of the three DB tables the frontend needs (adp, draft_trend_stats,
round1_trend_stats) to compact static JSON bundled into frontend/src/data/. This is
what lets the frontend run entirely client-side with no backend at runtime - the same
tables advisor.py queries live, just pre-baked.

Re-run this after any pipeline recompute that changes these tables (build_trend_stats.py,
load_adp.py, etc.) and rebuild/redeploy the frontend to pick up the new data.

Enum columns (league_type, bucket, position) are encoded as small ints against a shared
lookup array rather than repeating the strings 200k+ times - cuts the trend_stats.json
size by roughly 3x before gzip.
"""
import argparse
import json
import os
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv

load_dotenv()

OUT_DIR = Path(__file__).parent.parent / "frontend" / "public" / "data"

LEAGUE_TYPES = ["standard", "qb_premium"]
LEAGUE_FORMATS = ["redraft", "keeper", "dynasty"]
SCORING_TYPES = ["ppr", "half_ppr", "standard"]
BUCKETS = ["NONE", "LIGHT", "MODERATE", "HEAVY"]
POSITIONS = ["QB", "RB", "WR", "TE"]

LEAGUE_TYPE_IDX = {v: i for i, v in enumerate(LEAGUE_TYPES)}
LEAGUE_FORMAT_IDX = {v: i for i, v in enumerate(LEAGUE_FORMATS)}
SCORING_TYPE_IDX = {v: i for i, v in enumerate(SCORING_TYPES)}
BUCKET_IDX = {v: i for i, v in enumerate(BUCKETS)}
POSITION_IDX = {v: i for i, v in enumerate(POSITIONS)}


def get_db(source="railway"):
    """source='railway' (default) exports from the production Railway MySQL - the
    normal case, since that's meant to be the canonical copy these tables get pushed to
    (see OPERATIONS.md / draft-advisor-deployment-status memory). source='local' exports
    straight from the local dev DB (config.py's DB_* creds) instead - useful when Railway
    is unreachable, since the local raw tables are the actual source of truth and
    build_trend_stats.py/load_adp.py always run against them first anyway; reconcile
    Railway with a mysqldump push once it's reachable again so future runs stay canonical."""
    if source == "local":
        return mysql.connector.connect(
            host=os.getenv("DB_HOST"),
            port=int(os.getenv("DB_PORT", "3306")),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            database=os.getenv("DB_NAME"),
        )
    return mysql.connector.connect(
        host=os.getenv("RAILWAY_DB_HOST"),
        port=int(os.getenv("RAILWAY_DB_PORT")),
        user=os.getenv("RAILWAY_DB_USER"),
        password=os.getenv("RAILWAY_DB_PASSWORD"),
        database=os.getenv("RAILWAY_DB_NAME"),
    )


def export_adp(cursor):
    cursor.execute("""
        SELECT player_name, position, adp, season, league_type, league_format, scoring_type, tiebreak_adp FROM adp
    """)
    rows = [
        {
            "name": name,
            "position": position,
            "adp": adp,
            "season": season,
            "leagueType": league_type,
            "leagueFormat": league_format,
            "scoringType": scoring_type,
            "tiebreakAdp": float(tiebreak_adp) if tiebreak_adp is not None else None,
        }
        for name, position, adp, season, league_type, league_format, scoring_type, tiebreak_adp in cursor.fetchall()
    ]
    write_json("adp.json", rows)
    print(f"  adp: {len(rows)} rows")


def export_round1_trend_stats(cursor):
    # league_format/scoring_type are exported as their own dimensions now (redraft/keeper/
    # dynasty; ppr/half_ppr/standard) - the frontend's format/scoring selectors filter on
    # them client-side (see engine/advisor.ts), same pattern as leagueTypeIdx/bucket indices below.
    cursor.execute("""
        SELECT draft_slot, league_size, league_type, league_format, scoring_type, te_premium, position, total_count, success_count
        FROM round1_trend_stats
    """)
    rows = [
        [slot, size, LEAGUE_TYPE_IDX[ltype], LEAGUE_FORMAT_IDX[fmt], SCORING_TYPE_IDX[stype], tep, POSITION_IDX[pos], total, success]
        for slot, size, ltype, fmt, stype, tep, pos, total, success in cursor.fetchall()
    ]
    write_json("round1_trend_stats.json", {
        "leagueTypes": LEAGUE_TYPES,
        "leagueFormats": LEAGUE_FORMATS,
        "scoringTypes": SCORING_TYPES,
        "positions": POSITIONS,
        "columns": ["draftSlot", "leagueSize", "leagueTypeIdx", "leagueFormatIdx", "scoringTypeIdx", "tePremium", "positionIdx", "total", "success"],
        "rows": rows,
    })
    print(f"  round1_trend_stats: {len(rows)} rows")


def export_draft_trend_stats(cursor):
    # league_format/scoring_type - see export_round1_trend_stats for why these are dimensions, not filters
    cursor.execute("""
        SELECT league_size, league_type, league_format, scoring_type, te_premium, round, qb_bucket, rb_bucket,
               wr_bucket, te_bucket, position, total_count, success_count
        FROM draft_trend_stats
    """)
    rows = [
        [
            size, LEAGUE_TYPE_IDX[ltype], LEAGUE_FORMAT_IDX[fmt], SCORING_TYPE_IDX[stype], tep, rnd,
            BUCKET_IDX[qb_b], BUCKET_IDX[rb_b], BUCKET_IDX[wr_b], BUCKET_IDX[te_b],
            POSITION_IDX[pos], total, success,
        ]
        for size, ltype, fmt, stype, tep, rnd, qb_b, rb_b, wr_b, te_b, pos, total, success in cursor.fetchall()
    ]
    write_json("draft_trend_stats.json", {
        "leagueTypes": LEAGUE_TYPES,
        "leagueFormats": LEAGUE_FORMATS,
        "scoringTypes": SCORING_TYPES,
        "buckets": BUCKETS,
        "positions": POSITIONS,
        "columns": [
            "leagueSize", "leagueTypeIdx", "leagueFormatIdx", "scoringTypeIdx", "tePremium", "round",
            "qbBucketIdx", "rbBucketIdx", "wrBucketIdx", "teBucketIdx",
            "positionIdx", "total", "success",
        ],
        "rows": rows,
    })
    print(f"  draft_trend_stats: {len(rows)} rows")


def write_json(filename, data):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / filename
    with open(path, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    size_kb = path.stat().st_size / 1024
    print(f"  wrote {path} ({size_kb:.0f} KB)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["railway", "local"], default="railway")
    args = parser.parse_args()

    db = get_db(args.source)
    cursor = db.cursor()
    print(f"Exporting static data for frontend (source: {args.source})...")
    export_adp(cursor)
    export_round1_trend_stats(cursor)
    export_draft_trend_stats(cursor)
    cursor.close()
    db.close()
    print("Done!")


if __name__ == "__main__":
    main()
