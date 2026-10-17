"""
Ships the two small production summary tables (draft_trend_stats, round1_trend_stats)
plus adp into a Snowflake warehouse, alongside the existing MySQL production copy -
same "ship only the tiny aggregate, never the raw picks" split documented in
build_trend_stats.py, just to a second target.

This does NOT touch draft_picks/rosters/leagues/player_weekly_projections (the
~8.9GB local-only raw tables) - only the already-tiny aggregates, same as
scripts/export_static_data.py's JSON export does for the frontend.

Safe to re-run any time build_trend_stats.py/load_adp.py change their source tables:
each run truncates and reloads the three target tables from scratch. At ~226K rows
that's cheap - full reload keeps this script simple and avoids a MERGE/upsert story
for what's already a rebuildable-from-MySQL derived copy, not a source of truth.

After loading, (re)creates the reporting views in sql/snowflake_reporting_views.sql
(RPT_POSITION_SUCCESS_BY_ROUND, RPT_ROUND1_BY_DRAFT_SLOT, RPT_ROSTER_BUILD_OUTCOMES,
RPT_ADP_VS_HISTORY) - the self-serve layer for answering trend questions in SQL.

Usage:
    python load_snowflake.py              # load all three tables + recreate views
    python load_snowflake.py --verify     # ...then run analytical sanity queries
    python load_snowflake.py --views-only # just recreate the views (no reload)
"""
import os
import sys

import mysql.connector
import snowflake.connector

from config import DB_CONFIG
from snowflake_config import SNOWFLAKE_CONFIG

# (mysql_table, snowflake_table, columns, create_ddl) - DDL translated from
# build_trend_stats.py's MySQL schema to Snowflake types (TINYINT -> NUMBER(1), no
# PRIMARY KEY enforcement needed since Snowflake doesn't enforce PK/FK constraints and
# this is a full-reload table anyway). `columns` is explicit (rather than SELECT *) so
# MySQL-only columns like adp's auto-increment `id` surrogate key don't leak through.
TABLES = [
    (
        "round1_trend_stats",
        "ROUND1_TREND_STATS",
        ["draft_slot", "league_size", "league_type", "league_format", "scoring_type",
         "te_premium", "position", "total_count", "success_count"],
        """
        CREATE TABLE IF NOT EXISTS ROUND1_TREND_STATS (
            draft_slot NUMBER NOT NULL,
            league_size NUMBER NOT NULL,
            league_type VARCHAR(20) NOT NULL,
            league_format VARCHAR(10) NOT NULL,
            scoring_type VARCHAR(10) NOT NULL,
            te_premium NUMBER(1) NOT NULL,
            position VARCHAR(5) NOT NULL,
            total_count NUMBER NOT NULL,
            success_count FLOAT NOT NULL
        )
        """,
    ),
    (
        "draft_trend_stats",
        "DRAFT_TREND_STATS",
        ["league_size", "league_type", "league_format", "scoring_type", "te_premium", "round",
         "qb_bucket", "rb_bucket", "wr_bucket", "te_bucket", "position",
         "total_count", "success_count"],
        """
        CREATE TABLE IF NOT EXISTS DRAFT_TREND_STATS (
            league_size NUMBER NOT NULL,
            league_type VARCHAR(20) NOT NULL,
            league_format VARCHAR(10) NOT NULL,
            scoring_type VARCHAR(10) NOT NULL,
            te_premium NUMBER(1) NOT NULL,
            round NUMBER NOT NULL,
            qb_bucket VARCHAR(10) NOT NULL,
            rb_bucket VARCHAR(10) NOT NULL,
            wr_bucket VARCHAR(10) NOT NULL,
            te_bucket VARCHAR(10) NOT NULL,
            position VARCHAR(5) NOT NULL,
            total_count NUMBER NOT NULL,
            success_count FLOAT NOT NULL
        )
        """,
    ),
    (
        "adp",
        "ADP",
        ["player_name", "position", "adp", "season", "league_type",
         "league_format", "scoring_type", "tiebreak_adp"],
        """
        CREATE TABLE IF NOT EXISTS ADP (
            player_name VARCHAR(100) NOT NULL,
            position VARCHAR(5) NOT NULL,
            adp FLOAT,
            season NUMBER NOT NULL,
            league_type VARCHAR(20) NOT NULL,
            league_format VARCHAR(10) NOT NULL,
            scoring_type VARCHAR(10) NOT NULL,
            tiebreak_adp FLOAT
        )
        """,
    ),
]


