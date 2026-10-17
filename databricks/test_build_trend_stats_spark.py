"""
Parity test: build_trend_stats_spark.py must produce exactly what build_trend_stats.py
would from the same raw data.

The reference below is the replay loop from build_trend_stats.main() (plus its
load_leagues / load_roster_outcomes / load_dynasty_outcomes / load_max_rounds filters)
transcribed to run over in-memory rows instead of a MySQL cursor. bucket_score and
parse_scoring_type are the real functions, imported, not copies - so if those change,
this test exercises the change. The replay loop itself is a transcription and has to be
kept in sync by hand if build_trend_stats.py's loop changes.

Needs pyspark + a JDK (Spark won't start without one), and the repo's Python deps for
the advisor import:
    python -m unittest databricks.test_build_trend_stats_spark -v
"""
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from pyspark.sql import SparkSession
from pyspark.sql.types import (
    DoubleType, IntegerType, StringType, StructField, StructType,
)

from advisor import bucket_score
from build_trend_stats import parse_scoring_type, parse_te_premium
import build_trend_stats_spark as spark_job

POSITIONS = ("QB", "RB", "WR", "TE")

PICKS_SCHEMA = StructType([
    StructField("league_id", StringType()), StructField("owner_id", StringType()),
    StructField("draft_slot", IntegerType()), StructField("round", IntegerType()),
    StructField("pick_no", IntegerType()), StructField("position", StringType()),
])
ROSTERS_SCHEMA = StructType([
    StructField("league_id", StringType()), StructField("owner_id", StringType()),
    StructField("top_pct_optimal", IntegerType()),
    StructField("sustained_success_rate", DoubleType()),
])
LEAGUES_SCHEMA = StructType([
    StructField("league_id", StringType()), StructField("season", IntegerType()),
    StructField("season_type", StringType()), StructField("league_size", IntegerType()),
    StructField("league_type", StringType()), StructField("te_premium", IntegerType()),
    StructField("league_format", StringType()), StructField("scoring_type", StringType()),
    StructField("lineage_id", StringType()), StructField("season_index", IntegerType()),
])


def reference_replay(picks, rosters, leagues, season_min=2020, season_max=2025):
    """picks/rosters/leagues are lists of dicts. Returns (round1_stats, trend_stats)
    in build_trend_stats.py's own dict shape: key -> [total, success_sum]."""
    # load_leagues
    lg = {}
    for l in leagues:
        if season_min <= l["season"] <= season_max and l["season_type"] == "regular" \
                and l["league_format"] is not None:
            lg[l["league_id"]] = (
                l["league_size"], l["league_type"], parse_te_premium(l["te_premium"]),
                l["league_format"], parse_scoring_type(l["scoring_type"]),
            )
    fmt_by_league = {l["league_id"]: l for l in leagues}

    # load_roster_outcomes (redraft/keeper)
    outcomes = {}
    for r in rosters:
        l = fmt_by_league.get(r["league_id"])
        if l is None or r["top_pct_optimal"] is None:
            continue
        if l["league_format"] is None or l["league_format"] != "dynasty":
            outcomes[(r["league_id"], r["owner_id"])] = r["top_pct_optimal"]

    # load_dynasty_outcomes
    latest = {}
    for r in rosters:
        l = fmt_by_league.get(r["league_id"])
        if l is None or l["league_format"] != "dynasty" or l["lineage_id"] is None:
            continue
        if r["sustained_success_rate"] is None or r["owner_id"] is None or r["owner_id"] == "unknown":
            continue
        key = (l["lineage_id"], r["owner_id"])
        if key not in latest or l["season_index"] > latest[key][0]:
            latest[key] = (l["season_index"], r["sustained_success_rate"])
    startup = {
        l["lineage_id"]: l["league_id"] for l in leagues
        if l["league_format"] == "dynasty" and l["season_index"] == 1 and l["lineage_id"] is not None
    }
    for (lineage_id, owner_id), (_, rate) in latest.items():
        if lineage_id in startup:
            outcomes[(startup[lineage_id], owner_id)] = rate

    # load_max_rounds - over ALL positions
    max_rounds = {}
    for p in picks:
        max_rounds[p["league_id"]] = max(max_rounds.get(p["league_id"], 0), p["round"])

    # stream_picks grouping + the main() loop
    by_roster = {}
    for p in picks:
        if p["position"] in POSITIONS:
            by_roster.setdefault((p["league_id"], p["owner_id"]), []).append(p)

    round1_stats, trend_stats = {}, {}
    for (league_id, owner_id), plist in by_roster.items():
        league = lg.get(league_id)
        if league is None:
            continue
        league_size, league_type, te_premium, league_format, scoring_type = league
        success_label = outcomes.get((league_id, owner_id))
        if success_label is None:
            continue
        max_round = max_rounds.get(league_id)
        if not max_round:
            continue

        plist.sort(key=lambda p: p["pick_no"])
        draft_slot = plist[0]["draft_slot"]
        cumulative = {"QB": 0, "RB": 0, "WR": 0, "TE": 0}
        seen_round1 = False
        for p in plist:
            round_num, position = p["round"], p["position"]
            if round_num == 1 and not seen_round1:
                key = (draft_slot, league_size, league_type, league_format, scoring_type, te_premium, position)
                entry = round1_stats.setdefault(key, [0, 0])
                entry[0] += 1
                entry[1] += success_label
                seen_round1 = True
            else:
                buckets = tuple(bucket_score(cumulative[x]) for x in POSITIONS)
                key = (league_size, league_type, league_format, scoring_type, te_premium, round_num, *buckets, position)
                entry = trend_stats.setdefault(key, [0, 0])
                entry[0] += 1
                entry[1] += success_label
            cumulative[position] += max(1, max_round - round_num + 1)
    return round1_stats, trend_stats


