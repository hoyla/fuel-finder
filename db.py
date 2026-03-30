"""Database operations for Fuel Finder scraper."""

import json
import os
from decimal import Decimal

import psycopg2
from psycopg2.extras import execute_values


def get_connection(database_url=None):
    url = database_url or os.environ["DATABASE_URL"]
    return psycopg2.connect(url)


def init_schema(conn):
    from migrate import run_migrations
    applied = run_migrations(conn)
    if applied:
        import logging
        logging.getLogger(__name__).info("Applied migrations: %s", ", ".join(applied))


def start_scrape_run(conn, run_type="full"):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO scrape_runs (run_type) VALUES (%s) RETURNING id",
            (run_type,),
        )
        run_id = cur.fetchone()[0]
    conn.commit()
    return run_id


def complete_scrape_run(conn, run_id, batches, stations_count, price_count, s3_key=None):
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE scrape_runs
               SET finished_at = NOW(), batches_fetched = %s,
                   stations_count = %s, price_records_count = %s,
                   s3_key = %s, status = 'completed'
             WHERE id = %s""",
            (batches, stations_count, price_count, s3_key, run_id),
        )
    conn.commit()


def fail_scrape_run(conn, run_id, error_message):
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE scrape_runs
               SET finished_at = NOW(), status = 'failed', error_message = %s
             WHERE id = %s""",
            (str(error_message)[:2000], run_id),
        )
    conn.commit()


def upsert_stations(conn, stations):
    if not stations:
        return
    with conn.cursor() as cur:
        values = []
        for s in stations:
            loc = s.get("location", {})
            values.append((
                s["node_id"],
                s["trading_name"],
                (s.get("brand_name") or "").strip() or None,
                s.get("is_same_trading_and_brand_name"),
                s.get("public_phone_number"),
                s.get("temporary_closure", False),
                s.get("permanent_closure"),
                s.get("permanent_closure_date"),
                s.get("is_motorway_service_station", False),
                s.get("is_supermarket_service_station", False),
                loc.get("address_line_1"),
                loc.get("address_line_2"),
                loc.get("city"),
                loc.get("county"),
                loc.get("country"),
                loc.get("postcode"),
                loc.get("latitude"),
                loc.get("longitude"),
                s.get("amenities", []),
                s.get("fuel_types", []),
                json.dumps(s.get("opening_times")) if s.get("opening_times") else None,
            ))
        execute_values(
            cur,
            """INSERT INTO stations (
                node_id, trading_name, brand_name, is_same_trading_and_brand_name,
                public_phone_number, temporary_closure, permanent_closure,
                permanent_closure_date, is_motorway_service_station,
                is_supermarket_service_station, address_line_1, address_line_2,
                city, county, country, postcode, latitude, longitude,
                amenities, fuel_types, opening_times
            ) VALUES %s
            ON CONFLICT (node_id) DO UPDATE SET
                trading_name = EXCLUDED.trading_name,
                brand_name = EXCLUDED.brand_name,
                is_same_trading_and_brand_name = EXCLUDED.is_same_trading_and_brand_name,
                public_phone_number = EXCLUDED.public_phone_number,
                temporary_closure = EXCLUDED.temporary_closure,
                permanent_closure = EXCLUDED.permanent_closure,
                permanent_closure_date = EXCLUDED.permanent_closure_date,
                is_motorway_service_station = EXCLUDED.is_motorway_service_station,
                is_supermarket_service_station = EXCLUDED.is_supermarket_service_station,
                address_line_1 = EXCLUDED.address_line_1,
                address_line_2 = EXCLUDED.address_line_2,
                city = EXCLUDED.city,
                county = EXCLUDED.county,
                country = EXCLUDED.country,
                postcode = EXCLUDED.postcode,
                latitude = EXCLUDED.latitude,
                longitude = EXCLUDED.longitude,
                amenities = EXCLUDED.amenities,
                fuel_types = EXCLUDED.fuel_types,
                opening_times = EXCLUDED.opening_times,
                last_updated = NOW()""",
            values,
            page_size=500,
        )
    conn.commit()


# --- Anomaly detection ---

# Plausible price range in pence per litre.
# Anything outside this is almost certainly a data entry error.
PRICE_FLOOR = Decimal("80.0")    # Below 80p hasn't happened since ~2004
PRICE_CEILING = Decimal("300.0")  # Above 300p would be unprecedented

# A single price change of more than this % is suspicious.
MAX_CHANGE_PCT = Decimal("30.0")


def _detect_anomalies(price, fuel_type, previous_price):
    """Return a list of anomaly flag strings, or None if price looks normal."""
    price = Decimal(str(price))
    flags = []

    if price < PRICE_FLOOR:
        flags.append(f"price_below_floor:{price}<{PRICE_FLOOR}")
    elif price > PRICE_CEILING:
        flags.append(f"price_above_ceiling:{price}>{PRICE_CEILING}")

    # Check for likely decimal place errors (e.g. 14.9 instead of 149.0)
    if price < PRICE_FLOOR and 10 * price >= PRICE_FLOOR:
        flags.append(f"likely_decimal_error:x10_would_be_{10*price}")
    if price > PRICE_CEILING and price / 10 <= PRICE_CEILING:
        flags.append(f"likely_decimal_error:div10_would_be_{price/10}")

    # Large jump from previous price
    if previous_price and previous_price > 0:
        prev = Decimal(str(previous_price))
        change_pct = abs(price - prev) / prev * 100
        if change_pct > MAX_CHANGE_PCT:
            flags.append(f"large_change:{change_pct:.1f}%_from_{previous_price}")

    return flags if flags else None


