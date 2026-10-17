# Operations Runbook

Operational reference for running and troubleshooting the data pipeline — crawl,
compute, aggregate, Snowflake load, and orchestration. For what the app does and its
architecture, see `README.md`.

## Pipeline stages, in order

| # | Script | What it does | Resumable? |
|---|---|---|---|
| 1 | `crawler.py` | Crawls Sleeper for leagues/rosters/draft_picks. Multi-day for a full pass (targets 2,000 leagues × up to 972 season/size/type/scoring/format bucket combinations). | Idempotent DB writes, but re-scans all known owners from scratch each invocation (no separate "fully crawled" tracking beyond per-league dedup) — a restart isn't free, but it's not harmful either. |
| 2 | `fetch_roster_settings.py` | Backfills league settings (roster positions, size, season type) for any league still missing them. `crawler.py` fills these at crawl time, so normally a no-op; `--all` forces a full re-fetch (~95K API calls, hours). | Yes — only touches leagues with missing settings |
| 3 | `load_adp.py` | Loads ADP CSVs from `adp_data/` (FantasPros/DraftSharks/Sleeper), merges by name with alias/suffix handling. | Yes (`ON DUPLICATE KEY UPDATE`) |
| 4 | `fetch_weekly_projections.py` | Pulls historical weekly player projections. Only needed when extending into new seasons/weeks. | Yes |
| 5 | `build_lineages.py` | Chains dynasty leagues season-to-season into lineages. | Yes — safe to rerun any time the crawler adds leagues |
| 6 | `compute_optimal_lineups.py` | Simulates each roster's weekly optimal lineup from projections. | Yes — skips leagues already computed |
| 7 | `compute_top_pct_optimal.py` | Ranks rosters within their league by optimal-lineup total; top 20% = success. | Yes |
| 8 | `compute_position_weight_scores.py` | Exploratory per-position weight scoring. Not consumed by `build_trend_stats.py` — safe to skip if you're in a hurry. | Yes |
| 9 | `compute_sustained_success.py` | Dynasty-only: success rate across a lineage's seasons so far. | Yes |
| 10 | `build_trend_stats.py` | Full replay of every roster's picks into the two production summary tables. Cheap (~30MB output) — always does a full rebuild rather than an incremental one. | Yes (idempotent full rebuild) |
| 11 | `scripts/export_static_data.py` | Exports the production tables from the local MySQL to static JSON for the client-side frontend (deployed to GitHub Pages). | Yes |
| 12 | `load_snowflake.py` | Loads the same production summary tables into Snowflake. Truncate + full reload each run. | Yes |

Stages 1–4 are **ingestion** (Sleeper API, ADP CSVs, projections API → local MySQL);
each is safe to rerun without creating duplicates (`INSERT IGNORE` / `ON DUPLICATE KEY
UPDATE` / keyed `UPDATE`s), which is what lets a multi-day crawl simply be restarted
after an interruption. Stages 5–12 are compute → aggregate → publish.

**Rule of thumb:** after any meaningful crawl progress, rerun stages 5–12 in order.
Skipping straight to `build_trend_stats.py` without the prerequisite compute scripts
silently uses stale/missing `top_pct_optimal` values.

## Orchestration (Airflow)

Two DAGs in `dags/`, deliberately separate:

- **`draft_analyzer_crawl`** — stage 1–2 above. `schedule=None` (manually triggered
  only) — a full crawl pass is multi-day, not something to fire on a cron.
- **`draft_analyzer_downstream_pipeline`** — stages 5–12. **Data-triggered**, not on a
  clock: the crawl DAG's last task updates the `RAW_LEAGUE_DATA` Airflow Dataset
  (`dags/draft_analyzer_datasets.py`), and this DAG is scheduled on that Dataset, so new
  crawl data automatically rebuilds everything downstream. Every stage is safe to rerun,
  so it can also be triggered by hand (e.g. after a weighting change with no new data).

