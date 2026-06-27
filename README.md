# Fantasy Draft Advisor

A CLI-based fantasy football draft tool that analyzes historical draft data from thousands of real Sleeper leagues to recommend picks in real time based on what top-finishing teams actually drafted.

## How It Works

The tool pulls draft and standings data from Sleeper's API across 8, 10, and 12-team standard PPR redraft leagues. It identifies which teams finished as a top-2 seed and analyzes their draft construction round by round. During a live draft, it compares your picks so far against those historical patterns and recommends what position to target next.

Recommendations are based on:

- **Historical pattern matching** — what did top-2-seed teams draft in this round given a similar start
- **ADP value** — which players are available later than their consensus ranking suggests
- **Positional scarcity** — how fast each position is being depleted relative to ADP, adjusted for your league's starting requirements
- **Positional need** — what your roster still needs to fill starting slots, with urgency scaling as rounds run out
- **Combined scoring** — each recommended player gets a score weighted 60% ADP strength and 40% historical trend strength

## Demo

*(add GIF here)*

## Setup

### Requirements
- Python 3.11+
- MySQL 8.0+
- A Sleeper account

### Install dependencies
pip install mysql-connector-python requests python-dotenv

### Configure database
Create a MySQL database and add a `.env` file in the project root:
DB_HOST=localhost

DB_USER=root

DB_PASSWORD=yourpassword

DB_NAME=fantasy_draft_analyzer

### Set up the database schema
Run the following in MySQL:
```sql
CREATE TABLE leagues (
    league_id VARCHAR(20) PRIMARY KEY,
    name VARCHAR(100),
    season INT,
    scoring_type VARCHAR(10),
    total_rosters INT,
    status VARCHAR(20),
    league_size INT,
    roster_positions TEXT,
    league_type VARCHAR(20),
    te_premium TINYINT(1),
    season_type VARCHAR(20)
);

CREATE TABLE rosters (
    roster_id INT,
    league_id VARCHAR(20),
    owner_id VARCHAR(20),
    wins INT,
    losses INT,
    points_for FLOAT,
    final_seed INT,
    made_playoffs BOOLEAN,
    top_two_seed BOOLEAN,
    PRIMARY KEY (roster_id, league_id),
    FOREIGN KEY (league_id) REFERENCES leagues(league_id)
);

CREATE TABLE draft_picks (
    pick_id VARCHAR(50) PRIMARY KEY,
    draft_id VARCHAR(20),
    league_id VARCHAR(20),
    owner_id VARCHAR(20),
    round INT,
    pick_no INT,
    position VARCHAR(5),
    player_name VARCHAR(100),
    draft_slot INT,
    FOREIGN KEY (league_id) REFERENCES leagues(league_id)
);

CREATE TABLE adp (
    id INT AUTO_INCREMENT PRIMARY KEY,
    player_name VARCHAR(100),
    position VARCHAR(5),
    adp FLOAT,
    season INT,
    UNIQUE KEY unique_player_season (player_name, season)
);
```

### Seed data

**1. Pull league data from a Sleeper username:**
python fetch_data.py

**2. Crawl outward to find more leagues:**
python crawler.py
Run once per season year (2023, 2024, 2025). Targets 2000 leagues per league size (8, 10, 12 team).

**3. Backfill league settings:**
python fetch_roster_settings.py

**4. Load ADP data:**

Download PPR ADP CSVs from FantasyPros for each season and save as `fp_adp_2023.csv`, `fp_adp_2024.csv` etc., then:
python load_adp.py

**5. Calculate draft slots:**
```sql
UPDATE draft_picks dp
JOIN leagues l ON dp.league_id = l.league_id
SET dp.draft_slot = ((dp.pick_no - 1) % l.total_rosters) + 1
WHERE dp.draft_slot IS NULL;
```

### Run the advisor
python advisor.py

## Project Structure
draft-analyzer/

├── advisor.py               # main CLI draft tool

├── fetch_data.py            # pulls league/roster/draft data from Sleeper

├── crawler.py               # crawls Sleeper users to collect league data

├── fetch_roster_settings.py # backfills league settings

├── load_adp.py              # loads FantasyPros ADP CSVs

├── fetch_adp.py             # pulls current season ADP from Sleeper

├── config.py                # database config via dotenv

├── fp_adp_2023.csv

├── fp_adp_2024.csv

├── fp_adp_2025.csv

├── fp_adp_2026.csv

└── .env                     # not committed

## Data

This tool works best with a large dataset of completed seasons. The included ADP CSVs cover 2023-2026. League draft and standings data must be crawled from Sleeper using the scripts above — the database is not included in this repo.

## License

MIT License — free to use with attribution. The analysis logic and data pipeline are original work; please credit if you build on this.
