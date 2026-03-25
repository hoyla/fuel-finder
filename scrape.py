"""Fuel Finder scraper — fetches fuel prices and station data, stores in PostgreSQL, backs up to S3."""

import json
import logging
import os
from datetime import datetime, timezone

import boto3
from api_client import FuelFinderClient
from db import (
    get_connection,
    init_schema,
    start_scrape_run,
    complete_scrape_run,
    fail_scrape_run,
    upsert_stations,
    insert_fuel_prices,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


def upload_to_s3(data, key, bucket=None):
    bucket = bucket or os.environ.get("S3_BUCKET", "fuel-finder-raw")
    region = os.environ.get("AWS_REGION", "eu-west-1")
    s3 = boto3.client("s3", region_name=region)
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(data, default=str),
        ContentType="application/json",
    )
    log.info("Uploaded %s to s3://%s", key, bucket)
    return key


def run_scrape(include_stations=True):
    now = datetime.now(timezone.utc)
    timestamp_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    skip_s3 = os.environ.get("SKIP_S3", "false").lower() == "true"

    log.info("Starting fuel finder scrape at %s", timestamp_str)

    client = FuelFinderClient()
    conn = get_connection()

    try:
        init_schema(conn)
        run_id = start_scrape_run(conn, run_type="full")
        log.info("Scrape run %d started", run_id)

        # Fetch station info (less volatile, but needed for foreign key on first run)
        if include_stations:
            log.info("Fetching station info...")
            stations, station_batches = client.get_all_stations()
            log.info("Fetched %d stations across %d batches", len(stations), station_batches)
            upsert_stations(conn, stations)
            log.info("Upserted stations to database")

            if not skip_s3:
                s3_stations_key = f"stations/{now.strftime('%Y/%m/%d')}/{timestamp_str}.json"
                upload_to_s3(stations, s3_stations_key)

        # Fetch fuel prices
        log.info("Fetching fuel prices...")
        prices, price_batches = client.get_all_fuel_prices()
        log.info("Fetched prices for %d stations across %d batches", len(prices), price_batches)

        # Ensure stations exist for any new node_ids in price data
        if not include_stations:
            _ensure_stations_exist(conn, client, prices)

        price_count = insert_fuel_prices(conn, prices, run_id)
        log.info("Inserted %d price records", price_count)

        # S3 backup of raw price data
        s3_key = None
        if not skip_s3:
            s3_key = f"prices/{now.strftime('%Y/%m/%d')}/{timestamp_str}.json"
            upload_to_s3(prices, s3_key)

        complete_scrape_run(conn, run_id, price_batches, len(prices), price_count, s3_key)
        log.info("Scrape run %d completed successfully", run_id)
        return {"run_id": run_id, "stations": len(prices), "prices": price_count}

    except Exception as e:
        log.exception("Scrape failed: %s", e)
        try:
            fail_scrape_run(conn, run_id, e)
        except Exception:
            pass
        raise
    finally:
        conn.close()


def _ensure_stations_exist(conn, client, price_records):
    """If we skipped the full station fetch, make sure referenced stations exist."""
    node_ids = {r["node_id"] for r in price_records}
    with conn.cursor() as cur:
        cur.execute("SELECT node_id FROM stations WHERE node_id = ANY(%s)", (list(node_ids),))
        existing = {row[0] for row in cur.fetchall()}
    missing = node_ids - existing
    if missing:
        log.info("%d new stations detected, fetching station info...", len(missing))
        stations, _ = client.get_all_stations()
        upsert_stations(conn, stations)


if __name__ == "__main__":
    run_scrape()