**Alerting** (`dags/draft_analyzer_alerting.py`), on every task in both DAGs:
- **Failure / retry** alerts.
- **Slow-stage** alerts: each task has an expected duration; finishing slower than that
  sends an alert. `build_trend_stats`' 15-min expectation is based on a measured full
  run (6.1 min over 16.2M picks). Airflow's built-in SLAs aren't used — they're
  measured from the run's logical date, which means nothing for a Dataset-triggered DAG.
- **Hung stages**: `execution_timeout` at 4x the expected duration turns a hang into a
  failure, which alerts.

Alerts go to `ALERT_WEBHOOK_URL` (Slack or Discord incoming webhook) if set, and always
to the task log at WARNING level.

Airflow doesn't run natively on Windows (WSL2 or a container are the supported
paths) — this project uses Docker:

```
cp .env.example .env   # fill in DB_HOST/USER/PASSWORD/NAME, SNOWFLAKE_*
docker compose -f docker-compose.airflow.yml up airflow-init
docker compose -f docker-compose.airflow.yml up
```

Then open http://localhost:8080 (default login `airflow`/`airflow`). Trigger
`draft_analyzer_crawl` manually; `draft_analyzer_downstream_pipeline` starts on its own
when the crawl finishes.

**Windows/Docker Desktop note:** the containers don't use `.env`'s `DB_HOST` (usually
`127.0.0.1`, which inside a container is the container itself). They connect to
`AIRFLOW_DB_HOST`, default `host.docker.internal` — the host machine. Docker Desktop
forwards that so MySQL sees a `localhost` connection, so the normal `DB_USER` works.
Snowflake key-pair auth works too: `~/.snowflake` is mounted read-only into the
containers when `SNOWFLAKE_PRIVATE_KEY_PATH` is set.

## Snowflake

`load_snowflake.py` ships `draft_trend_stats`, `round1_trend_stats`, and `adp` — the
same tiny production aggregates that go to MySQL and to the frontend's static JSON —
into a Snowflake warehouse. It never touches the ~8.9GB local-only raw tables
(`draft_picks`, `rosters`, `leagues`, `player_weekly_projections`).

