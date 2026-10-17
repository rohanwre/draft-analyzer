"""
Builds frontend/public/data/trade_values.json, the static snapshot the trade calculator
runs on (frontend/src/engine/tradeEngine.ts). Same philosophy as the draft advisor: the
weights come from what actually happened in the crawled Sleeper leagues, not from what
people are currently willing to trade for (KTC / Roster Audit / FantasyCalc are all
market-only).

Four pieces, each written to the JSON:

1. players     - current-season (Sleeper) ROS weekly projections + receptions for every
                 fantasy-relevant QB/RB/WR/TE, age, team, plus preseason ADP for a
                 "market vs. model" comparison. Live data, pulled from Sleeper's API.
2. calibration - how much lineup strength turns into real outcomes. For every crawled
                 roster, z-score its optimal_points_total within its league and fit
                 P(made_playoffs) and win% against that z. Local DB (rosters/leagues).
3. ageCurves   - year-over-year retention of projected points by position and age,
                 from player_weekly_projections 2020-2025 + this season. Point-weighted
                 and kernel-smoothed across neighbouring ages (raw cells are ~10-50 players).
4. picks       - what the Nth rookie taken in a real dynasty rookie draft turned into:
                 per overall rookie pick (1QB vs superflex), the players taken there and
                 their projected points in each season after being drafted. Local DB
                 (draft_picks from dynasty leagues with season_index >= 2).

Usage: python build_trade_values.py [--refresh]   (--refresh re-downloads Sleeper data)
Needs the local MySQL (raw tables) running. Re-run weekly in-season so ROS projections
stay current, then rebuild/redeploy the frontend.
"""
import argparse
import json
import math
import statistics
from collections import defaultdict
from datetime import date
from pathlib import Path

import requests

from advisor import get_db, normalize_name

ROOT = Path(__file__).parent
CACHE = ROOT / "trade_cache"
OUT = ROOT / "frontend" / "public" / "data" / "trade_values.json"

POSITIONS = ["QB", "RB", "WR", "TE"]
POS_IDX = {p: i for i, p in enumerate(POSITIONS)}
HIST_SEASONS = [2020, 2021, 2022, 2023, 2024, 2025]
LAST_WEEK = 17
MAX_ROOKIE_PICK = 64           # 4 rounds of a 16-team rookie draft
TRAJECTORY_YEARS = 5           # seasons of production tracked per rookie
MIN_PICKS_PER_SLOT = 30
AGE_RANGE = range(20, 40)


def sleeper_get(url, cache_name, refresh):
    path = CACHE / cache_name
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def age_on(birth_date, season):
    """Age on Sept 1 of the given season, as a float."""
    if not birth_date:
        return None
    y, m, d = map(int, birth_date.split("-"))
    return round((date(season, 9, 1) - date(y, m, d)).days / 365.25, 2)


# ---------------------------------------------------------------------------
# 1. Current-season players
# ---------------------------------------------------------------------------

def fetch_current_season(season, refresh):
    """{player_id: {week: (pts_ppr, rec)}} for every projected week of the season."""
    weekly = defaultdict(dict)
    for week in range(1, LAST_WEEK + 1):
        rows = sleeper_get(
            f"https://api.sleeper.app/projections/nfl/{season}/{week}?season_type=regular",
            f"proj_{season}_{week}.json", refresh,
        )
        for row in rows:
            if (row.get("player") or {}).get("position") not in POS_IDX:
                continue
            stats = row.get("stats") or {}
            weekly[row["player_id"]][week] = (float(stats.get("pts_ppr") or 0), float(stats.get("rec") or 0))
    return weekly


def fetch_stats_to_date(season, current_week, refresh):
    """{player_id: [ppr points per game played]} for completed weeks."""
    games = defaultdict(list)
    for week in range(1, current_week):
        rows = sleeper_get(
            f"https://api.sleeper.app/stats/nfl/{season}/{week}?season_type=regular",
            f"stats_{season}_{week}.json", refresh,
        )
        for row in rows:
            stats = row.get("stats") or {}
            if (row.get("player") or {}).get("position") in POS_IDX and stats.get("gp"):
                games[row["player_id"]].append(float(stats.get("pts_ppr") or 0))
    return games


