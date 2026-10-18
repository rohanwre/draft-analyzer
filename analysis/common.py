"""
Shared helpers for the analysis notebooks: pandas loaders over the local MySQL raw tables
and Sleeper's weekly box-score stats, plus a matplotlib style matching the site (paper and
ink, one stamp color, the same position colors as frontend/src/index.css).
"""
import sys
from pathlib import Path

import matplotlib as mpl
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from advisor import get_db  # noqa: E402
from build_trade_values import (  # noqa: E402
    CACHE, HIST_SEASONS, LAST_WEEK, POS_IDX, age_on, sleeper_get,
)

CACHE_DIR = Path(__file__).resolve().parent / ".cache"
POSITIONS = ["QB", "RB", "WR", "TE"]
POS_COLORS = {"QB": "#b07d00", "RB": "#1a9a6c", "WR": "#2a78d6", "TE": "#d6457a"}
PAPER, INK, INK_3, HAIR, STAMP = "#f1ece2", "#1a1815", "#6f685c", "#cfc6b4", "#c2361b"


def style():
    mpl.rcParams.update({
        "figure.facecolor": PAPER, "axes.facecolor": PAPER, "savefig.facecolor": PAPER,
        "axes.edgecolor": INK, "axes.labelcolor": INK, "text.color": INK,
        "xtick.color": INK_3, "ytick.color": INK_3,
        "axes.spines.top": False, "axes.spines.right": False, "axes.linewidth": 1.2,
        "axes.grid": True, "grid.color": HAIR, "grid.linewidth": 0.6, "axes.axisbelow": True,
        "axes.titleweight": "bold", "axes.titlelocation": "left", "axes.titlesize": 13,
        "font.size": 10.5, "figure.figsize": (9, 4.6), "figure.dpi": 110,
        "legend.frameon": False, "lines.linewidth": 2,
    })


def query(sql, params=None, cache=None):
    """Run SQL against the local MySQL and return a DataFrame. With cache="name", the
    result is saved to analysis/.cache/name.pkl and reused (the big aggregations take
    ~30-60s)."""
    path = CACHE_DIR / f"{cache}.pkl" if cache else None
    if path and path.exists():
        return pd.read_pickle(path)
    db = get_db()
    try:
        cur = db.cursor()
        cur.execute(sql, params or ())
        df = pd.DataFrame(cur.fetchall(), columns=[c[0] for c in cur.description])
    finally:
        db.close()
    for col in df.columns:
        if df[col].dtype == object:
            converted = pd.to_numeric(df[col], errors="coerce")
            if converted.notna().sum() == df[col].notna().sum():
                df[col] = converted
    if path:
        CACHE_DIR.mkdir(exist_ok=True)
        df.to_pickle(path)
    return df


def load_players():
    """Sleeper's player directory (current + retired) as a DataFrame indexed by player_id."""
    CACHE.mkdir(exist_ok=True)
    raw = sleeper_get("https://api.sleeper.app/v1/players/nfl", "players.json", False)
    df = pd.DataFrame.from_dict(raw, orient="index")
    return df[["full_name", "position", "birth_date", "years_exp", "team"]]


def load_weekly_actuals(seasons=HIST_SEASONS):
    """One row per player-game: actual PPR points from Sleeper's weekly box scores
    (downloaded once into trade_cache/, shared with build_trade_values.py)."""
    CACHE.mkdir(exist_ok=True)
    rows = []
    for season in seasons:
        for week in range(1, LAST_WEEK + 1):
            data = sleeper_get(
                f"https://api.sleeper.app/stats/nfl/{season}/{week}?season_type=regular",
                f"stats_{season}_{week}.json", False,
            )
            for r in data:
                pos = (r.get("player") or {}).get("position")
                st = r.get("stats") or {}
                if pos in POS_IDX and st.get("gp"):
                    rows.append((r["player_id"], season, week, pos, float(st.get("pts_ppr") or 0)))
    return pd.DataFrame(rows, columns=["player_id", "season", "week", "position", "pts"])


def player_seasons(weekly, players, min_games=6):
    """Season-level actual points per game, with age on Sept 1 of that season."""
    ps = (weekly.groupby(["player_id", "season", "position"])
          .agg(ppg=("pts", "mean"), games=("pts", "size")).reset_index())
    ps = ps[ps.games >= min_games]
    birth = players["birth_date"].reindex(ps.player_id).to_numpy()
    ps["age"] = [age_on(b, s) if isinstance(b, str) else np.nan for b, s in zip(birth, ps.season)]
    ps["name"] = players["full_name"].reindex(ps.player_id).to_numpy()
    return ps


def binom_ci(p, n, z=1.96):
    """Normal-approximation 95% half-width for a proportion."""
    return z * np.sqrt(p * (1 - p) / n)
