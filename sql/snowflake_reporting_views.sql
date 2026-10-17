-- Reporting views over the Snowflake copy of the production summary tables.
--
-- Recreated by load_snowflake.py after every load (CREATE OR REPLACE - they're just
-- saved queries, no data of their own). The point is that trend questions get answered
-- with plain SQL in Snowflake - SELECT ... FROM RPT_... WHERE ... - instead of against
-- the production MySQL the live app's data export reads from.
--
-- "Success" throughout = a roster finishing in the top 20% of its league by
-- season-long optimal-lineup points (dynasty: sustained success across the lineage),
-- so the baseline success rate is ~20%. `lift_pct_pts` is how many percentage points
-- a choice beats (or trails) the average pick in the same situation.
-- `picks` is the sample size; filter on it (e.g. picks >= 100) before trusting a rate.


-- 1. Which position wins in each round, per league setup.
--    A roster's first round-1 pick lives in ROUND1_TREND_STATS, not DRAFT_TREND_STATS
--    (which only has the rare extra round-1 pick), so round 1 needs both.
CREATE OR REPLACE VIEW RPT_POSITION_SUCCESS_BY_ROUND AS
WITH all_picks AS (
    SELECT league_format, scoring_type, league_type, league_size, te_premium, round, position,
           total_count, success_count
    FROM DRAFT_TREND_STATS
    UNION ALL
    SELECT league_format, scoring_type, league_type, league_size, te_premium, 1, position,
           total_count, success_count
    FROM ROUND1_TREND_STATS
),
by_position AS (
    SELECT league_format, scoring_type, league_type, league_size, te_premium, round, position,
           SUM(total_count) AS picks,
           SUM(success_count) AS successes
    FROM all_picks
    GROUP BY league_format, scoring_type, league_type, league_size, te_premium, round, position
)
SELECT league_format, scoring_type, league_type, league_size, te_premium, round, position,
       picks,
       ROUND(successes / NULLIF(picks, 0) * 100, 1) AS success_pct,
       ROUND(picks / SUM(picks) OVER (PARTITION BY league_format, scoring_type, league_type,
                                                   league_size, te_premium, round) * 100, 1)
           AS share_of_round_picks_pct,
       ROUND((successes / NULLIF(picks, 0)
              - SUM(successes) OVER (PARTITION BY league_format, scoring_type, league_type, league_size, te_premium, round) / NULLIF(SUM(picks) OVER (PARTITION BY league_format, scoring_type, league_type, league_size, te_premium, round), 0)) * 100, 1) AS lift_pct_pts,
       RANK() OVER (PARTITION BY league_format, scoring_type, league_type, league_size,
                                 te_premium, round
                    ORDER BY successes / NULLIF(picks, 0) DESC) AS rank_in_round
FROM by_position;


-- 2. Best first-round position for each draft slot.
CREATE OR REPLACE VIEW RPT_ROUND1_BY_DRAFT_SLOT AS
SELECT league_format, scoring_type, league_type, league_size, te_premium, draft_slot, position,
       total_count AS picks,
       ROUND(success_count / NULLIF(total_count, 0) * 100, 1) AS success_pct,
       RANK() OVER (PARTITION BY league_format, scoring_type, league_type, league_size,
                                 te_premium, draft_slot
                    ORDER BY success_count / NULLIF(total_count, 0) DESC) AS rank_in_slot
FROM ROUND1_TREND_STATS;


-- 3. Roster construction: given how heavily a team had already invested in RB and WR
--    going into a round, how did the teams that took each position there do?
--    (Buckets are round-weighted cumulative draft capital: NONE/LIGHT/MODERATE/HEAVY.)
CREATE OR REPLACE VIEW RPT_ROSTER_BUILD_OUTCOMES AS
WITH builds AS (
    SELECT league_format, scoring_type, league_type, league_size, round,
           rb_bucket, wr_bucket, position,
           SUM(total_count) AS picks,
           SUM(success_count) AS successes
    FROM DRAFT_TREND_STATS
    GROUP BY league_format, scoring_type, league_type, league_size, round, rb_bucket, wr_bucket, position
)
SELECT league_format, scoring_type, league_type, league_size, round,
       rb_bucket AS rb_invested_before, wr_bucket AS wr_invested_before, position AS position_taken,
       picks,
       ROUND(successes / NULLIF(picks, 0) * 100, 1) AS success_pct,
       ROUND((successes / NULLIF(picks, 0)
              - SUM(successes) OVER (PARTITION BY league_format, scoring_type, league_type, league_size, round) / NULLIF(SUM(picks) OVER (PARTITION BY league_format, scoring_type, league_type, league_size, round), 0)) * 100, 1) AS lift_pct_pts
FROM builds;


-- 4. Current ADP vs history: for each player, the round they go in (12-team) and how
--    teams that took their position in that round historically did. Positive lift = the
--    market's usual pick there has paid off; negative = historically a reach.
CREATE OR REPLACE VIEW RPT_ADP_VS_HISTORY AS
WITH adp_round AS (
    SELECT player_name, position, adp, season, league_type, league_format, scoring_type,
           CEIL(adp / 12) AS adp_round
    FROM ADP
    WHERE adp IS NOT NULL
)
SELECT a.season, a.league_format, a.scoring_type, a.league_type,
       a.player_name, a.position, a.adp, a.adp_round,
       h.picks AS historical_picks,
       h.success_pct AS historical_success_pct,
       h.lift_pct_pts AS historical_lift_pct_pts,
       h.rank_in_round AS position_rank_in_round
FROM adp_round a
JOIN RPT_POSITION_SUCCESS_BY_ROUND h
  ON h.league_format = a.league_format
 AND h.scoring_type = a.scoring_type
 AND h.league_type = a.league_type
 AND h.league_size = 12
 AND h.te_premium = 0
 AND h.round = a.adp_round
 AND h.position = a.position;
