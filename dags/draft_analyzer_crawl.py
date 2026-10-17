"""
Runs the actual Sleeper crawl. Separate from draft_analyzer_downstream_pipeline.py on
purpose - per project memory, a single crawl pass targets ~972 season/size/type/
scoring/format bucket combinations (2,000 leagues each) and takes multiple days, not
minutes. Scheduling that on a daily cron alongside the cheap downstream rebuild would
either stack overlapping multi-day runs or force the whole DAG onto an unreasonably
long interval.

schedule=None: manually triggered only (`airflow dags trigger draft_analyzer_crawl`,
or the UI). crawler.py re-scans all known owners from scratch each invocation (no
separate "fully-crawled owner" tracking beyond per-league dedup - see project memory),
so triggering this mid-run isn't harmful, just redundant; DB writes are idempotent
regardless.

fetch_roster_settings.py backfills league settings for anything crawler.py found, so
it runs after, not in parallel. When it succeeds it updates the RAW_LEAGUE_DATA
dataset, which is what triggers draft_analyzer_downstream_pipeline - new crawl data
automatically rebuilds everything downstream.
"""
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator

from draft_analyzer_alerting import DEFAULT_ALERT_ARGS, slow_stage_check, stage_args
from draft_analyzer_datasets import RAW_LEAGUE_DATA

PROJECT_DIR = "/opt/project"

default_args = {
    "owner": "rohan",
    "retries": 1,
    "retry_delay": timedelta(minutes=15),
    **DEFAULT_ALERT_ARGS,
}

with DAG(
    dag_id="draft_analyzer_crawl",
    description="Multi-day Sleeper league crawl - manually triggered, not scheduled",
    default_args=default_args,
    schedule=None,
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    tags=["draft-analyzer"],
) as dag:

    crawl = BashOperator(
        task_id="crawler",
        bash_command=f"cd {PROJECT_DIR} && python crawler.py",
        # Multi-day by design: slow past 4 days, hard stop at 5.
        on_success_callback=slow_stage_check(timedelta(days=4)),
        execution_timeout=timedelta(days=5),
    )

    fetch_roster_settings = BashOperator(
        task_id="fetch_roster_settings",
        bash_command=f"cd {PROJECT_DIR} && python fetch_roster_settings.py",
        outlets=[RAW_LEAGUE_DATA],
        **stage_args(timedelta(hours=1)),
    )

    crawl >> fetch_roster_settings
