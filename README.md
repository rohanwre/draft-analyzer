\# Fantasy Draft Advisor



A CLI-based fantasy football draft tool that analyzes historical draft data from thousands of real Sleeper leagues to recommend picks in real time based on what top-finishing teams actually drafted.



\## How It Works



The tool pulls draft and standings data from Sleeper's API across 8, 10, and 12-team standard PPR redraft leagues. It identifies which teams finished as a top-2 seed and analyzes their draft construction round by round. During a live draft, it compares your picks so far against those historical patterns and recommends what position to target next.



Recommendations are based on:



\- \*\*Historical pattern matching\*\* — what did top-2-seed teams draft in this round given a similar start

\- \*\*ADP value\*\* — which players are available later than their consensus ranking suggests

\- \*\*Positional scarcity\*\* — how fast each position is being depleted relative to ADP, adjusted for your league's starting requirements

\- \*\*Positional need\*\* — what your roster still needs to fill starting slots, with urgency scaling as rounds run out

\- \*\*Combined scoring\*\* — each recommended player gets a score weighted 60% ADP strength and 40% historical trend strength



\## Demo



\*(add GIF here)\*



\## Setup



\### Requirements

\- Python 3.11+

\- MySQL 8.0+

\- A Sleeper account



\### Install dependencies

pip install mysql-connector-python requests python-dotenv



\### Configure database

Create a MySQL database and add a `.env` file in the project root:

DB\_HOST=localhost



DB\_USER=root



DB\_PASSWORD=yourpassword



DB\_NAME=fantasy\_draft\_analyzer



\### Set up the database schema

Run the following in MySQL:

```sql

CREATE TABLE leagues (

&#x20;   league\_id VARCHAR(20) PRIMARY KEY,

&#x20;   name VARCHAR(100),

&#x20;   season INT,

&#x20;   scoring\_type VARCHAR(10),

&#x20;   total\_rosters INT,

&#x20;   status VARCHAR(20),

&#x20;   league\_size INT,

&#x20;   roster\_positions TEXT,

&#x20;   league\_type VARCHAR(20),

&#x20;   te\_premium TINYINT(1),

&#x20;   season\_type VARCHAR(20)

);



CREATE TABLE rosters (

&#x20;   roster\_id INT,

&#x20;   league\_id VARCHAR(20),

&#x20;   owner\_id VARCHAR(20),

&#x20;   wins INT,

&#x20;   losses INT,

&#x20;   points\_for FLOAT,

&#x20;   final\_seed INT,

&#x20;   made\_playoffs BOOLEAN,

&#x20;   top\_two\_seed BOOLEAN,

&#x20;   PRIMARY KEY (roster\_id, league\_id),

&#x20;   FOREIGN KEY (league\_id) REFERENCES leagues(league\_id)

);



CREATE TABLE draft\_picks (

&#x20;   pick\_id VARCHAR(50) PRIMARY KEY,

&#x20;   draft\_id VARCHAR(20),

&#x20;   league\_id VARCHAR(20),

&#x20;   owner\_id VARCHAR(20),

&#x20;   round INT,

&#x20;   pick\_no INT,

&#x20;   position VARCHAR(5),

&#x20;   player\_name VARCHAR(100),

&#x20;   draft\_slot INT,

&#x20;   FOREIGN KEY (league\_id) REFERENCES leagues(league\_id)

);



CREATE TABLE adp (

&#x20;   id INT AUTO\_INCREMENT PRIMARY KEY,

&#x20;   player\_name VARCHAR(100),

&#x20;   position VARCHAR(5),

&#x20;   adp FLOAT,

&#x20;   season INT,

&#x20;   league\_type VARCHAR(20) NOT NULL DEFAULT 'standard',

&#x20;   UNIQUE KEY unique\_player\_season\_type (player\_name, season, league\_type)

);

```



\### Seed data



\*\*1. Pull league data from a Sleeper username:\*\*

python fetch\_data.py



\*\*2. Crawl outward to find more leagues:\*\*

python crawler.py

Run once per season year (2023, 2024, 2025). Targets 2000 leagues per league size (8, 10, 12 team).



\*\*3. Backfill league settings:\*\*

python fetch\_roster\_settings.py



\*\*4. Load ADP data:\*\*



Download PPR ADP CSVs from FantasyPros for each season and save as `fp\_adp\_2023.csv`, `fp\_adp\_2024.csv` etc., then:

python load\_adp.py



\*\*5. Calculate draft slots:\*\*

```sql

UPDATE draft\_picks dp

JOIN leagues l ON dp.league\_id = l.league\_id

SET dp.draft\_slot = ((dp.pick\_no - 1) % l.total\_rosters) + 1

WHERE dp.draft\_slot IS NULL;

```



\### Run the advisor

python advisor.py



\## Project Structure

draft-analyzer/



├── advisor.py               # main CLI draft tool



├── fetch\_data.py            # pulls league/roster/draft data from Sleeper



├── crawler.py               # crawls Sleeper users to collect league data



├── fetch\_roster\_settings.py # backfills league settings



├── load\_adp.py              # loads FantasyPros ADP CSVs



├── fetch\_adp.py             # pulls current season ADP from Sleeper



├── config.py                # database config via dotenv



├── fp\_adp\_2023.csv



├── fp\_adp\_2024.csv



├── fp\_adp\_2025.csv



├── fp\_adp\_2026.csv



└── .env                     # not committed



\## Data



This tool works best with a large dataset of completed seasons. The included ADP CSVs cover 2023-2026. League draft and standings data must be crawled from Sleeper using the scripts above — the database is not included in this repo.



\## License



MIT License — free to use with attribution. The analysis logic and data pipeline are original work; please credit if you build on this.

