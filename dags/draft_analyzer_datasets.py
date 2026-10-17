"""
Airflow Datasets shared between the two DAGs - the data-dependency link that makes the
downstream rebuild run automatically when new crawl data lands, instead of on a clock.

The URIs are identifiers, not connection strings: Airflow never opens them, it just
records "a task with this outlet succeeded" and starts any DAG scheduled on it.
"""
from airflow.datasets import Dataset

# Updated by draft_analyzer_crawl once crawler.py + fetch_roster_settings.py finish.
RAW_LEAGUE_DATA = Dataset("mysql://local/fantasy_draft_analyzer/raw_league_data")

# Updated by draft_analyzer_downstream_pipeline - recorded for lineage in the UI, no
# DAG is scheduled on these yet.
TREND_STATS_MYSQL = Dataset("mysql://local/fantasy_draft_analyzer/draft_trend_stats")
TREND_STATS_SNOWFLAKE = Dataset("snowflake://DRAFT_ANALYZER/PUBLIC/DRAFT_TREND_STATS")