def insert_fuel_prices(conn, price_records, scrape_run_id):
    """Insert price observations, but only when the price has actually changed.

    Compares each incoming price against the most recent stored price for that
    station + fuel_type. Only inserts a new row if the price differs (or no
    previous record exists). This means every row in fuel_prices represents
    a genuine price change, keeping storage lean and time-series queries simple.

    Each inserted row is checked for anomalies (implausible prices, decimal
    place errors, large jumps). Anomaly flags are stored in the anomaly_flags
    column but the row is still inserted — raw data is preserved, anomalies
    are flagged not filtered.
    """
    if not price_records:
        return 0

    # Build lookup of latest known prices: (node_id, fuel_type) -> price
    # Uses corrected price when available so that anomaly detection and
    # change-detection compare against the *intended* value, not a
    # mis-entered original (e.g. 1.8p that was corrected to 180.0p).
    all_node_ids = list({s["node_id"] for s in price_records})
    latest = {}
    with conn.cursor() as cur:
        cur.execute(
            """SELECT DISTINCT ON (fp.node_id, fp.fuel_type)
                      fp.node_id, fp.fuel_type,
                      COALESCE(pc.corrected_price, fp.price) AS price
                 FROM fuel_prices fp
                 LEFT JOIN price_corrections pc ON pc.fuel_price_id = fp.id
                WHERE fp.node_id = ANY(%s)
             ORDER BY fp.node_id, fp.fuel_type, fp.observed_at DESC""",
            (all_node_ids,),
        )
        for row in cur.fetchall():
            latest[(row[0], row[1])] = row[2]

    # Only keep rows where price has changed (or is new)
    rows = []
    anomaly_count = 0
    for station in price_records:
        node_id = station["node_id"]
        for fp in station.get("fuel_prices", []):
            key = (node_id, fp["fuel_type"])
            new_price = Decimal(str(fp["price"]))
            if key not in latest or latest[key] != new_price:
                previous = latest.get(key)
                flags = _detect_anomalies(float(fp["price"]), fp["fuel_type"], previous)
                if flags:
                    anomaly_count += 1
                rows.append((
                    node_id,
                    fp["fuel_type"],
                    fp["price"],
                    fp.get("price_last_updated"),
                    fp.get("price_change_effective_timestamp"),
                    scrape_run_id,
                    flags,
                ))
    if not rows:
        return 0
    with conn.cursor() as cur:
        execute_values(
            cur,
            """INSERT INTO fuel_prices
               (node_id, fuel_type, price, price_last_updated,
                price_change_effective_timestamp, scrape_run_id, anomaly_flags)
               VALUES %s""",
            rows,
            page_size=1000,
        )
    conn.commit()
    if anomaly_count:
        import logging
        logging.getLogger(__name__).warning(
            "Detected %d anomalous price records in this scrape", anomaly_count
        )
    return len(rows)


def refresh_current_prices(conn):
    """Refresh the current_prices materialised view after a scrape."""
    with conn.cursor() as cur:
        cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY current_prices")
    conn.commit()


def refresh_daily_prices(conn):
    """Upsert today's daily_prices summaries from fuel_prices.

    Re-aggregates today's data so it picks up any new scrape records.
    Runs after each scrape and after price corrections.
    """
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO daily_prices (node_id, fuel_type, price_date, avg_price, min_price, max_price, sample_count)
            SELECT fp.node_id,
                   fp.fuel_type,
                   DATE(fp.observed_at),
                   ROUND(AVG(COALESCE(pc.corrected_price, fp.price))::numeric, 1),
                   ROUND(MIN(COALESCE(pc.corrected_price, fp.price))::numeric, 1),
                   ROUND(MAX(COALESCE(pc.corrected_price, fp.price))::numeric, 1),
                   COUNT(*)
            FROM fuel_prices fp
            LEFT JOIN price_corrections pc ON pc.fuel_price_id = fp.id
            WHERE fp.anomaly_flags IS NULL
              AND DATE(fp.observed_at) >= CURRENT_DATE
            GROUP BY fp.node_id, fp.fuel_type, DATE(fp.observed_at)
            ON CONFLICT (node_id, fuel_type, price_date) DO UPDATE SET
                avg_price = EXCLUDED.avg_price,
                min_price = EXCLUDED.min_price,
                max_price = EXCLUDED.max_price,
                sample_count = EXCLUDED.sample_count
        """)
    conn.commit()


def get_last_scrape_timestamp(conn):
    """Get the finished_at timestamp of the last successful scrape run."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT finished_at FROM scrape_runs
                WHERE status = 'completed'
             ORDER BY finished_at DESC LIMIT 1"""
        )
        row = cur.fetchone()
    return row[0].strftime("%Y-%m-%d %H:%M:%S") if row else None
