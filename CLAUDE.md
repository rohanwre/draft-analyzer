# Draft Analyzer: project notes

Fantasy football tools built on ~95k crawled Sleeper leagues (2020-2025). Two products
live in one frontend:

- **Draft room** (`#/`): live draft recommendations from historical draft outcomes.
- **Trade desk** (`#/trade`): redraft + dynasty trade calculator valued by lineup impact
  and real outcomes, not market trade data.

The live site is fully static/client-side (Vercel `draft-analyzer-web` + GitHub Pages).
Engines are TypeScript in `frontend/src/engine/`, running on JSON snapshots in
`frontend/public/data/`. The FastAPI backend in `api/` (Railway) is NOT used by the
frontend anymore.

## Local setup

- MySQL 8 service `MySQL80` holds the raw tables (`draft_picks`, `rosters`, `leagues`,
  `player_weekly_projections`, `adp`). It needs an elevated shell to start:
  `Start-Process net -ArgumentList "start MySQL80" -Verb RunAs`.
- DB creds come from `.env` (see `.env.example`), read by `config.py`.
- Frontend: `cd frontend && npm run dev` (`npm run build` runs `tsc -b` + vite).
- TS engine files can be smoke-tested in Node with
  `node --experimental-strip-types file:///.../script.ts` (import with a `file:///` URL on Windows).

## Draft room pipeline (after a crawl or model change)

`crawler.py` → `fetch_weekly_projections.py` (new seasons only) → `compute_optimal_lineups.py`
→ `compute_top_pct_optimal.py` → `compute_position_weight_scores.py` → `build_trend_stats.py`
→ `scripts/export_static_data.py` → rebuild/redeploy frontend. Skipping the export step means
the live site silently serves stale data.

## Trade desk

### Data: `python build_trade_values.py [--refresh]`

Writes `frontend/public/data/trade_values.json` (~200 KB). Needs local MySQL plus internet
(Sleeper API). Sleeper downloads are cached in `trade_cache/` (gitignored); `--refresh`
re-downloads. **Re-run weekly in-season with `--refresh`**, because ROS projections, the
current week and injuries all come from that pull. Then commit the JSON and deploy.

What it computes:
1. `players`: every active QB/RB/WR/TE with real projections (or young + rostered). Sleeper
   weekly ROS projections (PPR points + receptions, so half/standard/TE-premium are derived
   client-side), healthy per-game base, age, preseason ADP per `format|league_type`.
2. `calibration`: per format (redraft/dynasty), z-score each crawled roster's
   `optimal_points_total` within its league. Then fit P(made_playoffs) = logistic(a + b·z),
   the win% slope, and the median weekly SD of team strength. This is the "outcomes" part.
3. `ageCurves`: point-weighted year-over-year retention of projected points by position×age
   (2020-2026), kernel-smoothed, **divided by the league-wide average** (raw retention is
   ~0.82 at every age due to regression/new entrants; the engine keeps replacement level
   fixed, so curves must be relative). Plus an `levelAdj[ageBand][levelBin]` interaction fit.
4. `picks`: for each overall rookie-draft pick 1-64, split 1QB (`standard`) vs superflex
   (`qb_premium`), the players actually taken there in crawled dynasty rookie drafts
   (dynasty leagues with `season_index >= 2`, drafts kept only if ≥80% of picks are true
   rookies) and their projected points/week in years 1-5 (unobserved years extrapolated
   with the age curve).

`draft_picks` has no player_id. Rookies are matched by `normalize_name` + Sleeper
`years_exp` = draft season.

### Engine: `frontend/src/engine/tradeEngine.ts`

- **Value = starter points added.** `buildContext` snake-drafts the player pool into N
  simulated lineups per season (a team only takes a player who'd start). An asset's value
  is its **average** lineup gain across all N teams. (A single "median team" was tried and
  gave lopsided rosters and bad SF QB values.)
- Packages are valued jointly via `lineupTotal`, so consolidation premium is emergent.
- Season 0 = real ROS weeks (byes included); seasons 1-4 (dynasty) = base × AVAILABILITY
  (15/17) × retention chain. Season weights come from `STRATEGY_WEIGHTS` (Win now /
  Balanced / Rebuild).
- Picks: 40 quantile draws of real outcomes for that slot; unknown slot averages the round.
- Playoff odds: `playoffDelta` converts season lineup points to z using `weeklySd × 17`
  (linear, team-strength spread is persistent). Shown NET (gets − gives).
- Verdict: ratio of weighted values. <1.08 even, <1.2 leans, <1.5 wins, else lopsided.
- FLEX = RB/WR/TE (Sleeper standard), plus `WRRB_FLEX`, `REC_FLEX`, `SUPER_FLEX`.
  Note: this differs from the draft room's `rosterSlots.ts`, where FLEX is RB/WR only.

### League mode: `frontend/src/engine/sleeperImport.ts`

Browser calls Sleeper's public API directly (CORS-open): username → leagues, or league
ID → league/rosters/users/traded_picks. Next season's picks get a projected slot from
current lineup strength + record. `evaluateLeague` re-ranks both teams against every team in
the league for playoff odds. Pick keys include the original roster (`|r<id>`) so two teams'
own 2027 1sts don't collide. Leagues that haven't drafted yet have empty rosters (all teams
show identical odds), which is expected.

### UI: `frontend/src/trade/`

`TradePage.tsx` (all sections), `AssetPicker.tsx`, `Charts.tsx` (hand-rolled SVG),
`format.ts` (URL-hash share encoding `#/trade?a=<ids,pYYYY.R.S>&b=...`), `trade.css`.
Design is deliberately not generic fantasy-app: paper/ink "trade ticket", square corners,
Archivo (wdth axis) + JetBrains Mono from Google Fonts, rubber-stamp verdict. All tokens are
scoped to `.trade-desk`, with dark mode via `prefers-color-scheme` and `:root[data-theme]`.
Side and position colors were checked with the dataviz palette validator in both modes, so
re-validate if you change them. `.trade-desk` needs `min-width: 0` because `#root` is a
flex column.

### Known limitations

Current W-L record isn't in playoff odds (only in projected pick slots). K/DEF/IDP are
ignored. "Market" rank is preseason ADP, stale by midseason. Dynasty horizon is 4 future
seasons, which slightly undervalues picks and very young players. Asset rows show the
average-team value even in league mode; the verdict uses roster-aware values.

## Conventions

- Push policy: workflow-affecting changes get committed and pushed to `main` (auto-deploys).
  Small/isolated ones get a local commit only.
- Commit only the files you touched. The working tree often has unrelated in-progress
  work (Airflow/Terraform/Databricks/Snowflake files).
- Never TRUNCATE or bulk-write the Railway prod DB from an agent session (classifier-blocked).
  Use `mysqldump --replace`, or have the user run it.
