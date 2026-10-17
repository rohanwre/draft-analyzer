"""
PySpark port of build_trend_stats.py, for running on Databricks (or any Spark).

build_trend_stats.py streams ~12M draft picks through a single-threaded Python loop and
keeps the running per-position investment in a dict. That's fine at today's scale, but
it's inherently serial and memory-bound on one machine. The same replay is a natural
window-function job: partition picks by roster, order by pick_no, and the cumulative
round-weighted score BEFORE each pick is a running SUM over the strictly-prior rows.
Spark then parallelizes it across rosters.

This is a port, not a redesign - the output has to match build_trend_stats.py exactly
(same buckets, same success labels, same two tables), so every rule below mirrors a
specific line there:

  - max_round per league is taken over ALL of a league's picks (K/DEF/etc. included),
    BEFORE filtering to QB/RB/WR/TE, same as load_max_rounds().
  - A pick's bucket reflects only picks made BEFORE it; its own weight is added after.
  - Only a roster's FIRST round-1 pick goes to round1_trend_stats; any later round-1
    pick (rare, e.g. a traded pick) is treated as a normal pick in draft_trend_stats.
  - Success label: top_pct_optimal for redraft/keeper; for dynasty, the startup-draft
    league's owners are labelled with sustained_success_rate from the LATEST season of
    their lineage, and non-startup dynasty drafts are absent (so never replayed).
  - te_premium is truthy -> 1; scoring_type is Sleeper's raw `rec` string bucketed
    ppr (>=0.75) / half_ppr (>=0.25) / standard, defaulting to ppr when unparseable.

Input is three Parquet directories (draft_picks/, rosters/, leagues/) produced by
export_raw_parquet.py and uploaded to a Unity Catalog volume (or read from Delta
tables with --format delta). Output is two Delta tables, draft_trend_stats and
round1_trend_stats, with the same schema as the MySQL production tables.

Usage (Databricks job "Python script" task, or spark-submit):
    build_trend_stats_spark.py --raw-path /Volumes/main/draft_analyzer/raw \
        --catalog main --schema draft_analyzer [--verify]

Local / Airflow (--output mysql): runs Spark in local mode on this machine's cores and
writes the result straight into the MySQL production tables, replacing the output of
build_trend_stats.py - this is how draft_analyzer_downstream_pipeline runs the step:
    python databricks/build_trend_stats_spark.py --raw-path ./databricks_export --output mysql
"""
import argparse

from pyspark.sql import DataFrame, SparkSession, Window
from pyspark.sql import functions as F

SEASON_MIN, SEASON_MAX = 2020, 2025
POSITIONS = ("QB", "RB", "WR", "TE")


def bucket_score_col(score):
    """Column form of advisor.bucket_score - thresholds must stay identical or the
    historical buckets stop matching live lookups."""
    return (
        F.when(score <= 0, "NONE")
        .when(score <= 10, "LIGHT")
        .when(score <= 25, "MODERATE")
        .otherwise("HEAVY")
    )


def scoring_type_col(col):
    """Column form of build_trend_stats.parse_scoring_type. try_cast so an unparseable
    string becomes NULL (-> 'ppr', like the ValueError branch) instead of raising
    under ANSI mode."""
    rec = col.try_cast("double")
    return (
        F.when(rec.isNull(), "ppr")
        .when(rec >= 0.75, "ppr")
        .when(rec >= 0.25, "half_ppr")
        .otherwise("standard")
    )


def te_premium_col(col):
    val = col.try_cast("double")
    return F.when(val.isNotNull() & (val != 0), 1).otherwise(0)


def build_leagues(leagues: DataFrame, season_min: int, season_max: int) -> DataFrame:
    """load_leagues(): regular-season leagues in range with a known league_format."""
    return (
        leagues.filter(
            F.col("season").between(season_min, season_max)
            & (F.col("season_type") == "regular")
            & F.col("league_format").isNotNull()
        )
        .select(
            "league_id",
            "league_size",
            "league_type",
            te_premium_col(F.col("te_premium")).alias("te_premium"),
            "league_format",
            scoring_type_col(F.col("scoring_type")).alias("scoring_type"),
        )
        .dropDuplicates(["league_id"])
    )