def load_market(cursor, season):
    """{(format, league_type): {normalized name: adp}} from the preseason ADP table."""
    cursor.execute("""
        SELECT player_name, adp, league_type, league_format FROM adp
        WHERE season = %s AND scoring_type = 'ppr'
    """, (season,))
    market = defaultdict(dict)
    for name, adp, league_type, league_format in cursor.fetchall():
        market[f"{league_format}|{league_type}"][normalize_name(name)] = float(adp)
    return market


def build_players(players, weekly, games, market, season, current_week):
    out = []
    ros_weeks = list(range(current_week, LAST_WEEK + 1))
    for pid, info in players.items():
        pos = info.get("position")
        if pos not in POS_IDX or not info.get("active"):
            continue
        proj = weekly.get(pid, {})
        ros = [proj.get(w, (0.0, 0.0)) for w in ros_weeks]
        season_games = [v for v in proj.values() if v[0] > 0]
        age = age_on(info.get("birth_date"), season)
        ros_total = sum(p for p, _ in ros)
        # Keep anyone projected for real points, plus young players on a roster who are
        # dynasty stashes even with no current role.
        if ros_total < 5 and not (info.get("team") and age is not None and age < 25 and season_games):
            continue

        per_game = statistics.mean(p for p, _ in season_games) if season_games else 0.0
        rec_per_game = statistics.mean(r for _, r in season_games) if season_games else 0.0
        name = f"{info.get('first_name', '')} {info.get('last_name', '')}".strip()
        key = normalize_name(name)
        ytd = games.get(pid, [])
        out.append({
            "id": pid,
            "n": name,
            "p": pos,
            "t": info.get("team") or "FA",
            "a": age,
            "x": info.get("years_exp"),
            "inj": info.get("injury_status"),
            "w": [round(p, 1) for p, _ in ros],
            "r": [round(r, 1) for _, r in ros],
            # Healthy per-game projection used as the base for future dynasty seasons.
            "b": round(per_game, 2),
            "br": round(rec_per_game, 2),
            "ytd": round(statistics.mean(ytd), 1) if ytd else None,
            "gp": len(ytd),
            "m": {k: v[key] for k, v in market.items() if key in v},
        })
    out.sort(key=lambda p: -sum(p["w"]))
    return out


# ---------------------------------------------------------------------------
# 2. Outcome calibration
# ---------------------------------------------------------------------------

def fit_logistic(points):
    """points: [(z, success, weight)] -> (a, b) for P = 1/(1+exp(-(a+bz))). Newton's method."""
    a, b = 0.0, 0.5
    for _ in range(50):
        ga = gb = haa = hab = hbb = 0.0
        for z, y, w in points:
            p = 1 / (1 + math.exp(-(a + b * z)))
            ga += w * (y - p)
            gb += w * (y - p) * z
            v = w * p * (1 - p)
            haa += v
            hab += v * z
            hbb += v * z * z
        det = haa * hbb - hab * hab
        a += (hbb * ga - hab * gb) / det
        b += (haa * gb - hab * ga) / det
    return a, b


