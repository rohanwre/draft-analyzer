import csv
import os
import mysql.connector
from config import DB_CONFIG
from advisor import normalize_name, SUFFIXES

def get_db():
    return mysql.connector.connect(**DB_CONFIG)

def clean_position(pos):
    if not pos:
        return None
    for p in ["QB", "RB", "WR", "TE"]:
        if pos.upper().startswith(p):
            return p
    return None

def flip_name(name):
    """Convert 'Last, First' to 'First Last'"""
    if "," in name:
        parts = name.split(",", 1)
        return f"{parts[1].strip()} {parts[0].strip()}"
    return name.strip()

def name_has_suffix(name):
    tokens = name.strip().split()
    return bool(tokens) and tokens[-1].lower().rstrip(".") in SUFFIXES

def load_fp_adp(filepath, season, league_type):
    """Load FantasyPros CSV: Rank, Player, Team, Bye, POS, ..., AVG
    Also handles the FantasyPros Rankings/ECR export shape (PLAYER NAME, RK instead of Player, AVG)"""
    players = {}
    try:
        with open(filepath, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = (row.get("Player") or row.get("PLAYER NAME") or "").strip()
                pos = clean_position(row.get("POS") or "")
                adp = (row.get("AVG") or row.get("RK") or "").strip()
                if not name or not pos or not adp:
                    continue
                try:
                    key = normalize_name(name)
                    players[key] = {
                        "name": name,
                        "position": pos,
                        "adps": [float(adp)]
                    }
                except ValueError:
                    continue
    except FileNotFoundError:
        print(f"  File not found: {filepath}, skipping")
    return players

def load_nfc_adp(filepath, season, league_type):
    """Load NFC TSV: Rank, Player ID, Player (Last First), Team, Position, ADP..."""
    players = {}
    try:
        with open(filepath, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f, delimiter='\t')
            for row in reader:
                raw_name = (row.get("Player") or "").strip()
                name = flip_name(raw_name)
                pos = clean_position(row.get("Position(s)") or "")
                adp = (row.get("ADP") or "").strip()
                if not name or not pos or not adp:
                    continue
                try:
                    key = normalize_name(name)
                    players[key] = {
                        "name": name,
                        "position": pos,
                        "adps": [float(adp)]
                    }
                except ValueError:
                    continue
    except FileNotFoundError:
        print(f"  File not found: {filepath}, skipping")
    return players

def load_ffpc_adp(filepath, season, league_type):
    """Load FFPC CSV: ADP, Position (RB-01 format), Player, Team, FFPC, Sleeper, 10-Team, 12-Team"""
    players = {}
    try:
        with open(filepath, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = (row.get("Player") or "").strip()
                pos = clean_position(row.get("Position") or "")
                adp = (row.get("ADP") or "").strip()
                if not name or not pos or not adp:
                    continue
                try:
                    key = normalize_name(name)
                    players[key] = {
                        "name": name,
                        "position": pos,
                        "adps": [float(adp)]
                    }
                except ValueError:
                    continue
    except FileNotFoundError:
        print(f"  File not found: {filepath}, skipping")
    return players

def merge_sources(named_sources, tiebreak_source=None):
    """Merge multiple player dicts, averaging ADP values across sources equally.
    named_sources: dict of {source_name: player_dict}. If tiebreak_source is given
    (e.g. "sleeper"), that source's own ADP value is carried through separately as
    tiebreak_adp — used only to deterministically order players who land on the exact
    same averaged ADP, not folded into the average itself.
    Merge keys use the same normalize_name() as the rest of the app (suffix-insensitive:
    "James Cook" and "James Cook III" are the same key), so sources that spell a player's
    suffix inconsistently still get averaged together into one player instead of silently
    splitting into two rows. When sources disagree on the suffix, the suffixed spelling
    (more complete) is kept as the display name."""
    merged = {}
    for source_name, source in named_sources.items():
        for key, data in source.items():
            entry = merged.setdefault(key, {
                "name": data["name"], "position": data["position"], "adps": [], "tiebreak": None,
            })
            if name_has_suffix(data["name"]) and not name_has_suffix(entry["name"]):
                entry["name"] = data["name"]
            entry["adps"].extend(data["adps"])
            if source_name == tiebreak_source:
                entry["tiebreak"] = data["adps"][0]

    result = {}
    for key, data in merged.items():
        result[key] = {
            "name": data["name"],
            "position": data["position"],
            "adp": round(sum(data["adps"]) / len(data["adps"]), 2),
            "tiebreak_adp": data["tiebreak"],
        }
    return result

def insert_merged(cursor, merged, season, league_type, league_format="redraft", scoring_type="ppr"):
    # Delete-then-insert instead of upsert-only: a merge-key change (like this fix) can
    # change which player_name ends up stored (e.g. "James Cook" -> "James Cook III"),
    # and ON DUPLICATE KEY UPDATE can't clean up the old spelling's now-stale row since
    # it no longer matches on player_name. load_adp.py is authoritative for these
    # (season, league_type, league_format, scoring_type) rows, so a full replace each
    # run is safe and self-healing.
    cursor.execute(
        "DELETE FROM adp WHERE season = %s AND league_type = %s AND league_format = %s AND scoring_type = %s",
        (season, league_type, league_format, scoring_type),
    )

    inserted = 0
    skipped = 0
    for key, data in merged.items():
        try:
            cursor.execute("""
                INSERT INTO adp (player_name, position, adp, season, league_type, league_format, scoring_type, tiebreak_adp)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE adp = %s, tiebreak_adp = %s
            """, (
                data["name"], data["position"], data["adp"],
                season, league_type, league_format, scoring_type, data["tiebreak_adp"],
                data["adp"], data["tiebreak_adp"],
            ))
            inserted += 1
        except Exception as e:
            skipped += 1
    print(f"  {league_format} {league_type} {scoring_type} season {season}: inserted/updated {inserted}, skipped {skipped}")
    return inserted

ADP_DATA_DIR = "adp_data"

def load_all():
    db = get_db()
    cursor = db.cursor()

    # standard PPR — average FP + NFC
    standard_sources = [
        # (source_name, loader_fn, filepath, season, league_type)
        ("fp",  load_fp_adp,  os.path.join(ADP_DATA_DIR, "fp_adp_2020.csv"),  2020, "standard"),
        ("fp",  load_fp_adp,  os.path.join(ADP_DATA_DIR, "fp_adp_2021.csv"),  2021, "standard"),
        ("fp",  load_fp_adp,  os.path.join(ADP_DATA_DIR, "fp_adp_2022.csv"),  2022, "standard"),
        ("fp",  load_fp_adp,  os.path.join(ADP_DATA_DIR, "fp_adp_2023.csv"),  2023, "standard"),
        ("fp",  load_fp_adp,  os.path.join(ADP_DATA_DIR, "fp_adp_2024.csv"),  2024, "standard"),
        ("fp",  load_fp_adp,  os.path.join(ADP_DATA_DIR, "fp_adp_2025.csv"),  2025, "standard"),
        ("fp",  load_fp_adp,  os.path.join(ADP_DATA_DIR, "fp_adp_2026.csv"),  2026, "standard"),
        ("nfc", load_nfc_adp, os.path.join(ADP_DATA_DIR, "nfc_adp_2026.tsv"), 2026, "standard"),
    ]

    # half-PPR / standard scoring — FantasyPros only so far (their ADP tool only offers
    # PPR/half-PPR boards; there's no standard-scoring ADP board at all, so
    # fp_adp_standard_2026.csv comes from their Expert Consensus Rankings instead - same
    # RK-as-ADP-proxy fallback load_fp_adp already uses for the superflex/dynasty sources
    # below, since that CSV has no AVG column). Single-source, so no averaging needed.
    # NFC/MFL's public ADP feed doesn't expose a scoring-format filter, so it stays PPR-only.
    half_ppr_sources = [
        ("fp", load_fp_adp, os.path.join(ADP_DATA_DIR, "fp_adp_half_ppr_2026.csv"), 2026, "standard"),
    ]
    standard_scoring_sources = [
        ("fp", load_fp_adp, os.path.join(ADP_DATA_DIR, "fp_adp_standard_2026.csv"), 2026, "standard"),
    ]

    # superflex — average FFPC + FantasyPros + Sleeper superflex rankings equally;
    # ties in the averaged ADP defer to Sleeper's own ranking (see tiebreak_source below).
    # Not split by scoring_type (stored as the 'ppr' default, unchanged) - these are
    # already ECR-rank proxies rather than a real ADP metric, so a scoring breakdown here
    # would be more precision than the underlying data actually supports.
    sflex_sources = [
        ("ffpc",    load_ffpc_adp, os.path.join(ADP_DATA_DIR, "ffpc_adp_sf_2026.csv"),    2026, "qb_premium"),
        ("fp",      load_fp_adp,   os.path.join(ADP_DATA_DIR, "fp_adp_sf_2026.csv"),      2026, "qb_premium"),
        ("sleeper", load_ffpc_adp, os.path.join(ADP_DATA_DIR, "sleeper_adp_sf_2026.csv"), 2026, "qb_premium"),
    ]

    # process standard by season
    standard_by_season = {}
    for source_name, loader, filepath, season, league_type in standard_sources:
        data = loader(filepath, season, league_type)
        standard_by_season.setdefault(season, {})[source_name] = data

    print("Loading standard PPR ADP...")
    for season, sources in standard_by_season.items():
        print(f"  Season {season}: merging {len(sources)} source(s)")
        merged = merge_sources(sources)
        insert_merged(cursor, merged, season, "standard", scoring_type="ppr")
        db.commit()

    # half-PPR and standard-scoring ADP - single source each, no merging needed
    print("Loading half-PPR ADP...")
    for source_name, loader, filepath, season, league_type in half_ppr_sources:
        merged = merge_sources({source_name: loader(filepath, season, league_type)})
        insert_merged(cursor, merged, season, league_type, scoring_type="half_ppr")
        db.commit()

    print("Loading standard-scoring ADP...")
    for source_name, loader, filepath, season, league_type in standard_scoring_sources:
        merged = merge_sources({source_name: loader(filepath, season, league_type)})
        insert_merged(cursor, merged, season, league_type, scoring_type="standard")
        db.commit()

    # process superflex by season
    sflex_by_season = {}
    for source_name, loader, filepath, season, league_type in sflex_sources:
        data = loader(filepath, season, league_type)
        sflex_by_season.setdefault(season, {})[source_name] = data

    print("Loading superflex ADP...")
    for season, sources in sflex_by_season.items():
        print(f"  Season {season}: merging {len(sources)} source(s)")
        merged = merge_sources(sources, tiebreak_source="sleeper")
        insert_merged(cursor, merged, season, "qb_premium")
        db.commit()

    # dynasty superflex — average FantasyPros' dynasty/OP rankings export with a
    # DraftSharks dynasty SF board and a Sleeper dynasty PPR ADP slice (see
    # adp_data/draftsharks_adp_dynasty_sf_2026.csv, sleeper_adp_dynasty_ppr_2026.csv; all
    # three happen to already match load_fp_adp's expected Player/POS/AVG-or-RK shape, so
    # no new loader was needed). The Sleeper source only covers a mid/late slice of the
    # board (ADP ~229-301, 73 players, hand-transcribed from a paste with abbreviated
    # first-initial names resolved to full names via last-name+position+first-initial
    # matching against the other two sources, verified against real rosters for the 6
    # ambiguous cases) - merge_sources unions keys, so players outside that slice just
    # average the other two sources, same as any other partial source. Only superflex is
    # covered - real dynasty ADP sources skew heavily superflex, and
    # resolve_adp_league_type's fallback chain (advisor.py) covers a standard-league-type
    # dynasty session by falling back to this data rather than crossing into redraft
    # numbers, which would be a worse mismatch.
    dynasty_sflex_sources = [
        ("fp",          load_fp_adp, os.path.join(ADP_DATA_DIR, "fp_adp_dynasty_sf_2026.csv"),         2026, "qb_premium"),
        ("draftsharks", load_fp_adp, os.path.join(ADP_DATA_DIR, "draftsharks_adp_dynasty_sf_2026.csv"), 2026, "qb_premium"),
        ("sleeper",     load_fp_adp, os.path.join(ADP_DATA_DIR, "sleeper_adp_dynasty_ppr_2026.csv"),    2026, "qb_premium"),
    ]
    dynasty_sflex_by_season = {}
    for source_name, loader, filepath, season, league_type in dynasty_sflex_sources:
        data = loader(filepath, season, league_type)
        dynasty_sflex_by_season.setdefault(season, {})[source_name] = data

    print("Loading dynasty superflex ADP...")
    for season, sources in dynasty_sflex_by_season.items():
        print(f"  Season {season}: merging {len(sources)} source(s)")
        merged = merge_sources(sources)
        insert_merged(cursor, merged, season, "qb_premium", league_format="dynasty")
        db.commit()

    cursor.close()
    db.close()
    print("Done!")

if __name__ == "__main__":
    load_all()