def build_outcomes(rosters: DataFrame, leagues: DataFrame) -> DataFrame:
    """(league_id, owner_id, success_label) - load_roster_outcomes() unioned with
    load_dynasty_outcomes(). The two key sets can't collide: redraft/keeper rows come
    from non-dynasty leagues, dynasty rows are keyed by a dynasty startup league."""
    league_fmt = leagues.select("league_id", "league_format", "lineage_id", "season_index")

    redraft = (
        rosters.select("league_id", "owner_id", "top_pct_optimal")
        .join(league_fmt.select("league_id", "league_format"), "league_id")
        .filter(
            F.col("top_pct_optimal").isNotNull()
            & (F.col("league_format").isNull() | (F.col("league_format") != "dynasty"))
        )
        .select(
            "league_id", "owner_id", F.col("top_pct_optimal").cast("double").alias("success_label")
        )
        .dropDuplicates(["league_id", "owner_id"])
    )

    dynasty_leagues = league_fmt.filter(
        (F.col("league_format") == "dynasty") & F.col("lineage_id").isNotNull()
    ).select("league_id", "lineage_id", "season_index")

    latest_per_owner = Window.partitionBy("lineage_id", "owner_id").orderBy(
        F.col("season_index").desc()
    )
    latest = (
        rosters.select("league_id", "owner_id", "sustained_success_rate")
        .join(dynasty_leagues, "league_id")
        .filter(
            F.col("sustained_success_rate").isNotNull()
            & F.col("owner_id").isNotNull()
            & (F.col("owner_id") != "unknown")
        )
        .withColumn("rn", F.row_number().over(latest_per_owner))
        .filter(F.col("rn") == 1)
    )
    startup = (
        dynasty_leagues.filter(F.col("season_index") == 1)
        .select("lineage_id", F.col("league_id").alias("startup_league_id"))
        .dropDuplicates(["lineage_id"])
    )
    dynasty = latest.join(startup, "lineage_id").select(
        F.col("startup_league_id").alias("league_id"),
        "owner_id",
        F.col("sustained_success_rate").cast("double").alias("success_label"),
    )

    return redraft.unionByName(dynasty)


def build_trend_tables(picks, rosters, leagues, season_min=SEASON_MIN, season_max=SEASON_MAX):
    """Returns (round1_trend_stats, draft_trend_stats) DataFrames, column-for-column
    the same as the MySQL tables build_trend_stats.py writes."""
    lg = build_leagues(leagues, season_min, season_max)
    outcomes = build_outcomes(rosters, leagues)

    # Over ALL positions, before the QB/RB/WR/TE filter - see module docstring.
    max_rounds = (
        picks.groupBy("league_id")
        .agg(F.max("round").alias("max_round"))
        .filter(F.col("max_round").isNotNull() & (F.col("max_round") != 0))
    )

    p = (
        picks.filter(F.col("position").isin(*POSITIONS))
        .select("league_id", "owner_id", "draft_slot", "round", "pick_no", "position")
        .join(lg, "league_id")
        .join(outcomes, ["league_id", "owner_id"])
        .join(max_rounds, "league_id")
    )

    roster = Window.partitionBy("league_id", "owner_id").orderBy("pick_no")
    prior = roster.rowsBetween(Window.unboundedPreceding, -1)
    whole = roster.rowsBetween(Window.unboundedPreceding, Window.unboundedFollowing)

    weight = F.greatest(F.lit(1), F.col("max_round") - F.col("round") + 1)
    p = p.withColumn("weight", weight)
    for pos in POSITIONS:
        cumulative = F.coalesce(
            F.sum(F.when(F.col("position") == pos, F.col("weight")).otherwise(0)).over(prior), F.lit(0)
        )
        p = p.withColumn(f"{pos.lower()}_bucket", bucket_score_col(cumulative))

    prior_round1 = F.coalesce(
        F.sum(F.when(F.col("round") == 1, 1).otherwise(0)).over(prior), F.lit(0)
    )
    p = p.withColumn("roster_slot", F.first("draft_slot").over(whole)).withColumn(
        "is_round1", (F.col("round") == 1) & (prior_round1 == 0)
    )

    round1 = (
        p.filter(F.col("is_round1"))
        .groupBy(
            F.col("roster_slot").alias("draft_slot"),
            "league_size", "league_type", "league_format", "scoring_type", "te_premium", "position",
        )
        .agg(
            F.count(F.lit(1)).cast("int").alias("total_count"),
            F.sum("success_label").alias("success_count"),
        )
    )

    trend = (
        p.filter(~F.col("is_round1"))
        .groupBy(
            "league_size", "league_type", "league_format", "scoring_type", "te_premium", "round",
            "qb_bucket", "rb_bucket", "wr_bucket", "te_bucket", "position",
        )
        .agg(
            F.count(F.lit(1)).cast("int").alias("total_count"),
            F.sum("success_label").alias("success_count"),
        )
    )
    return round1, trend


def read_raw(spark, raw_path, fmt):
    """draft_picks/, rosters/, leagues/ under raw_path as Parquet dirs - or, with
    fmt='delta', raw_path is a 'catalog.schema' holding tables of those names."""
    if fmt == "delta":
        return tuple(spark.table(f"{raw_path}.{name}") for name in ("draft_picks", "rosters", "leagues"))
    base = raw_path.rstrip("/")
    return tuple(spark.read.parquet(f"{base}/{name}") for name in ("draft_picks", "rosters", "leagues"))