def spark_result(spark, picks, rosters, leagues):
    round1, trend = spark_job.build_trend_tables(
        spark.createDataFrame(picks, PICKS_SCHEMA),
        spark.createDataFrame(rosters, ROSTERS_SCHEMA),
        spark.createDataFrame(leagues, LEAGUES_SCHEMA),
    )
    r1 = {tuple(row)[:-2]: [row["total_count"], row["success_count"]] for row in round1.collect()}
    tr = {tuple(row)[:-2]: [row["total_count"], row["success_count"]] for row in trend.collect()}
    return r1, tr


def league(league_id, **kw):
    base = dict(league_id=league_id, season=2023, season_type="regular", league_size=12,
                league_type="standard", te_premium=0, league_format="redraft",
                scoring_type="1.0", lineage_id=None, season_index=None)
    base.update(kw)
    return base


def pick(league_id, owner_id, slot, rnd, pick_no, pos):
    return dict(league_id=league_id, owner_id=owner_id, draft_slot=slot, round=rnd,
                pick_no=pick_no, position=pos)


def roster(league_id, owner_id, top=None, sustained=None):
    return dict(league_id=league_id, owner_id=owner_id, top_pct_optimal=top,
                sustained_success_rate=sustained)


def assert_stats_equal(tc, expected, actual, label):
    tc.assertEqual(set(expected), set(actual), f"{label}: key sets differ")
    for key, (total, success) in expected.items():
        tc.assertEqual(total, actual[key][0], f"{label} total {key}")
        tc.assertAlmostEqual(success, actual[key][1], places=9, msg=f"{label} success {key}")


class ParityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spark = (
            SparkSession.builder.master("local[2]")
            .config("spark.sql.shuffle.partitions", "4")
            .config("spark.ui.enabled", "false")
            .getOrCreate()
        )

    @classmethod
    def tearDownClass(cls):
        cls.spark.stop()

    def check(self, picks, rosters, leagues):
        exp_r1, exp_tr = reference_replay(picks, rosters, leagues)
        act_r1, act_tr = spark_result(self.spark, picks, rosters, leagues)
        self.assertTrue(exp_tr, "reference produced nothing - test data is vacuous")
        assert_stats_equal(self, exp_r1, act_r1, "round1")
        assert_stats_equal(self, exp_tr, act_tr, "trend")
        return exp_r1, exp_tr

    def test_edge_cases(self):
        leagues = [
            league("L1"),
            league("L2", season=2022, league_size=10, league_type="superflex", te_premium=1,
                   league_format="keeper", scoring_type="0.5"),
            league("OLD", season=2019),                       # out of season range
            league("POST", season_type="post"),               # not regular season
            league("NOFMT", league_format=None),              # unknown format -> dropped
            league("BADREC", scoring_type="garbage"),         # unparseable -> ppr
            league("STD", scoring_type="0"),                  # -> standard
            league("D1", league_format="dynasty", lineage_id="A", season_index=1),
            league("D2", league_format="dynasty", lineage_id="A", season_index=2, season=2024),
        ]
        rosters, picks = [], []
        # L1/L2/OLD/POST/NOFMT/BADREC/STD each get 2 rosters: one labelled, one NULL outcome
        for lid in ("L1", "L2", "OLD", "POST", "NOFMT", "BADREC", "STD"):
            rosters += [roster(lid, "a", top=1), roster(lid, "b", top=None)]
            for owner, slot in (("a", 1), ("b", 2)):
                # pick_no inserted out of order on purpose; K/DEF rounds inflate max_round
                picks += [
                    pick(lid, owner, slot, 2, slot + 12, "WR"),
                    pick(lid, owner, slot, 1, slot, "RB"),
                    pick(lid, owner, slot, 3, slot + 24, "TE"),
                    pick(lid, owner, slot, 4, slot + 36, "RB"),
                    pick(lid, owner, slot, 15, slot + 168, "K"),
                ]
        # A second round-1 pick for one roster (pick_no 13 is still round 1): the second
        # must land in draft_trend_stats, not round1_trend_stats
        picks.append(pick("L1", "a", 1, 1, 13, "QB"))
        picks.append(pick("L1", "a", 1, 5, 49, "QB"))
        # dynasty: owner X's latest season (idx 2) rate wins, 'unknown' owner is ignored,
        # D2 picks are never replayed (no outcome keyed to a non-startup league)
        rosters += [
            roster("D1", "x", sustained=0.3), roster("D2", "x", sustained=0.6),
            roster("D1", "y", sustained=0.1),                # only season 1 exists
            roster("D1", "unknown", sustained=0.9),
            roster("D1", "z", sustained=None),               # no rate yet
        ]
        for lid in ("D1", "D2"):
            for owner, slot in (("x", 1), ("y", 2), ("unknown", 3), ("z", 4)):
                picks += [
                    pick(lid, owner, slot, 1, slot, "WR"),
                    pick(lid, owner, slot, 2, slot + 12, "RB"),
                    pick(lid, owner, slot, 3, slot + 24, "RB"),
                ]
        exp_r1, _ = self.check(picks, rosters, leagues)
        # sanity that the interesting cases really were exercised, not silently dropped
        slots_formats = {(k[0], k[3]) for k in exp_r1}
        self.assertIn((1, "dynasty"), slots_formats)   # x -> startup league, rate 0.6
        self.assertIn((2, "dynasty"), slots_formats)   # y
        self.assertNotIn((3, "dynasty"), slots_formats)  # 'unknown' owner excluded
        self.assertNotIn((4, "dynasty"), slots_formats)  # NULL rate excluded
        self.assertAlmostEqual(sum(v[1] for k, v in exp_r1.items() if k[3] == "dynasty"), 0.6 + 0.1)

    def test_randomized_drafts(self):
        rng = random.Random(20260919)
        leagues, rosters, picks = [], [], []
        for i in range(60):
            lid = f"R{i}"
            size = rng.choice([8, 10, 12, 14])
            rounds = rng.randint(5, 16)
            fmt = rng.choice(["redraft", "keeper", "redraft"])
            leagues.append(league(
                lid, season=rng.randint(2019, 2025), league_size=size,
                league_type=rng.choice(["standard", "superflex"]),
                te_premium=rng.choice([0, 0, 1]), league_format=fmt,
                scoring_type=rng.choice(["1.0", "0.5", "0.0", "0", "1", None]),
            ))
            for team in range(size):
                owner = f"o{team}"
                # some rosters have no outcome yet; some have NULL owner rate
                rosters.append(roster(lid, owner, top=rng.choice([0, 1, None, 0, 1])))
                for rnd in range(1, rounds + 1):
                    pick_no = (rnd - 1) * size + (team + 1 if rnd % 2 else size - team)
                    pos = rng.choice(["QB", "RB", "RB", "WR", "WR", "TE", "K", "DEF"])
                    picks.append(pick(lid, owner, team + 1, rnd, pick_no, pos))
                if rng.random() < 0.1:  # extra round-1 pick
                    picks.append(pick(lid, owner, team + 1, 1, rounds * size + team, rng.choice(POSITIONS)))
        self.check(picks, rosters, leagues)


if __name__ == "__main__":
    unittest.main()