def build_calibration(cursor):
    cursor.execute("""
        SELECT r.league_id, l.league_format, r.optimal_points_total, r.made_playoffs, r.wins, r.losses
        FROM rosters r JOIN leagues l USING (league_id)
        WHERE r.optimal_points_total IS NOT NULL
          AND l.league_format IN ('redraft', 'dynasty')
          AND (l.season_index IS NULL OR l.season_index = 1)
    """)
    leagues = defaultdict(list)
    for row in cursor.fetchall():
        leagues[row[0]].append(row)

    result = {}
    for fmt in ("redraft", "dynasty"):
        bins = defaultdict(lambda: [0, 0, 0.0])   # z bin -> [n, playoffs, win% sum]
        weekly_sds = []
        for rows in leagues.values():
            if rows[0][1] != fmt or len(rows) < 6:
                continue
            pts = [float(r[2]) for r in rows]
            mean, sd = statistics.mean(pts), statistics.pstdev(pts)
            if sd <= 0:
                continue
            weekly_sds.append(sd / LAST_WEEK)
            for r in rows:
                z = max(-2.0, min(2.0, round((float(r[2]) - mean) / sd * 4) / 4))
                games = (r[4] or 0) + (r[5] or 0)
                b = bins[z]
                b[0] += 1
                b[1] += r[3] or 0
                b[2] += (r[4] / games) if games else 0.5
        pts_for_fit = []
        for z, (n, playoffs, _) in bins.items():
            pts_for_fit += [(z, 1, playoffs), (z, 0, n - playoffs)]
        a, b = fit_logistic(pts_for_fit)
        # Least-squares slope of win% on z.
        n_tot = sum(v[0] for v in bins.values())
        zbar = sum(z * v[0] for z, v in bins.items()) / n_tot
        wbar = sum(v[2] for v in bins.values()) / n_tot
        cov = sum(v[0] * (z - zbar) * (v[2] / v[0] - wbar) for z, v in bins.items())
        var = sum(v[0] * (z - zbar) ** 2 for z, v in bins.items())
        result[fmt] = {
            "playoffA": round(a, 4),
            "playoffB": round(b, 4),
            "winSlope": round(cov / var, 4),
            "weeklySd": round(statistics.median(weekly_sds), 2),
            "rosters": n_tot,
            "leagues": len(weekly_sds),
            "curve": [[z, round(v[1] / v[0], 3), v[0]] for z, v in sorted(bins.items())],
        }
        print(f"  {fmt}: {len(weekly_sds)} leagues, P(playoffs)=logistic({a:.3f} + {b:.3f}z), "
              f"win%/z={cov / var:.3f}, weekly sd={statistics.median(weekly_sds):.2f}")
    return result


# ---------------------------------------------------------------------------
# 3. Age curves
# ---------------------------------------------------------------------------

def season_totals(cursor, current_weekly, season):
    """{(player_id, season): (position, points per week over a 17-week season)}."""
    cursor.execute("""
        SELECT player_id, position, season, SUM(pts_ppr) FROM player_weekly_projections
        GROUP BY player_id, position, season
    """)
    totals = {(pid, s): (pos, float(t or 0) / LAST_WEEK) for pid, pos, s, t in cursor.fetchall()}
    for pid, weeks in current_weekly.items():
        totals[(pid, season)] = (None, sum(p for p, _ in weeks.values()) / LAST_WEEK)
    return totals


LEVEL_EDGES = [8, 12, 16]          # points/week bins for the level adjustment
AGE_BAND_EDGES = [25, 28]          # age bands: <25, 25-27, 28+


def retention_samples(totals, players, season):
    """[(pos, age, points/week this season, points/week next season)] for every player-season with a real role."""
    out = []
    for (pid, s), (pos, cur) in totals.items():
        info = players.get(pid) or {}
        pos = pos or info.get("position")
        if pos not in POS_IDX or cur < 3 or s >= season:
            continue
        age = age_on(info.get("birth_date"), s)
        if age is None:
            continue
        out.append((pos, age, cur, min(totals.get((pid, s + 1), (pos, 0.0))[1], cur * 2.5)))
    return out


def level_bin(v):
    return sum(1 for e in LEVEL_EDGES if v >= e)


def age_band(age):
    return sum(1 for e in AGE_BAND_EDGES if age >= e)


