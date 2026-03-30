#!/usr/bin/env python3
"""Import historical fuel price data from a CSV file into the fuel_prices table.

The CSV has columns: id, node_id, fuel_type, price_pence, recorded_at, source_updated_at

Price values in the CSV use two different scales:
  - Whole pence (e.g. 126 = 126.0p) for values < 500
  - Tenths of a penny (e.g. 1339 = 133.9p) for values >= 500

Usage:
    # Dry-run (shows what would be imported, no DB changes)
    python import_historical.py historical_prices.csv --dry-run

    # Import into local dev database
    python import_historical.py historical_prices.csv

    # Import into a specific database
    python import_historical.py historical_prices.csv --database-url "postgres://..."
"""

import argparse
import csv
import logging
import sys
from datetime import datetime, timezone
from decimal import Decimal

import psycopg2
from psycopg2.extras import execute_values

from db import get_connection, _detect_anomalies, refresh_current_prices

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

# Prices above this threshold are integer-encoded tenths of a penny (e.g. 1339 = 133.9p).
# Normal UK fuel prices are ~100–300 ppl, so anything > 300 is almost certainly this format.
TENTHS_THRESHOLD = Decimal("300")


def normalize_price(raw_price_pence):
    """Convert CSV price_pence to DB format (Decimal, pence per litre).

    The CSV mixes three formats:
      - Decimal pence:        "133.9"  → 133.9p  (most common)
      - Whole-number pence:   "126"    → 126.0p
      - Tenths-of-a-penny:   "1339"   → 133.9p  (value > 300, divide by 10)
    """
    val = Decimal(raw_price_pence).quantize(Decimal("0.1"))
    if val > TENTHS_THRESHOLD:
        # Integer-encoded tenths: 1339 -> 133.9
        val = val / 10
    return val


