"""
Orchestrates the resumable/idempotent downstream half of the pipeline: everything
after the crawl. This is exactly the "rerun the downstream pipeline after any
meaningful crawl progress" step already documented as a manual operational step for
this project - now automated instead of run by hand: it runs whenever the crawl DAG
updates the RAW_LEAGUE_DATA dataset (i.e. new crawl data landed), not on a clock.

Deliberately does NOT include crawler.py - that's a separate, multi-day, manually
triggered job (see draft_analyzer_crawl_dag.py). Running a multi-day crawl on this
DAG's schedule would either time out the DAG run or force an unreasonably long
schedule interval for what is otherwise a cheap, few-minutes downstream rebuild. The
two are linked by a Dataset instead: crawl finishes -> this DAG starts. It can still be
triggered by hand (e.g. after a weighting/bucketing change with no new crawl data).

Dependency order matches the pipeline order documented in project memory:
    build_lineages -> compute_optimal_lineups -> compute_top_pct_optimal
        -> compute_sustained_success -> build_trend_stats
        -> export_static_data -> load_snowflake

compute_position_weight_scores runs in parallel off compute_top_pct_optimal - it's an
exploratory column build_trend_stats.py doesn't actually consume (it recomputes
cumulative-at-time-of-pick itself), so it's non-blocking rather than sitting on the
critical path.

Every task in the required chain is safe to retry: build_lineages/compute_optimal_lineups
skip already-processed leagues/rosters, build_trend_stats does a full cheap replay each
time, and load_snowflake truncates+reloads its (already tiny, ~500K-row) target tables.

Alerting (see draft_analyzer_alerting.py): every task alerts on failure and on retry;
each also has an expected duration - finishing slower than that sends a slow-stage
alert, and running 4x past it hits execution_timeout and fails (which alerts).
build_trend_stats' expectation is based on a measured full run: 6.1 min over 16.2M
picks (2026-09-27).
"""
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator

from draft_analyzer_alerting import DEFAULT_ALERT_ARGS, stage_args
from draft_analyzer_datasets import RAW_LEAGUE_DATA, TREND_STATS_MYSQL, TREND_STATS_SNOWFLAKE

PROJECT_DIR = "/opt/project"

default_args = {
    "owner": "rohan",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    **DEFAULT_ALERT_ARGS,
}

with DAG(
    dag_id="draft_analyzer_downstream_pipeline",
    description="Compute -> aggregate -> export -> Snowflake, triggered by new crawl data",
    default_args=default_args,
    schedule=[RAW_LEAGUE_DATA],
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    tags=["draft-analyzer"],
) as dag:

    build_lineages = BashOperator(
        task_id="build_lineages",
        bash_command=f"cd {PROJECT_DIR} && python build_lineages.py",
        **stage_args(timedelta(minutes=10)),
    )

    compute_optimal_lineups = BashOperator(
        task_id="compute_optimal_lineups",
        bash_command=f"cd {PROJECT_DIR} && python compute_optimal_lineups.py",
        **stage_args(timedelta(minutes=60)),
    )

    compute_top_pct_optimal = BashOperator(
        task_id="compute_top_pct_optimal",
        bash_command=f"cd {PROJECT_DIR} && python compute_top_pct_optimal.py",
        **stage_args(timedelta(minutes=15)),
    )

    compute_position_weight_scores = BashOperator(
        task_id="compute_position_weight_scores",
        bash_command=f"cd {PROJECT_DIR} && python compute_position_weight_scores.py",
        **stage_args(timedelta(minutes=30)),
    )

    compute_sustained_success = BashOperator(
        task_id="compute_sustained_success",
        bash_command=f"cd {PROJECT_DIR} && python compute_sustained_success.py",
        **stage_args(timedelta(minutes=15)),
    )

    build_trend_stats = BashOperator(
        task_id="build_trend_stats",
        bash_command=f"cd {PROJECT_DIR} && python build_trend_stats.py",
        outlets=[TREND_STATS_MYSQL],
        **stage_args(timedelta(minutes=15)),
    )

    export_static_data = BashOperator(
        task_id="export_static_data",
        bash_command=f"cd {PROJECT_DIR} && python scripts/export_static_data.py",
        **stage_args(timedelta(minutes=10)),
    )

    load_snowflake = BashOperator(
        task_id="load_snowflake",
        bash_command=f"cd {PROJECT_DIR} && python load_snowflake.py",
        outlets=[TREND_STATS_SNOWFLAKE],
        **stage_args(timedelta(minutes=10)),
    )

    (
        build_lineages
        >> compute_optimal_lineups
        >> compute_top_pct_optimal
        >> compute_sustained_success
        >> build_trend_stats
        >> export_static_data
        >> load_snowflake
    )
    compute_top_pct_optimal >> compute_position_weight_scores