def get_mysql():
    return mysql.connector.connect(**DB_CONFIG)


def get_snowflake():
    return snowflake.connector.connect(**SNOWFLAKE_CONFIG)


def load_table(mysql_cursor, sf_cursor, mysql_table, sf_table, columns, create_ddl):
    print(f"Loading {mysql_table} -> {sf_table}...")
    sf_cursor.execute(create_ddl)

    mysql_cursor.execute(f"SELECT {', '.join(columns)} FROM {mysql_table}")
    rows = mysql_cursor.fetchall()
    print(f"  Pulled {len(rows):,} rows from MySQL.")

    sf_cursor.execute(f"TRUNCATE TABLE {sf_table}")

    if not rows:
        print("  Nothing to load.")
        return

    placeholders = ", ".join(["%s"] * len(columns))
    insert_sql = f"INSERT INTO {sf_table} ({', '.join(columns)}) VALUES ({placeholders})"

    batch_size = 10000
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        sf_cursor.executemany(insert_sql, batch)
        print(f"  Inserted {min(i + batch_size, len(rows)):,}/{len(rows):,}")

    print(f"  Done: {sf_table} now has {len(rows):,} rows.")


VIEWS_SQL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sql", "snowflake_reporting_views.sql")


def create_reporting_views(sf_conn):
    print("Creating reporting views...")
    with open(VIEWS_SQL, encoding="utf-8") as f:
        sql = f.read()
    for cursor in sf_conn.execute_string(sql, remove_comments=True):
        print(f"  {cursor.fetchone()[0]}")