def build_age_curves(totals, players, season):
    samples = retention_samples(totals, players, season)
    raw = defaultdict(lambda: [0.0, 0.0, 0])   # (pos, age) -> [sum cur, sum next, n]
    for pos, age, cur, nxt in samples:
        cell = raw[(pos, int(age))]
        cell[0] += cur
        cell[1] += nxt
        cell[2] += 1

    # Raw retention is < 1 at every age: anyone with a real projection this year regresses
    # on average, while next year's new risers aren't in the sample. The trade engine keeps
    # replacement level fixed at this season's, so curves are expressed relative to the
    # league-wide point-weighted average - "how this age ages vs. the rest of the pool".
    league_avg = sum(c[1] for c in raw.values()) / sum(c[0] for c in raw.values())
    print(f"  league-wide raw retention {league_avg:.3f}")

    curves = {}
    kernel = {-2: 1, -1: 2, 0: 3, 1: 2, 2: 1}
    for pos in POSITIONS:
        curve = {}
        for age in AGE_RANGE:
            cur = nxt = n = 0.0
            for off, w in kernel.items():
                c = raw.get((pos, age + off))
                if c:
                    cur += w * c[0]
                    nxt += w * c[1]
                    n += w * c[2]
            # Thin tails (very young / very old) shrink toward a neutral-ish 0.85 prior.
            prior_w = 40.0
            ratio = (nxt + 0.85 * prior_w) / (cur + prior_w) if cur else 0.85
            curve[age] = round(ratio / league_avg, 3)
        # Retention shouldn't rise again once a player is past their prime.
        peak = max(curve, key=curve.get)
        for age in AGE_RANGE:
            if age > peak + 1 and curve[age] > curve[age - 1]:
                curve[age] = curve[age - 1]
        curves[pos] = [curve[a] for a in AGE_RANGE]
        print(f"  {pos}: " + " ".join(f"{a}:{curve[a]:.2f}" for a in range(21, 35)))
    # Age alone overstates growth for young players who are already elite (a 24-year-old
    # at 20 pts/week has far less room to grow than one at 9). Fit a multiplier on top of
    # the age curve per (age band, production level) - it's an interaction, a single
    # level multiplier doesn't capture it.
    level = defaultdict(lambda: [0.0, 0.0])
    for pos, age, cur, nxt in samples:
        pred = cur * curves[pos][max(0, min(len(AGE_RANGE) - 1, int(age) - AGE_RANGE.start))] * league_avg
        cell = level[(age_band(age), level_bin(cur))]
        cell[0] += pred
        cell[1] += nxt
    prior = 30.0   # shrink thin cells toward 1.0
    level_adj = [[round((level[(a, b)][1] + prior) / (level[(a, b)][0] + prior), 3)
                  for b in range(len(LEVEL_EDGES) + 1)] for a in range(len(AGE_BAND_EDGES) + 1)]
    print(f"  level adjustment [age band {AGE_BAND_EDGES}][pts/week {LEVEL_EDGES}]: {level_adj}")
    return {"startAge": AGE_RANGE.start, "byPos": curves, "rawLeagueAvg": round(league_avg, 3),
            "levelEdges": LEVEL_EDGES, "ageBandEdges": AGE_BAND_EDGES, "levelAdj": level_adj}


def retention(curves, pos, age, level=None):
    arr = curves["byPos"][pos]
    i = max(0, min(len(arr) - 1, int(age) - curves["startAge"]))
    adj = curves["levelAdj"][age_band(age)][level_bin(level)] if level is not None and "levelAdj" in curves else 1.0
    return arr[i] * adj


# ---------------------------------------------------------------------------
# 4. Rookie pick outcomes
# ---------------------------------------------------------------------------

