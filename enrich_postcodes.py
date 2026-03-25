"""Enrich station data by looking up postcodes via postcodes.io.

Fetches geographic and administrative data for every unique postcode
in the stations table. Uses the bulk endpoint (100 postcodes per request)
to minimise API calls. Results are cached in the postcode_lookups table.

Usage:
    python enrich_postcodes.py           # look up postcodes not yet in table
    python enrich_postcodes.py --all     # re-lookup all postcodes
"""

import logging
import os
import sys
import time

import psycopg2
from psycopg2.extras import execute_values
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

POSTCODES_IO_URL = "https://api.postcodes.io/postcodes"
BATCH_SIZE = 100  # postcodes.io max per request


def get_connection():
    url = os.environ.get(
        "DATABASE_URL",
        "postgresql://fuelfinder:fuelfinder@localhost:5432/fuelfinder",
    )
    return psycopg2.connect(url)


def get_postcodes_to_lookup(conn, refresh_all=False):
    """Get distinct postcodes from stations that haven't been looked up yet."""
    with conn.cursor() as cur:
        if refresh_all:
            cur.execute(
                "SELECT DISTINCT postcode FROM stations WHERE postcode IS NOT NULL AND postcode != ''"
            )
        else:
            cur.execute("""
                SELECT DISTINCT s.postcode
                FROM stations s
                LEFT JOIN postcode_lookups pl ON pl.postcode = s.postcode
                WHERE s.postcode IS NOT NULL
                  AND s.postcode != ''
                  AND pl.postcode IS NULL
            """)
        return [row[0] for row in cur.fetchall()]


def bulk_lookup(postcodes):
    """Look up a batch of postcodes via postcodes.io bulk endpoint."""
    resp = requests.post(
        POSTCODES_IO_URL,
        json={"postcodes": postcodes},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["result"]


def parse_result(item):
    """Extract the fields we care about from a postcodes.io result."""
    query = item["query"]
    r = item.get("result")
    if r is None:
        return None  # invalid/terminated postcode
    return (
        query,
        r.get("latitude"),
        r.get("longitude"),
        r.get("admin_district"),
        r.get("admin_county"),
        r.get("admin_ward"),
        r.get("parish"),
        r.get("parliamentary_constituency_2024") or r.get("parliamentary_constituency"),
        r.get("region"),          # ONS region — England only
        r.get("country"),
        r.get("ruc21") or r.get("ruc11"),  # prefer 2021 classification
        _extract_ruc_code(r),
        r.get("lsoa") or r.get("lsoa21") or r.get("lsoa11"),
        r.get("msoa") or r.get("msoa21") or r.get("msoa11"),
        r.get("bua"),
        r.get("quality"),
    )


def _extract_ruc_code(r):
    """Extract the rural/urban code from the codes sub-dict."""
    codes = r.get("codes", {})
    return codes.get("ruc21") or codes.get("ruc11")


def upsert_lookups(conn, rows):
    """Insert or update postcode lookup results."""
    if not rows:
        return
    with conn.cursor() as cur:
        execute_values(
            cur,
            """INSERT INTO postcode_lookups (
                postcode, pc_latitude, pc_longitude,
                admin_district, admin_county, admin_ward, parish,
                parliamentary_constituency,
                ons_region, country, rural_urban, rural_urban_code,
                lsoa, msoa, built_up_area, quality
            ) VALUES %s
            ON CONFLICT (postcode) DO UPDATE SET
                pc_latitude = EXCLUDED.pc_latitude,
                pc_longitude = EXCLUDED.pc_longitude,
                admin_district = EXCLUDED.admin_district,
                admin_county = EXCLUDED.admin_county,
                admin_ward = EXCLUDED.admin_ward,
                parish = EXCLUDED.parish,
                parliamentary_constituency = EXCLUDED.parliamentary_constituency,
                ons_region = EXCLUDED.ons_region,
                country = EXCLUDED.country,
                rural_urban = EXCLUDED.rural_urban,
                rural_urban_code = EXCLUDED.rural_urban_code,
                lsoa = EXCLUDED.lsoa,
                msoa = EXCLUDED.msoa,
                built_up_area = EXCLUDED.built_up_area,
                quality = EXCLUDED.quality,
                looked_up_at = NOW()
            """,
            rows,
            page_size=100,
        )
    conn.commit()


def run(refresh_all=False):
    conn = get_connection()
    try:
        postcodes = get_postcodes_to_lookup(conn, refresh_all)
        if not postcodes:
            log.info("All postcodes already looked up — nothing to do")
            return 0

        log.info("Looking up %d postcodes via postcodes.io", len(postcodes))

        total_ok = 0
        total_failed = 0

        for i in range(0, len(postcodes), BATCH_SIZE):
            batch = postcodes[i : i + BATCH_SIZE]
            batch_num = i // BATCH_SIZE + 1
            total_batches = (len(postcodes) + BATCH_SIZE - 1) // BATCH_SIZE

            try:
                results = bulk_lookup(batch)
            except requests.RequestException as e:
                log.error("Batch %d/%d failed: %s", batch_num, total_batches, e)
                total_failed += len(batch)
                time.sleep(2)
                continue

            rows = []
            for item in results:
                parsed = parse_result(item)
                if parsed:
                    rows.append(parsed)
                else:
                    total_failed += 1

            upsert_lookups(conn, rows)
            total_ok += len(rows)

            log.info(
                "Batch %d/%d: %d postcodes resolved",
                batch_num, total_batches, len(rows),
            )

            # Be polite to the free API
            if i + BATCH_SIZE < len(postcodes):
                time.sleep(0.5)

        log.info(
            "Done: %d postcodes enriched, %d failed/invalid",
            total_ok, total_failed,
        )
        return total_ok

    finally:
        conn.close()


if __name__ == "__main__":
    refresh_all = "--all" in sys.argv
    run(refresh_all)
