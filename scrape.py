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
    refresh_current_prices,
    get_last_scrape_timestamp,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


def upload_to_s3(data, key, bucket=None):
    """Back up raw API response to S3 for internal audit/replay only.

    The bucket has all public access blocked (BlockPublicAcls,
    IgnorePublicAcls, BlockPublicPolicy, RestrictPublicBuckets).
    Raw GOV.UK Fuel Finder data must not be redistributed per the
    developer guidelines — this copy is retained solely for internal
    debugging, reprocessing, and data-integrity checks.
    """
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


def run_scrape(mode="auto"):
    """Run a scrape.

    mode:
        'full'        — fetch all batches of prices + stations
        'incremental' — fetch only prices changed since last successful scrape
        'auto'        — incremental if a previous scrape exists, otherwise full
    """
    now = datetime.now(timezone.utc)
    timestamp_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    skip_s3 = os.environ.get("SKIP_S3", "false").lower() == "true"

    log.info("Starting fuel finder scrape at %s (mode=%s)", timestamp_str, mode)

    client = FuelFinderClient()
    conn = get_connection()

    try:
        init_schema(conn)

        # Decide run type
        since_timestamp = None
        if mode == "auto":
            since_timestamp = get_last_scrape_timestamp(conn)
            run_type = "incremental" if since_timestamp else "full"
        elif mode == "incremental":
            since_timestamp = get_last_scrape_timestamp(conn)
            if not since_timestamp:
                log.warning("No previous scrape found, falling back to full")
            run_type = "incremental" if since_timestamp else "full"
        else:
            run_type = "full"

        run_id = start_scrape_run(conn, run_type=run_type)
        log.info("Scrape run %d started (type=%s, since=%s)", run_id, run_type, since_timestamp)

        # Fetch station info on full runs (needed for foreign keys)
        if run_type == "full":
            log.info("Fetching station info...")
            stations, station_batches = client.get_all_stations()
            log.info("Fetched %d stations across %d batches", len(stations), station_batches)
            upsert_stations(conn, stations)
            log.info("Upserted stations to database")

            if not skip_s3:
                s3_stations_key = f"stations/{now.strftime('%Y/%m/%d')}/{timestamp_str}.json"
                upload_to_s3(stations, s3_stations_key)

        # Fetch fuel prices (incremental uses since_timestamp)
        log.info("Fetching fuel prices%s...", f" since {since_timestamp}" if since_timestamp else "")
        prices, price_batches = client.get_all_fuel_prices(since_timestamp)
        log.info("Fetched prices for %d stations across %d batches", len(prices), price_batches)

        # Ensure stations exist for any new node_ids in price data
        if run_type == "incremental":
            _ensure_stations_exist(conn, client, prices)

        price_count = insert_fuel_prices(conn, prices, run_id)
        log.info("Inserted %d changed price records (out of %d stations fetched)", price_count, len(prices))

        # Refresh the current_prices snapshot view
        refresh_current_prices(conn)
        log.info("Refreshed current_prices materialised view")

        # Enrich any new postcodes via postcodes.io
        try:
            from enrich_postcodes import run as enrich_run
            enriched = enrich_run()
            if enriched:
                log.info("Enriched %d new postcodes via postcodes.io", enriched)
                refresh_current_prices(conn)
        except Exception as e:
            log.warning("Postcode enrichment failed (non-fatal): %s", e)

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
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "auto"
    run_scrape(mode=mode)