def run_verification_queries(spark, fq):
    """Same idea as load_snowflake.py --verify: a couple of non-trivial queries so a
    run doubles as evidence the tables are queryable, not just written."""
    print("\n--- Verification queries ---")
    print("\n[1] Best next position per (league_size, round), redraft + ppr, RANK() per round:")
    spark.sql(f"""
        WITH rates AS (
            SELECT league_size, round, position,
                   SUM(total_count) AS attempts,
                   SUM(success_count) / NULLIF(SUM(total_count), 0) AS success_rate
            FROM {fq('draft_trend_stats')}
            WHERE league_format = 'redraft' AND scoring_type = 'ppr' AND league_type = 'standard'
            GROUP BY league_size, round, position
        )
        SELECT league_size, round, position, attempts,
               ROUND(success_rate * 100, 1) AS success_pct
        FROM (
            SELECT *, RANK() OVER (PARTITION BY league_size, round ORDER BY success_rate DESC) AS rnk
            FROM rates
        )
        WHERE rnk = 1
        ORDER BY league_size, round
        LIMIT 15
    """).show(truncate=False)

    print("[2] Round-1 success rate by position for a 12-team redraft ppr standard league:")
    spark.sql(f"""
        SELECT position, SUM(total_count) AS attempts,
               ROUND(SUM(success_count) / NULLIF(SUM(total_count), 0) * 100, 1) AS success_pct
        FROM {fq('round1_trend_stats')}
        WHERE league_format = 'redraft' AND scoring_type = 'ppr'
              AND league_type = 'standard' AND league_size = 12
        GROUP BY position ORDER BY success_pct DESC
    """).show(truncate=False)


def write_mysql(round1: DataFrame, trend: DataFrame):
    """Replaces the MySQL production tables with the Spark result, using
    build_trend_stats.create_tables so the schema can't drift from the Python version.
    Unlike build_trend_stats.py (which drops the tables before its replay), the drop
    happens only after Spark has finished computing, so the tables are empty for
    seconds rather than the whole run."""
    import os
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    import mysql.connector
    from build_trend_stats import create_tables
    from config import DB_CONFIG

    # Collect first (~500K small rows) - any Spark failure then happens before the
    # production tables are touched.
    round1_rows = [tuple(r) for r in round1.collect()]
    trend_rows = [tuple(r) for r in trend.collect()]

    db = mysql.connector.connect(**DB_CONFIG)
    cursor = db.cursor()
    try:
        create_tables(cursor, db)
        for table, cols, rows in (("round1_trend_stats", round1.columns, round1_rows),
                                  ("draft_trend_stats", trend.columns, trend_rows)):
            sql = (f"INSERT INTO {table} ({', '.join(cols)}) "
                   f"VALUES ({', '.join(['%s'] * len(cols))})")
            for i in range(0, len(rows), 5000):
                cursor.executemany(sql, rows[i:i + 5000])
            db.commit()
            print(f"{table}: {len(rows):,} rows written to MySQL")
    finally:
        cursor.close()
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--raw-path", required=True,
                        help="Dir holding draft_picks/ rosters/ leagues/ Parquet (or 'catalog.schema' with --format delta)")
    parser.add_argument("--format", choices=["parquet", "delta"], default="parquet")
    parser.add_argument("--catalog", default="main")
    parser.add_argument("--schema", default="draft_analyzer")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--output", choices=["delta", "mysql"], default="delta",
                        help="delta: Unity Catalog tables (Databricks). mysql: local production tables.")
    parser.add_argument("--driver-memory", default=None,
                        help="Local mode only, e.g. 3g (on Databricks the cluster sets it)")
    args = parser.parse_args()

    builder = SparkSession.builder.appName("draft_analyzer_build_trend_stats")
    if args.output == "mysql":
        builder = builder.master("local[*]").config("spark.ui.enabled", "false")
        if args.driver_memory:
            builder = builder.config("spark.driver.memory", args.driver_memory)
    spark = builder.getOrCreate()
    picks, rosters, leagues = read_raw(spark, args.raw_path, args.format)
    round1, trend = build_trend_tables(picks, rosters, leagues)

    if args.output == "mysql":
        write_mysql(round1, trend)
        return

    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {args.catalog}.{args.schema}")

    def fq(name):
        return f"{args.catalog}.{args.schema}.{name}"

    # Full overwrite each run - same "always a full cheap rebuild, never incremental"
    # contract as build_trend_stats.py. Overwrite is a Delta transaction, so readers
    # never see a half-written table.
    round1.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable(fq("round1_trend_stats"))
    trend.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable(fq("draft_trend_stats"))
    print(f"round1_trend_stats: {spark.table(fq('round1_trend_stats')).count():,} rows")
    print(f"draft_trend_stats:  {spark.table(fq('draft_trend_stats')).count():,} rows")

    if args.verify:
        run_verification_queries(spark, fq)


if __name__ == "__main__":
    main()
