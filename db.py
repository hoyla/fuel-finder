"""Database operations for Fuel Finder scraper."""

import json
import os
import psycopg2
from psycopg2.extras import execute_values


def get_connection(database_url=None):
    url = database_url or os.environ["DATABASE_URL"]
    return psycopg2.connect(url)


def init_schema(conn):
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(schema_path) as f:
        sql = f.read()
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()


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
                s.get("brand_name"),
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


def insert_fuel_prices(conn, price_records, scrape_run_id):
    """Insert price observations. price_records is the raw API response list."""
    if not price_records:
        return 0
    rows = []
    for station in price_records:
        node_id = station["node_id"]
        for fp in station.get("fuel_prices", []):
            rows.append((
                node_id,
                fp["fuel_type"],
                fp["price"],
                fp.get("price_last_updated"),
                fp.get("price_change_effective_timestamp"),
                scrape_run_id,
            ))
    if not rows:
        return 0
    with conn.cursor() as cur:
        execute_values(
            cur,
            """INSERT INTO fuel_prices
               (node_id, fuel_type, price, price_last_updated,
                price_change_effective_timestamp, scrape_run_id)
               VALUES %s""",
            rows,
            page_size=1000,
        )
    conn.commit()
    return len(rows)