def read_csv(csv_path):
    """Read and parse the CSV, returning a list of row dicts with normalized prices."""
    rows = []
    with open(csv_path, "r", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append({
                "node_id": row["node_id"],
                "fuel_type": row["fuel_type"],
                "price": normalize_price(row["price_pence"]),
                "raw_price_pence": row["price_pence"],
                "recorded_at": row["recorded_at"],
                "source_updated_at": row["source_updated_at"],
            })
    return rows


def deduplicate_csv(rows):
    """Remove consecutive identical prices per (node_id, fuel_type).

    Sorts by (node_id, fuel_type, source_updated_at) then keeps only rows
    where the price differs from the previous row for that station+fuel.
    This mirrors the existing scraper's change-only insert semantics.
    """
    rows.sort(key=lambda r: (r["node_id"], r["fuel_type"], r["source_updated_at"]))

    deduped = []
    last_price = {}
    for row in rows:
        key = (row["node_id"], row["fuel_type"])
        if key not in last_price or last_price[key] != row["price"]:
            deduped.append(row)
            last_price[key] = row["price"]

    return deduped


def check_station_coverage(conn, node_ids):
    """Check which node_ids from the CSV exist in the stations table."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT node_id FROM stations WHERE node_id = ANY(%s)",
            (list(node_ids),),
        )
        return {row[0] for row in cur.fetchall()}


def filter_existing_prices(conn, rows):
    """Remove rows that duplicate an already-stored price_last_updated for the same key.

    This prevents re-inserting data if the import is run multiple times.
    Uses a SQL-level timestamp cast for reliable timezone-aware comparison.
    """
    all_node_ids = list({r["node_id"] for r in rows})
    if not all_node_ids:
        return rows

    existing = set()
    with conn.cursor() as cur:
        cur.execute(
            """SELECT node_id, fuel_type, price_last_updated
                 FROM fuel_prices
                WHERE node_id = ANY(%s)
                  AND price_last_updated IS NOT NULL""",
            (all_node_ids,),
        )
        for row in cur.fetchall():
            # Store as (node_id, fuel_type, epoch_seconds) for reliable comparison
            existing.add((row[0], row[1], row[2].timestamp()))

    filtered = []
    skipped = 0
    for row in rows:
        # Parse the CSV timestamp to epoch for comparison
        ts_str = row["source_updated_at"]
        # Handle both "...Z" and "...+00:00" suffixes
        ts_str_norm = ts_str.replace("Z", "+00:00")
        try:
            epoch = datetime.fromisoformat(ts_str_norm).timestamp()
        except ValueError:
            epoch = None

        key = (row["node_id"], row["fuel_type"], epoch)
        if key in existing:
            skipped += 1
        else:
            filtered.append(row)

    if skipped:
        log.info("Skipped %d rows already present in database", skipped)
    return filtered


def build_insert_rows(rows, scrape_run_id):
    """Build the final tuples for INSERT, running anomaly detection on each row."""
    insert_rows = []
    anomaly_count = 0

    # Build a running tally of "previous price" for anomaly detection within the import
    last_price = {}
    for row in rows:
        key = (row["node_id"], row["fuel_type"])
        previous = last_price.get(key)

        flags = _detect_anomalies(float(row["price"]), row["fuel_type"], previous)
        if flags:
            anomaly_count += 1

        insert_rows.append((
            row["node_id"],
            row["fuel_type"],
            row["price"],
            row["source_updated_at"],   # price_last_updated
            row["source_updated_at"],   # price_change_effective_timestamp
            row["recorded_at"],         # observed_at
            scrape_run_id,
            flags,
        ))
        last_price[key] = float(row["price"])

    return insert_rows, anomaly_count


def import_csv_to_db(csv_path, database_url=None, dry_run=False, batch_size=5000):
    """Main import function."""
    # 1. Read and parse CSV
    log.info("Reading CSV: %s", csv_path)
    rows = read_csv(csv_path)
    log.info("Read %d rows from CSV", len(rows))

    if not rows:
        log.warning("CSV is empty, nothing to import")
        return

    # 2. Normalize and deduplicate within CSV
    deduped = deduplicate_csv(rows)
    log.info(
        "After deduplication: %d price changes from %d raw rows (%.0f%% reduction)",
        len(deduped), len(rows), (1 - len(deduped) / len(rows)) * 100,
    )

    # Show price format stats
    tenths_encoded = sum(1 for r in rows if Decimal(str(r["raw_price_pence"])) > TENTHS_THRESHOLD)
    log.info("Price formats: %d normal, %d tenths-of-penny (divided by 10)", len(rows) - tenths_encoded, tenths_encoded)

    # 3. Connect to database
    conn = get_connection(database_url)
    try:
        # 4. Check station coverage
        csv_node_ids = {r["node_id"] for r in deduped}
        existing_stations = check_station_coverage(conn, csv_node_ids)
        missing = csv_node_ids - existing_stations
        if missing:
            log.warning(
                "%d of %d stations in CSV not found in stations table — their rows will be skipped",
                len(missing), len(csv_node_ids),
            )
            deduped = [r for r in deduped if r["node_id"] in existing_stations]
            log.info("After filtering missing stations: %d rows", len(deduped))

        if not deduped:
            log.warning("No importable rows remaining")
            return

        # 5. Filter out prices already in the database (idempotency)
        deduped = filter_existing_prices(conn, deduped)
        if not deduped:
            log.info("All rows already present in database — nothing to import")
            return

        log.info("Rows to import: %d", len(deduped))

        if dry_run:
            log.info("DRY RUN — no changes will be made")
            # Show a sample
            for row in deduped[:10]:
                log.info(
                    "  Would insert: %s %s %.1fp (updated %s, observed %s)",
                    row["node_id"][:12] + "…",
                    row["fuel_type"],
                    row["price"],
                    row["source_updated_at"],
                    row["recorded_at"],
                )
            if len(deduped) > 10:
                log.info("  ... and %d more rows", len(deduped) - 10)
            return

        # 6. Create scrape_run record for the import
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO scrape_runs (run_type, status)
                   VALUES ('historical_import', 'running')
                   RETURNING id""",
            )
            run_id = cur.fetchone()[0]
        conn.commit()
        log.info("Created scrape run #%d (type=historical_import)", run_id)

        # 7. Build rows with anomaly detection
        insert_rows, anomaly_count = build_insert_rows(deduped, run_id)
        if anomaly_count:
            log.warning("Detected %d anomalous price records", anomaly_count)

        # 8. Batch insert
        inserted = 0
        with conn.cursor() as cur:
            for i in range(0, len(insert_rows), batch_size):
                batch = insert_rows[i : i + batch_size]
                execute_values(
                    cur,
                    """INSERT INTO fuel_prices
                       (node_id, fuel_type, price, price_last_updated,
                        price_change_effective_timestamp, observed_at,
                        scrape_run_id, anomaly_flags)
                       VALUES %s""",
                    batch,
                    page_size=1000,
                )
                inserted += len(batch)
                log.info("Inserted batch: %d/%d rows", inserted, len(insert_rows))
        conn.commit()

        # 9. Complete the scrape run
        unique_stations = len({r[0] for r in insert_rows})
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE scrape_runs
                   SET finished_at = NOW(),
                       stations_count = %s,
                       price_records_count = %s,
                       status = 'completed'
                 WHERE id = %s""",
                (unique_stations, inserted, run_id),
            )
        conn.commit()

        log.info(
            "Import complete: %d price records for %d stations (scrape run #%d)",
            inserted, unique_stations, run_id,
        )

        # 10. Refresh materialised view
        log.info("Refreshing current_prices materialised view...")
        refresh_current_prices(conn)
        log.info("Materialised view refreshed")

    except Exception:
        log.exception("Import failed")
        conn.rollback()
        raise
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Import historical fuel prices from CSV into the database",
    )
    parser.add_argument("csv_file", help="Path to the historical prices CSV file")
    parser.add_argument(
        "--database-url",
        help="PostgreSQL connection URL (default: DATABASE_URL env var)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and validate without writing to the database",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=5000,
        help="Number of rows per INSERT batch (default: 5000)",
    )
    args = parser.parse_args()

    import_csv_to_db(
        csv_path=args.csv_file,
        database_url=args.database_url,
        dry_run=args.dry_run,
        batch_size=args.batch_size,
    )


if __name__ == "__main__":
    main()