def build_pick_samples(cursor, players, totals, curves, season):
    by_name = defaultdict(list)
    for pid, info in players.items():
        if info.get("position") in POS_IDX:
            name = f"{info.get('first_name', '')} {info.get('last_name', '')}".strip()
            by_name[normalize_name(name)].append(pid)

    cursor.execute("""
        SELECT d.draft_id, l.season, l.league_type, d.pick_no, d.player_name
        FROM draft_picks d JOIN leagues l USING (league_id)
        WHERE l.league_format = 'dynasty' AND l.season_index >= 2 AND d.pick_no <= %s
        ORDER BY d.draft_id, d.pick_no
    """, (MAX_ROOKIE_PICK,))
    drafts = defaultdict(list)
    for draft_id, s, league_type, pick_no, name in cursor.fetchall():
        drafts[(draft_id, s, league_type)].append((pick_no, name))

    trajectories = {}        # player_id -> [pos idx, y1..yN]
    counts = defaultdict(lambda: defaultdict(int))   # (league_type, pick) -> {pid: n}
    kept = skipped = 0
    for (_, s, league_type), picks in drafts.items():
        resolved = []
        for pick_no, name in picks:
            pid = None
            for cand in by_name.get(normalize_name(name), []):
                exp = players[cand].get("years_exp")
                if exp is not None and season - exp == s:
                    pid = cand
            resolved.append((pick_no, pid))
        # Only trust drafts that are actually rookie drafts (supplemental/devy/re-drafts
        # mix in veterans).
        if sum(1 for _, pid in resolved if pid) < 0.8 * len(resolved):
            skipped += 1
            continue
        kept += 1
        for pick_no, pid in resolved:
            if not pid:
                continue
            if pid not in trajectories:
                info = players[pid]
                pos = info["position"]
                age = age_on(info.get("birth_date"), s) or 22.0
                years, last = [], None
                for k in range(TRAJECTORY_YEARS):
                    val = totals.get((pid, s + k))
                    if val is not None and s + k <= season:
                        last = val[1]
                    else:
                        last = (last or 0.0) * retention(curves, pos, age + k - 1, last or 0.0)
                    years.append(round(last, 2))
                trajectories[pid] = [POS_IDX[pos]] + years
            counts[(league_type, pick_no)][pid] += 1
    print(f"  rookie drafts kept {kept}, skipped {skipped} (not rookie-only); {len(trajectories)} players")

    pid_index = {pid: i for i, pid in enumerate(trajectories)}
    slots = {}
    for league_type in ("standard", "qb_premium"):
        rows = []
        for pick in range(1, MAX_ROOKIE_PICK + 1):
            c = counts.get((league_type, pick), {})
            # Thin slots borrow from neighbours so a late 4th never has 3 samples.
            spread = 0
            while sum(c.values()) < MIN_PICKS_PER_SLOT and spread < 4:
                spread += 1
                c = defaultdict(int, c)
                for nb in (pick - spread, pick + spread):
                    for pid, n in counts.get((league_type, nb), {}).items():
                        c[pid] += n
            rows.append([[pid_index[pid], n] for pid, n in sorted(c.items(), key=lambda kv: -kv[1])[:60]])
        slots[league_type] = rows
    return {"trajectories": list(trajectories.values()), "byLeagueType": slots,
            "years": TRAJECTORY_YEARS}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="re-download Sleeper data")
    args = parser.parse_args()
    CACHE.mkdir(exist_ok=True)

    state = sleeper_get("https://api.sleeper.app/v1/state/nfl", "state.json", args.refresh)
    season, current_week = int(state["season"]), int(state["week"])
    print(f"Season {season}, week {current_week}")
    players = sleeper_get("https://api.sleeper.app/v1/players/nfl", "players.json", args.refresh)

    db = get_db()
    cursor = db.cursor()

    print("Current-season projections...")
    weekly = fetch_current_season(season, args.refresh)
    games = fetch_stats_to_date(season, current_week, args.refresh)
    market = load_market(cursor, season)
    player_rows = build_players(players, weekly, games, market, season, current_week)
    print(f"  {len(player_rows)} players")

    print("Calibration (lineup strength -> playoffs)...")
    calibration = build_calibration(cursor)

    print("Age curves...")
    totals = season_totals(cursor, weekly, season)
    curves = build_age_curves(totals, players, season)

    print("Rookie pick outcomes...")
    picks = build_pick_samples(cursor, players, totals, curves, season)

    payload = {
        "generated": date.today().isoformat(),
        "season": season,
        "week": current_week,
        "rosWeeks": list(range(current_week, LAST_WEEK + 1)),
        "positions": POSITIONS,
        "players": player_rows,
        "calibration": calibration,
        "ageCurves": curves,
        "picks": picks,
    }
    OUT.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
