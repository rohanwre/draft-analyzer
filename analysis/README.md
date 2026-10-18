# Analysis notebooks

Exploratory analysis of the crawled Sleeper data with pandas, NumPy and matplotlib. Each notebook
is committed with its outputs, so the charts and tables render on GitHub without running anything.

| Notebook | Question | Data |
|---|---|---|
| [01_draft_strategy](01_draft_strategy.ipynb) | What do playoff teams draft? Opening picks, early RB count, QB timing, draft slot, season-to-season stability | 118k teams in 10.6k 12-team PPR redraft leagues |
| [02_projection_accuracy](02_projection_accuracy.ipynb) | How accurate are Sleeper's projections, and does player history beat them? Out-of-sample backtest | 33.7k player-games, 2020-2025 |
| [03_aging_curves](03_aging_curves.ipynb) | How does production change with age, and how often do players wash out? | 2.8k player-seasons of box-score stats |

These findings feed the product: 01 is the signal behind the draft room, and 02 and 03 back the
trade desk's player values and dynasty aging curves (`build_trade_values.py`).

## Running them

```
python -m pip install -r requirements-analysis.txt
python -m jupyter lab analysis/
```

They need the local MySQL (`.env` DB creds, same as the rest of the repo). Box-score stats are
downloaded from Sleeper's public API on first run and cached in `trade_cache/`. Large SQL
aggregations are cached in `analysis/.cache/` (gitignored), so delete that folder after a new crawl.

To re-run a notebook and save its outputs in place:

```
python -m jupyter nbconvert --to notebook --execute --inplace analysis/01_draft_strategy.ipynb
```

`common.py` holds the shared loaders (`query`, `load_weekly_actuals`, `player_seasons`) and the
chart style, which matches the site's paper-and-ink look.
