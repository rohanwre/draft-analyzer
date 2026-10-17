"""
Exports the columns build_trend_stats_spark.py needs from the local-only raw MySQL
tables (draft_picks, rosters, leagues) to Parquet, ready to upload to a Databricks
Unity Catalog volume.

Why this exists: the raw tables are deliberately never shipped anywhere (see
build_trend_stats.py / the two-tier DB split), so a Databricks job has nothing to read
until they're exported once. Only the six/four/ten columns the replay actually uses
are exported, so even the ~16M-row draft_picks table is a few hundred MB of Parquet
rather than the ~8.6GB it occupies in MySQL. Read-only against MySQL.

Usage:
    python databricks/export_raw_parquet.py --out-dir ./databricks_export
    databricks fs cp -r ./databricks_export dbfs:/Volumes/main/draft_analyzer/raw

Layout produced (what build_trend_stats_spark.py --raw-path expects):
    <out-dir>/draft_picks/part-00000.parquet ...
    <out-dir>/rosters/part-00000.parquet
    <out-dir>/leagues/part-00000.parquet
"""
import argparse
import os
import sys

import mysql.connector
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import DB_CONFIG  # noqa: E402

# table -> [(column, arrow type)]. Explicit types (rather than letting pandas infer)
# so nullable ints stay ints - pandas would silently turn them into floats.
TABLES = {
    "draft_picks": [
        ("league_id", pa.string()), ("owner_id", pa.string()), ("draft_slot", pa.int32()),
        ("round", pa.int32()), ("pick_no", pa.int32()), ("position", pa.string()),
    ],
    "rosters": [
        ("league_id", pa.string()), ("owner_id", pa.string()),
        ("top_pct_optimal", pa.int8()), ("sustained_success_rate", pa.float64()),
    ],
    "leagues": [
        ("league_id", pa.string()), ("season", pa.int32()), ("season_type", pa.string()),
        ("league_size", pa.int32()), ("league_type", pa.string()), ("te_premium", pa.int8()),
        ("league_format", pa.string()), ("scoring_type", pa.string()),
        ("lineage_id", pa.string()), ("season_index", pa.int32()),
    ],
}
CHUNK_ROWS = 1_000_000


def export_table(db, table, columns, out_dir):
    table_dir = os.path.join(out_dir, table)
    os.makedirs(table_dir, exist_ok=True)
    schema = pa.schema(columns)

    # buffered=False streams rows from the server instead of materializing the full
    # result set - draft_picks alone is 16M rows.
    cursor = db.cursor(buffered=False)
    cursor.execute(f"SELECT {', '.join(name for name, _ in columns)} FROM {table}")

    part, total = 0, 0
    while True:
        rows = cursor.fetchmany(CHUNK_ROWS)
        if not rows:
            break
        arrays = [pa.array(col, type=typ) for col, (_, typ) in zip(zip(*rows), columns)]
        pq.write_table(pa.Table.from_arrays(arrays, schema=schema),
                       os.path.join(table_dir, f"part-{part:05d}.parquet"))
        part += 1
        total += len(rows)
        print(f"  {table}: {total:,} rows written ({part} file{'s' if part != 1 else ''})")
    cursor.close()
    if part == 0:
        # Spark can't infer a schema from an empty directory - write an empty file.
        pq.write_table(schema.empty_table(), os.path.join(table_dir, "part-00000.parquet"))
    return total


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()

    db = mysql.connector.connect(**DB_CONFIG)
    try:
        for table, columns in TABLES.items():
            print(f"Exporting {table}...")
            export_table(db, table, columns, args.out_dir)
    finally:
        db.close()
    print(f"\nDone. Upload {args.out_dir} to a Unity Catalog volume, then run "
          f"build_trend_stats_spark.py --raw-path <that volume path>.")


if __name__ == "__main__":
    main()