Setup:
1. Sign up for a free trial at [signup.snowflake.com](https://signup.snowflake.com).
2. Create a database (default expected name: `DRAFT_ANALYZER`) and note your account
   identifier from the account URL (e.g. `xy12345.us-east-1`).
3. Fill in `SNOWFLAKE_ACCOUNT`/`SNOWFLAKE_USER`/`SNOWFLAKE_PASSWORD` in `.env`
   (`SNOWFLAKE_WAREHOUSE`/`DATABASE`/`SCHEMA` default to `COMPUTE_WH`/`DRAFT_ANALYZER`/`PUBLIC`).
4. `python load_snowflake.py` — or `python load_snowflake.py --verify` to also run a
   few analytical sanity queries (window functions, a CTE, a join against `adp`)
   against the freshly-loaded warehouse.

**Status as of this writing: code complete, not yet run against a real account** — no
Snowflake trial has been created yet, so this hasn't been executed end-to-end. Run it
once credentials exist and update this note.

## Databricks / Spark (databricks/)

`databricks/build_trend_stats_spark.py` is a PySpark port of stage 10
(`build_trend_stats.py`) — the one stage that streams the whole raw `draft_picks` table
(~16M rows) through a single-threaded Python loop. The per-roster replay is expressed
as window functions (partition by roster, order by `pick_no`, running `SUM` over the
strictly-prior rows) so Spark parallelizes it across rosters, and it writes
`draft_trend_stats` / `round1_trend_stats` as Delta tables with the same schema as the
MySQL production tables. It doesn't replace stage 10 — MySQL stays the production path
— it's the scalable version of it.

Setup / run:
1. `pip install -r requirements-databricks.txt`, then
   `python databricks/export_raw_parquet.py --out-dir ./databricks_export` — exports just
   the columns the replay needs from the local raw tables (read-only) to Parquet; a few
   hundred MB instead of the ~8.6GB in MySQL.
2. Upload it to a Unity Catalog volume (Databricks CLI:
   `databricks fs cp -r ./databricks_export dbfs:/Volumes/main/draft_analyzer/raw`).
3. Run `databricks/build_trend_stats_spark.py` as a job (Python script task) or with
   `spark-submit`: `--raw-path /Volumes/main/draft_analyzer/raw --catalog main
   --schema draft_analyzer [--verify]`. `--verify` runs a couple of window-function
   queries on the result, same idea as `load_snowflake.py --verify`.

**Verification status.** Row-for-row parity with `build_trend_stats.py` was checked two
ways, both locally on a single-machine Spark:
- `python -m unittest databricks.test_build_trend_stats_spark` — hand-built edge cases
  (dynasty latest-season labels, second round-1 picks, K/DEF inflating `max_round`,
  unparseable scoring types, out-of-order `pick_no`) plus 60 randomized leagues,
  against a transcription of the original replay loop.
- The real `build_trend_stats.py` run against a scratch-DB copy of ~9.7K real leagues
  (all three formats, incl. dynasty lineages) vs. the Spark port on the same rows:
  identical — 7,363 round-1 rows and 157,418 trend rows, no missing rows, no value
  differences.

**Not yet run on a real Databricks workspace** — no workspace exists yet, so the
Unity Catalog volume read and the Delta `saveAsTable` writes (the only parts that
differ from what was tested) are untested. Run once and update this note.

**Known data quirk (affects the original script too):** ~4% of leagues contain a roster
with two picks sharing the same `pick_no` (e.g. a QB and a WR both at pick 2). Row order
among tied picks is arbitrary, so those rosters' buckets are nondeterministic in
`build_trend_stats.py` *and* in the Spark port — the two only disagreed on exactly those
leagues (~1% of aggregate rows) and matched exactly once they were excluded. Worth
finding out why the crawler stored duplicate `pick_no`s.

## AWS Lambda deployment (api/)

`api/lambda_handler.py` wraps the existing FastAPI app (`api/main.py`) with
[Mangum](https://mangum.io) so it can run behind API Gateway — no route or logic
changes, just a deployment target for the same app.

```
python package_lambda.py                    # builds lambda_deployment.zip
cd terraform
cp terraform.tfvars.example terraform.tfvars # fill in db_host/user/password/name
terraform init
terraform apply                              # prints the API's invoke URL
```

`db_host` must be reachable from Lambda over the public internet — this config has no
VPC attached, so it needs a publicly reachable MySQL host, not a VPC-internal database. Re-run `package_lambda.py` then `terraform apply`
after any change to `api/`, `advisor.py`, or `config.py` to redeploy.

**Status as of this writing: code and Terraform config complete, never applied** — no
AWS account/credentials configured yet. Same "built, not yet run" caveat as the
Snowflake section above.

## Common failure modes

- **Recommendations look stale/wrong** → check whether `draft_trend_stats` reflects
  the latest `build_trend_stats.py` run (stage 10), not just the latest crawl.
- **Crawler restarted mid-run** (e.g. computer restart) → just rerun `crawler.py`; DB
  writes are idempotent regardless of where it left off, though it re-scans all known
  owners from scratch (not instant, but not destructive).
- **`export_static_data.py` fails or exports stale data** → confirm the local MySQL is
  running and `DB_*` in `.env` point at it, and that stage 10 actually ran since the
  last crawl.
- **Frontend shows old numbers after a pipeline rebuild** → `export_static_data.py`
  (stage 11) needs to be rerun and the frontend redeployed; the recompute alone
  doesn't touch the static JSON bundled into the site.
- **Dockerized Airflow tasks can't reach MySQL** → check `AIRFLOW_DB_HOST` (not
  `DB_HOST`) — see the Windows/Docker Desktop note above.