def run_verification_queries(sf_cursor):
    """
    A few genuinely non-trivial queries against the warehouse - window functions and a
    CTE - not just a row-count smoke test. Prints results so a run of this script
    doubles as evidence the warehouse is actually queryable, not just loadable.
    """
    print("\n--- Verification queries ---")

    print("\n[1] Best position per (league_size, league_type, round), redraft only,")
    print("    ranked by success rate within each round via RANK() OVER (PARTITION BY ...):")
    sf_cursor.execute("""
        WITH round_position_rates AS (
            SELECT
                league_size,
                league_type,
                round,
                position,
                SUM(success_count) AS successes,
                SUM(total_count) AS attempts,
                SUM(success_count) / NULLIF(SUM(total_count), 0) AS success_rate
            FROM DRAFT_TREND_STATS
            WHERE league_format = 'redraft' AND scoring_type = 'ppr'
            GROUP BY league_size, league_type, round, position
        )
        SELECT
            league_size,
            league_type,
            round,
            position,
            attempts,
            ROUND(success_rate * 100, 1) AS success_pct,
            RANK() OVER (
                PARTITION BY league_size, league_type, round
                ORDER BY success_rate DESC
            ) AS rank_in_round
        FROM round_position_rates
        QUALIFY rank_in_round = 1
        ORDER BY league_size, league_type, round
        LIMIT 15
    """)
    for row in sf_cursor.fetchall():
        print(f"   {row}")

    print("\n[2] Round-over-round success-rate delta for RB (redraft, 12-team standard),")
    print("    via LAG() OVER (ORDER BY round):")
    sf_cursor.execute("""
        SELECT
            round,
            ROUND(SUM(success_count) / NULLIF(SUM(total_count), 0) * 100, 1) AS success_pct,
            ROUND(
                SUM(success_count) / NULLIF(SUM(total_count), 0) * 100
                - LAG(SUM(success_count) / NULLIF(SUM(total_count), 0) * 100)
                    OVER (ORDER BY round),
                1
            ) AS delta_vs_prior_round
        FROM DRAFT_TREND_STATS
        WHERE league_format = 'redraft' AND scoring_type = 'ppr' AND league_size = 12
            AND league_type = 'standard' AND position = 'RB'
        GROUP BY round
        ORDER BY round
        LIMIT 20
    """)
    for row in sf_cursor.fetchall():
        print(f"   {row}")

    print("\n[3] Join ADP against round1_trend_stats: players whose ADP round doesn't")
    print("    match their position's strongest round-1 draft slot range (join + CTE):")
    sf_cursor.execute("""
        WITH best_r1_position AS (
            SELECT
                league_size,
                league_type,
                draft_slot,
                position,
                success_count / NULLIF(total_count, 0) AS success_rate,
                RANK() OVER (
                    PARTITION BY league_size, league_type, draft_slot
                    ORDER BY success_count / NULLIF(total_count, 0) DESC
                ) AS rnk
            FROM ROUND1_TREND_STATS
            WHERE league_format = 'redraft' AND scoring_type = 'ppr'
        )
        SELECT
            a.player_name,
            a.position,
            a.adp,
            b.league_size,
            b.draft_slot,
            ROUND(b.success_rate * 100, 1) AS best_position_success_pct
        FROM ADP a
        JOIN best_r1_position b
            ON a.position = b.position AND b.rnk = 1
        WHERE a.adp <= 12 AND a.league_type = 'standard' AND a.league_format = 'redraft'
        AND a.scoring_type = 'ppr'
        ORDER BY a.adp
        LIMIT 15
    """)
    for row in sf_cursor.fetchall():
        print(f"   {row}")

    print("\n[4] Reporting view: top roster builds for a round-6 pick, 12-team PPR redraft")
    print("    (RPT_ROSTER_BUILD_OUTCOMES, picks >= 500):")
    sf_cursor.execute("""
        SELECT rb_invested_before, wr_invested_before, position_taken, picks, success_pct, lift_pct_pts
        FROM RPT_ROSTER_BUILD_OUTCOMES
        WHERE league_format = 'redraft' AND scoring_type = 'ppr' AND league_type = 'standard'
          AND league_size = 12 AND round = 6 AND picks >= 500
        ORDER BY lift_pct_pts DESC
        LIMIT 8
    """)
    for row in sf_cursor.fetchall():
        print(f"   {row}")

    print("\n[5] Reporting view: current ADP vs history, biggest historical reaches")
    print("    (RPT_ADP_VS_HISTORY, 12-team PPR redraft, rounds 1-10):")
    sf_cursor.execute("""
        SELECT player_name, position, adp, adp_round, historical_picks, historical_success_pct,
               historical_lift_pct_pts
        FROM RPT_ADP_VS_HISTORY
        WHERE league_format = 'redraft' AND scoring_type = 'ppr' AND league_type = 'standard'
          AND adp_round <= 10 AND historical_picks >= 500
        ORDER BY historical_lift_pct_pts ASC, adp
        LIMIT 8
    """)
    for row in sf_cursor.fetchall():
        print(f"   {row}")


def main():
    verify = "--verify" in sys.argv
    if "--views-only" in sys.argv:
        sf_conn = get_snowflake()
        try:
            create_reporting_views(sf_conn)
        finally:
            sf_conn.close()
        return

    mysql_conn = get_mysql()
    mysql_cursor = mysql_conn.cursor()
    sf_conn = get_snowflake()
    sf_cursor = sf_conn.cursor()

    try:
        for mysql_table, sf_table, columns, ddl in TABLES:
            load_table(mysql_cursor, sf_cursor, mysql_table, sf_table, columns, ddl)
        sf_conn.commit()
        create_reporting_views(sf_conn)

        if verify:
            run_verification_queries(sf_cursor)

    finally:
        mysql_cursor.close()
        mysql_conn.close()
        sf_cursor.close()
        sf_conn.close()

    print("\nDone.")


if __name__ == "__main__":
    main()
