"""FastAPI backend for the Fuel Finder web UI.

Provides read-only API endpoints over the PostgreSQL database.
Auth is stubbed out for future use (JWT / API key).
"""

import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Optional

import statistics

import boto3
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import SimpleConnectionPool
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Database connection pool
# ---------------------------------------------------------------------------

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://fuelfinder:fuelfinder@localhost:5432/fuelfinder",
)

_pool = SimpleConnectionPool(
    minconn=2,
    maxconn=int(os.environ.get("DB_POOL_MAX", "10")),
    dsn=DATABASE_URL,
    cursor_factory=RealDictCursor,
)


def get_db():
    conn = _pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SET work_mem = '16MB'")
        yield conn
    finally:
        _pool.putconn(conn)


# ---------------------------------------------------------------------------
# Auth — Cognito JWT / API key / no-auth (see auth.py)
# ---------------------------------------------------------------------------

from auth import require_auth, require_admin, require_editor, get_auth_config, get_user_role, resolve_role


# ---------------------------------------------------------------------------
# Run pending migrations on startup
# ---------------------------------------------------------------------------

import migrate

_migrate_conn = psycopg2.connect(DATABASE_URL)
try:
    applied = migrate.run_migrations(_migrate_conn)
    for name in applied:
        print(f"  Applied migration: {name}")
finally:
    _migrate_conn.close()

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Fuel Finder", version="1.0.0")

# CORS configuration: explicit allowlist for production, wildcard for local/staging.
import_env = os.environ.get("ENVIRONMENT", "local").strip().lower()
if import_env == "local":
    CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "*").split(",")
else:
    # Production: require explicit CORS_ORIGINS, default to localhost only for safety.
    CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "localhost").split(",")
    if "*" in CORS_ORIGINS:
        raise RuntimeError("Wildcard CORS origins are not allowed in production. Set CORS_ORIGINS explicitly.")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request bodies for admin endpoints
# ---------------------------------------------------------------------------

class BrandAliasBody(BaseModel):
    raw_brand_name: str
    canonical_brand: str

class BrandCategoryBody(BaseModel):
    canonical_brand: str
    forecourt_type: str

class StationOverrideBody(BaseModel):
    node_id: str
    canonical_brand: str
    notes: Optional[str] = None

class PriceCorrectionBody(BaseModel):
    fuel_price_id: int
    corrected_price: float
    reason: Optional[str] = None

class BatchCorrectionsBody(BaseModel):
    corrections: list[PriceCorrectionBody]

class PostcodeCoordsBody(BaseModel):
    latitude: float
    longitude: float


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    """Health check for ECS / load balancer."""
    return {"status": "ok"}


@app.get("/auth/config")
def auth_config():
    """Return auth mode so the frontend can adapt."""
    return get_auth_config()


@app.get("/auth/me")
def auth_me(request: Request, _auth=Depends(require_auth)):
    """Return the current user's role for frontend permission gating."""
    from auth import get_current_user
    api_key = request.headers.get("x-api-key", "")
    real_role = get_user_role(request, api_key)
    override = request.headers.get("x-role-override", "").lower()
    effective = override if override in ("editor", "readonly") and real_role == "admin" else real_role
    return {
        "role": effective,
        "real_role": real_role,
        "email": get_current_user(request),
    }


@app.get("/api/summary")
def summary(db=Depends(get_db), _auth=Depends(require_auth)):
    """Dashboard headline numbers."""
    with db.cursor() as cur:
        cur.execute("""
            SELECT
                fuel_type,
                fuel_name,
                ROUND(AVG(price) FILTER (WHERE NOT price_is_outlier)::numeric, 1) AS avg_price,
                MIN(price) FILTER (WHERE NOT price_is_outlier) AS min_price,
                MAX(price) FILTER (WHERE NOT price_is_outlier) AS max_price,
                COUNT(*) FILTER (WHERE NOT price_is_outlier) AS station_count,
                COUNT(*) FILTER (WHERE price_is_outlier) AS outliers_excluded
            FROM current_prices
            WHERE NOT temporary_closure
            GROUP BY fuel_type, fuel_name
            ORDER BY fuel_type
        """)
        by_fuel = cur.fetchall()

        cur.execute("""
            SELECT COUNT(DISTINCT node_id) AS total_stations,
                   COUNT(*) AS total_prices
            FROM current_prices
        """)
        totals = cur.fetchone()

        cur.execute("""
            SELECT
                CASE
                    WHEN UPPER(country) IN ('ENGLAND') THEN 'England'
                    WHEN UPPER(country) IN ('SCOTLAND') THEN 'Scotland'
                    WHEN UPPER(country) IN ('WALES') THEN 'Wales'
                    WHEN UPPER(country) IN ('NORTHERN IRELAND', 'N. IRELAND') THEN 'Northern Ireland'
                    ELSE 'Other/Unknown'
                END AS country_name,
                COUNT(DISTINCT node_id) AS station_count
            FROM current_prices
            GROUP BY country_name
            ORDER BY station_count DESC
        """)
        by_country = cur.fetchall()

        cur.execute("""
            SELECT finished_at FROM scrape_runs
            WHERE status = 'completed'
            ORDER BY finished_at DESC LIMIT 1
        """)
        row = cur.fetchone()
        last_scrape = row["finished_at"].isoformat() if row and row["finished_at"] else None

        cur.execute("""
            SELECT COALESCE(SUM(price_records_count), 0) AS total_reports,
                   COALESCE(SUM(price_records_count) FILTER (
                       WHERE started_at >= CURRENT_DATE
                   ), 0) AS reports_today
            FROM scrape_runs
            WHERE status = 'completed'
        """)
        reports = cur.fetchone()

    return {
        "by_fuel_type": by_fuel,
        "by_country": by_country,
        "total_stations": totals["total_stations"],
        "total_prices": totals["total_prices"],
        "last_scrape": last_scrape,
        "total_reports": reports["total_reports"],
        "reports_today": reports["reports_today"],
    }


@app.get("/api/prices/by-region")
def prices_by_region(
    fuel_type: str = Query("E10"),
    db=Depends(get_db),
    _auth=Depends(require_auth),
):
    """Average price by region for a given fuel type."""
    with db.cursor() as cur:
        cur.execute("""
            SELECT region,
                   ROUND(AVG(price)::numeric, 1) AS avg_price,
                   MIN(price) AS min_price,
                   MAX(price) AS max_price,
                   COUNT(*) AS station_count
            FROM current_prices
            WHERE fuel_type = %s
              AND region IS NOT NULL
              AND NOT temporary_closure
              AND NOT price_is_outlier
            GROUP BY region
            ORDER BY avg_price DESC
        """, (fuel_type,))
        return cur.fetchall()


@app.get("/api/prices/by-brand")
def prices_by_brand(
    fuel_type: str = Query("E10"),
    limit: int = Query(20, ge=1, le=100),
    order: str = Query("asc"),
    db=Depends(get_db),
    _auth=Depends(require_auth),
):
    """Average price by brand for a given fuel type."""
    sort_dir = "DESC" if order.lower() == "desc" else "ASC"
    with db.cursor() as cur:
        cur.execute(f"""
            SELECT brand_name,
                   forecourt_type,
                   ROUND(AVG(price)::numeric, 1) AS avg_price,
                   MIN(price) AS min_price,
                   MAX(price) AS max_price,
                   COUNT(*) AS station_count
            FROM current_prices
            WHERE fuel_type = %s
              AND brand_name IS NOT NULL
              AND NOT temporary_closure
              AND NOT price_is_outlier
            GROUP BY brand_name, forecourt_type
            HAVING COUNT(*) >= 3
            ORDER BY avg_price {sort_dir}
            LIMIT %s
        """, (fuel_type, limit))
        return cur.fetchall()


@app.get("/api/prices/by-category")
def prices_by_category(
    fuel_type: str = Query("E10"),
    db=Depends(get_db),
    _auth=Depends(require_auth),
):
    """Average price by forecourt category for a given fuel type."""
    with db.cursor() as cur:
        cur.execute("""
            SELECT forecourt_type,
                   ROUND(AVG(price)::numeric, 1) AS avg_price,
                   MIN(price) AS min_price,
                   MAX(price) AS max_price,
                   COUNT(*) AS station_count
            FROM current_prices
            WHERE fuel_type = %s
              AND NOT temporary_closure
              AND NOT price_is_outlier
            GROUP BY forecourt_type
            ORDER BY avg_price
        """, (fuel_type,))
        return cur.fetchall()


@app.get("/api/prices/station/{node_id}/history")
def station_price_history(
    node_id: str,
    fuel_type: str = Query("E10"),
    days: Optional[int] = Query(None, ge=1, le=365),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    granularity: Optional[str] = Query(None),
    db=Depends(get_db),
    role: str = Depends(resolve_role),
):
    """Price history for a single station."""
    max_days = 90 if role == "readonly" else 365
    if start_date or end_date:
        range_start = datetime.fromisoformat(start_date) if start_date else None
        range_end = datetime.fromisoformat(end_date) if end_date else None
        span_days = ((range_end or datetime.now()) - (range_start or range_end)).days if range_start or range_end else 30
        span_days = min(span_days, max_days)
    else:
        effective_days = min(days if days is not None else 30, max_days)
        range_start = range_end = None
        span_days = effective_days

    if range_start and range_end:
        time_filter = "fp.observed_at >= %s AND fp.observed_at < %s + interval '1 day'"
        time_params = (range_start, range_end)
    elif range_start:
        time_filter = "fp.observed_at >= %s"
        time_params = (range_start,)
    elif range_end:
        time_filter = "fp.observed_at < %s + interval '1 day'"
        time_params = (range_end,)
    else:
        effective_days = min(days if days is not None else 30, max_days)
        time_filter = "fp.observed_at >= NOW() - make_interval(days => %s)"
        time_params = (effective_days,)

    # Equivalent date-range filter for the daily_prices summary table
    if range_start and range_end:
        daily_time_filter = "dp.price_date >= %s AND dp.price_date <= %s"
        daily_time_params = (range_start, range_end)
    elif range_start:
        daily_time_filter = "dp.price_date >= %s"
        daily_time_params = (range_start,)
    elif range_end:
        daily_time_filter = "dp.price_date <= %s"
        daily_time_params = (range_end,)
    else:
        daily_time_filter = "dp.price_date >= CURRENT_DATE - make_interval(days => %s)"
        daily_time_params = (effective_days,)

    if granularity in ('hourly', 'daily'):
        effective_granularity = granularity
    else:
        effective_granularity = "hourly"

    time_col = "date_trunc('hour', fp.observed_at)" if effective_granularity == 'hourly' else "DATE(fp.observed_at)"

    with db.cursor() as cur:
        if effective_granularity == 'daily':
            cur.execute(f"""
                SELECT dp.price_date AS bucket,
                       dp.avg_price
                FROM daily_prices dp
                WHERE dp.node_id = %s
                  AND dp.fuel_type = %s
                  AND {daily_time_filter}
                ORDER BY dp.price_date
            """, (node_id, fuel_type, *daily_time_params))
        else:
            cur.execute(f"""
                SELECT {time_col} AS bucket,
                       ROUND(AVG(COALESCE(pc.corrected_price, fp.price))::numeric, 1) AS avg_price
                FROM fuel_prices fp
                LEFT JOIN price_corrections pc ON pc.fuel_price_id = fp.id
                WHERE fp.node_id = %s
                  AND fp.fuel_type = %s
                  AND {time_filter}
                  AND fp.anomaly_flags IS NULL
                GROUP BY bucket
                ORDER BY bucket
            """, (node_id, fuel_type, *time_params))
        data = cur.fetchall()

        # Also fetch station info (from the materialised view which
        # already resolves brand aliases, categories, etc.)
        cur.execute("""
            SELECT DISTINCT ON (node_id)
                   trading_name, brand_name, raw_brand_name,
                   city, postcode, forecourt_type
            FROM current_prices WHERE node_id = %s
        """, (node_id,))
        station = cur.fetchone()

    return {
        "granularity": effective_granularity,
        "station": station,
        "data": data,
    }


@app.get("/api/prices/history")
def price_history(
    fuel_type: str = Query("E10"),
    days: Optional[int] = Query(None, ge=1, le=365),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    granularity: Optional[str] = Query(None),
    region: Optional[str] = Query(None),
    country: Optional[str] = Query(None),
    rural_urban: Optional[str] = Query(None),
    node_ids: Optional[str] = Query(None),
    # Search-style filters — used for "view trend for all results"
    station: Optional[str] = Query(None),
    brand: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    postcode: Optional[str] = Query(None),
    city: Optional[str] = Query(None),
    district: Optional[str] = Query(None),
    constituency: Optional[str] = Query(None),
    supermarket_only: bool = Query(False),
    motorway_only: bool = Query(False),
    exclude_outliers: bool = Query(False),
    db=Depends(get_db),
    role: str = Depends(resolve_role),
):
    """Average price over time, optionally filtered by region.

    Accepts either a date range (start_date/end_date as YYYY-MM-DD) or a
    days parameter (relative to now). If none are provided, defaults to 30 days.

    Granularity can be 'hourly' or 'daily'. If not specified, defaults to
    hourly for ranges under 30 days and daily for 30 days or longer.
    Excludes anomaly-flagged records. Applies a Hampel filter (rolling
    median ± 3×MAD) to smooth outlier averages without distorting trends.
    """
    max_days = 90 if role == "readonly" else 365
    # Resolve the time range
    if start_date or end_date:
        range_start = datetime.fromisoformat(start_date) if start_date else None
        range_end = datetime.fromisoformat(end_date) if end_date else None
        if range_start and range_end:
            span_days = (range_end - range_start).days
            if span_days > max_days:
                range_start = range_end - timedelta(days=max_days)
                span_days = max_days
        elif range_start:
            span_days = (datetime.now() - range_start).days
            if span_days > max_days:
                range_start = datetime.now() - timedelta(days=max_days)
                span_days = max_days
        else:
            span_days = 30
    else:
        effective_days = days if days is not None else 30
        effective_days = min(effective_days, max_days)
        range_start = None
        range_end = None
        span_days = effective_days

    # Build time filter clause and params
    if range_start and range_end:
        time_filter = "fp.observed_at >= %s AND fp.observed_at < %s + interval '1 day'"
        time_params = (range_start, range_end)
    elif range_start:
        time_filter = "fp.observed_at >= %s"
        time_params = (range_start,)
    elif range_end:
        time_filter = "fp.observed_at < %s + interval '1 day'"
        time_params = (range_end,)
    else:
        effective_days = min(days if days is not None else 30, max_days)
        time_filter = "fp.observed_at >= NOW() - make_interval(days => %s)"
        time_params = (effective_days,)

    # Equivalent date-range filter for the daily_prices summary table
    if range_start and range_end:
        daily_time_filter = "dp.price_date >= %s AND dp.price_date <= %s"
        daily_time_params = (range_start, range_end)
    elif range_start:
        daily_time_filter = "dp.price_date >= %s"
        daily_time_params = (range_start,)
    elif range_end:
        daily_time_filter = "dp.price_date <= %s"
        daily_time_params = (range_end,)
    else:
        daily_time_filter = "dp.price_date >= CURRENT_DATE - make_interval(days => %s)"
        daily_time_params = (effective_days,)

    # Determine granularity
    if granularity in ('hourly', 'daily'):
        effective_granularity = granularity
    else:
        effective_granularity = "hourly" if span_days < 30 else "daily"

    if effective_granularity == 'hourly':
        time_col = "date_trunc('hour', fp.observed_at)"
    else:
        time_col = "DATE(fp.observed_at)"

    # Optional node_ids filter (small selections) or search filter subquery (large sets)
    node_filter = ""
    node_params = ()

    # Build a subquery from search-style filters if any are provided
    search_conditions = []
    search_params_list = []
    if station:
        search_conditions.append("UPPER(trading_name) LIKE %s")
        search_params_list.append("%" + station.upper() + "%")
    if brand:
        search_conditions.append("UPPER(brand_name) LIKE %s")
        search_params_list.append("%" + brand.upper() + "%")
    if category:
        cats = [c.strip() for c in category.split(",") if c.strip()]
        if len(cats) == 1:
            search_conditions.append("forecourt_type = %s")
            search_params_list.append(cats[0])
        elif cats:
            ph = ", ".join(["%s"] * len(cats))
            search_conditions.append(f"forecourt_type IN ({ph})")
            search_params_list.extend(cats)
    if postcode:
        search_conditions.append("UPPER(postcode) LIKE %s")
        search_params_list.append(postcode.upper().replace(" ", "") + "%")
    if city:
        search_conditions.append("UPPER(city) LIKE %s")
        search_params_list.append("%" + city.upper() + "%")
    if district:
        search_conditions.append("admin_district = %s")
        search_params_list.append(district)
    if constituency:
        search_conditions.append("parliamentary_constituency = %s")
        search_params_list.append(constituency)
    if supermarket_only:
        search_conditions.append("is_supermarket_service_station = TRUE")
    if motorway_only:
        search_conditions.append("is_motorway_service_station = TRUE")
    if exclude_outliers:
        search_conditions.append("NOT price_is_outlier")

    if search_conditions:
        # Use a subquery against the indexed current_prices view
        sub_where = " AND ".join(["fuel_type = %s", "NOT temporary_closure"] + search_conditions)
        node_filter = f"AND fp.node_id IN (SELECT node_id FROM current_prices WHERE {sub_where})"
        node_params = (fuel_type, *search_params_list)
    elif node_ids:
        ids = [n.strip() for n in node_ids.split(",") if n.strip()]
        if ids:
            placeholders = ", ".join(["%s"] * len(ids))
            node_filter = f"AND fp.node_id IN ({placeholders})"
            node_params = tuple(ids)

    # Location filters — need stations/postcode joins when any are set
    needs_location = bool(region or country or rural_urban)
    location_joins = ""
    location_filters = ""
    location_params = ()

    if needs_location:
        location_joins = """
            JOIN stations s ON s.node_id = fp.node_id
            LEFT JOIN postcode_lookups pl ON pl.postcode = s.postcode
            LEFT JOIN postcode_regions pr ON pr.postcode_area = (
                CASE WHEN LEFT(s.postcode, 2) ~ '^[A-Z]{2}$'
                     THEN LEFT(s.postcode, 2) ELSE LEFT(s.postcode, 1) END
            )
        """
        filters = []
        params_list = []

        if region:
            vals = [v.strip() for v in region.split(",") if v.strip()]
            if len(vals) == 1:
                filters.append("AND COALESCE(pl.ons_region, pr.region) = %s")
                params_list.append(vals[0])
            elif vals:
                ph = ", ".join(["%s"] * len(vals))
                filters.append(f"AND COALESCE(pl.ons_region, pr.region) IN ({ph})")
                params_list.extend(vals)

        if country:
            vals = [v.strip() for v in country.split(",") if v.strip()]
            known = {'ENGLAND', 'SCOTLAND', 'WALES', 'NORTHERN IRELAND', 'N. IRELAND'}
            named = [v for v in vals if v != "Other/Unknown"]
            has_other = "Other/Unknown" in vals
            parts = []
            if named:
                ph = ", ".join(["UPPER(%s)"] * len(named))
                parts.append(f"UPPER(COALESCE(pl.country, s.country)) IN ({ph})")
                params_list.extend(named)
            if has_other:
                known_ph = ", ".join(["'" + k + "'" for k in known])
                parts.append(f"(COALESCE(pl.country, s.country) IS NULL OR UPPER(COALESCE(pl.country, s.country)) NOT IN ({known_ph}))")
            if parts:
                filters.append("AND (" + " OR ".join(parts) + ")")

        if rural_urban:
            vals = [v.strip() for v in rural_urban.split(",") if v.strip()]
            if len(vals) == 1:
                filters.append("AND pl.rural_urban = %s")
                params_list.append(vals[0])
            elif vals:
                ph = ", ".join(["%s"] * len(vals))
                filters.append(f"AND pl.rural_urban IN ({ph})")
                params_list.extend(vals)

        location_filters = "\n                  ".join(filters)
        location_params = tuple(params_list)

    with db.cursor() as cur:
        if effective_granularity == 'daily':
            # Use pre-aggregated daily_prices table for daily queries
            daily_location_joins = location_joins.replace("fp.node_id", "dp.node_id") if location_joins else ""
            daily_node_filter = node_filter.replace("fp.node_id", "dp.node_id") if node_filter else ""
            cur.execute(f"""
                SELECT dp.price_date AS bucket,
                       ROUND((SUM(dp.avg_price * dp.sample_count) / SUM(dp.sample_count))::numeric, 1) AS avg_price,
                       COUNT(DISTINCT dp.node_id) AS stations
                FROM daily_prices dp
                {daily_location_joins}
                WHERE dp.fuel_type = %s
                  AND {daily_time_filter}
                  {daily_node_filter}
                  {location_filters}
                GROUP BY dp.price_date
                ORDER BY dp.price_date
            """, (fuel_type, *daily_time_params, *node_params, *location_params))
        else:
            cur.execute(f"""
                SELECT {time_col} AS bucket,
                       ROUND(AVG(COALESCE(pc.corrected_price, fp.price))::numeric, 1) AS avg_price,
                       COUNT(DISTINCT fp.node_id) AS stations
                FROM fuel_prices fp
                LEFT JOIN price_corrections pc ON pc.fuel_price_id = fp.id
                {location_joins}
                WHERE fp.fuel_type = %s
                  AND {time_filter}
                  {node_filter}
                  {location_filters}
                  AND fp.anomaly_flags IS NULL
                GROUP BY bucket
                ORDER BY bucket
            """, (fuel_type, *time_params, *node_params, *location_params))
        rows = cur.fetchall()

    # Apply Hampel filter to smooth outlier daily/hourly averages.
    # Uses a rolling window with median ± 3×MAD to detect and replace
    # anomalous buckets, handling trending data correctly.
    if len(rows) >= 3:
        prices = [float(r["avg_price"]) for r in rows]
        win = 49 if effective_granularity == "hourly" else 7
        half = win // 2
        n = len(prices)
        k = 1.4826  # consistency constant for Gaussian distribution
        for i in range(n):
            lo = max(0, i - half)
            hi = min(n, i + half + 1)
            window = prices[lo:hi]
            med = statistics.median(window)
            mad = statistics.median([abs(x - med) for x in window])
            if mad * k > 0 and abs(prices[i] - med) > 3.0 * k * mad:
                rows[i]["avg_price"] = round(med, 1)

    return {"granularity": effective_granularity, "data": rows}


@app.get("/api/prices/history/export")
def price_history_export(
    fuel_type: str = Query(...),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    days: Optional[int] = Query(None, ge=1, le=3650),
    region: Optional[str] = Query(None),
    country: Optional[str] = Query(None),
    rural_urban: Optional[str] = Query(None),
    node_ids: Optional[str] = Query(None),
    brand: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    postcode: Optional[str] = Query(None),
    city: Optional[str] = Query(None),
    district: Optional[str] = Query(None),
    constituency: Optional[str] = Query(None),
    supermarket_only: bool = Query(False),
    motorway_only: bool = Query(False),
    exclude_outliers: bool = Query(False),
    format: str = Query("csv"),
    db=Depends(get_db),
    _auth=Depends(require_editor),
):
    """Export raw individual price records matching the trend filters.

    Returns every fuel_prices row (not averages) with station and
    postcode enrichment data.  Streamed to handle large result sets.
    """
    import csv
    import io

    # Time filter
    time_filter = ""
    time_params: list = []
    if start_date and end_date:
        time_filter = "AND fp.observed_at >= %s::date AND fp.observed_at < %s::date + interval '1 day'"
        time_params = [start_date, end_date]
    elif start_date:
        time_filter = "AND fp.observed_at >= %s::date"
        time_params = [start_date]
    elif end_date:
        time_filter = "AND fp.observed_at < %s::date + interval '1 day'"
        time_params = [end_date]
    else:
        effective_days = days if days is not None else 30
        time_filter = "AND fp.observed_at >= NOW() - make_interval(days => %s)"
        time_params = [effective_days]

    # Node / search filters
    node_filter = ""
    node_params: list = []

    search_conditions = []
    search_params_list: list = []
    if brand:
        search_conditions.append("UPPER(brand_name) LIKE %s")
        search_params_list.append("%" + brand.upper() + "%")
    if category:
        cats = [c.strip() for c in category.split(",") if c.strip()]
        if len(cats) == 1:
            search_conditions.append("forecourt_type = %s")
            search_params_list.append(cats[0])
        elif cats:
            ph = ", ".join(["%s"] * len(cats))
            search_conditions.append(f"forecourt_type IN ({ph})")
            search_params_list.extend(cats)
    if postcode:
        search_conditions.append("UPPER(postcode) LIKE %s")
        search_params_list.append(postcode.upper().replace(" ", "") + "%")
    if city:
        search_conditions.append("UPPER(city) LIKE %s")
        search_params_list.append("%" + city.upper() + "%")
    if district:
        search_conditions.append("admin_district = %s")
        search_params_list.append(district)
    if constituency:
        search_conditions.append("parliamentary_constituency = %s")
        search_params_list.append(constituency)
    if supermarket_only:
        search_conditions.append("is_supermarket_service_station = TRUE")
    if motorway_only:
        search_conditions.append("is_motorway_service_station = TRUE")
    if exclude_outliers:
        search_conditions.append("NOT price_is_outlier")

    if search_conditions:
        sub_where = " AND ".join(["fuel_type = %s", "NOT temporary_closure"] + search_conditions)
        node_filter = f"AND fp.node_id IN (SELECT node_id FROM current_prices WHERE {sub_where})"
        node_params = [fuel_type, *search_params_list]
    elif node_ids:
        ids = [n.strip() for n in node_ids.split(",") if n.strip()]
        if ids:
            placeholders = ", ".join(["%s"] * len(ids))
            node_filter = f"AND fp.node_id IN ({placeholders})"
            node_params = list(ids)

    # Location filters
    location_filters = ""
    location_params: list = []
    if region:
        vals = [v.strip() for v in region.split(",") if v.strip()]
        if len(vals) == 1:
            location_filters += " AND COALESCE(pl.ons_region, pr.region) = %s"
            location_params.append(vals[0])
        elif vals:
            ph = ", ".join(["%s"] * len(vals))
            location_filters += f" AND COALESCE(pl.ons_region, pr.region) IN ({ph})"
            location_params.extend(vals)
    if country:
        vals = [v.strip() for v in country.split(",") if v.strip()]
        known = {'ENGLAND', 'SCOTLAND', 'WALES', 'NORTHERN IRELAND', 'N. IRELAND'}
        named = [v for v in vals if v != "Other/Unknown"]
        has_other = "Other/Unknown" in vals
        parts = []
        if named:
            ph = ", ".join(["UPPER(%s)"] * len(named))
            parts.append(f"UPPER(COALESCE(pl.country, s.country)) IN ({ph})")
            location_params.extend(named)
        if has_other:
            known_ph = ", ".join(["'" + k + "'" for k in known])
            parts.append(f"(COALESCE(pl.country, s.country) IS NULL OR UPPER(COALESCE(pl.country, s.country)) NOT IN ({known_ph}))")
        if parts:
            location_filters += " AND (" + " OR ".join(parts) + ")"
    if rural_urban:
        vals = [v.strip() for v in rural_urban.split(",") if v.strip()]
        if len(vals) == 1:
            location_filters += " AND pl.rural_urban = %s"
            location_params.append(vals[0])
        elif vals:
            ph = ", ".join(["%s"] * len(vals))
            location_filters += f" AND pl.rural_urban IN ({ph})"
            location_params.extend(vals)

    query = f"""
        SELECT fp.node_id,
               s.trading_name,
               s.brand_name AS raw_brand,
               COALESCE(so.canonical_brand, ba.canonical_brand, s.brand_name) AS brand,
               fp.fuel_type,
               COALESCE(ftl.fuel_name, fp.fuel_type) AS fuel_name,
               fp.price AS original_price,
               pc.corrected_price,
               COALESCE(pc.corrected_price, fp.price) AS price,
               fp.observed_at,
               fp.anomaly_flags,
               s.postcode,
               s.city,
               s.county,
               COALESCE(pl.country, s.country) AS country,
               COALESCE(pl.ons_region, pr.region) AS region,
               pl.admin_district,
               pl.parliamentary_constituency,
               pl.rural_urban,
               CASE WHEN s.is_motorway_service_station THEN 'Motorway'
                    ELSE COALESCE(bc.forecourt_type, 'Uncategorised')
               END AS forecourt_type,
               s.latitude,
               s.longitude,
               s.is_motorway_service_station,
               s.is_supermarket_service_station
        FROM fuel_prices fp
        JOIN stations s ON s.node_id = fp.node_id
        LEFT JOIN price_corrections pc ON pc.fuel_price_id = fp.id
        LEFT JOIN postcode_lookups pl ON pl.postcode = s.postcode
        LEFT JOIN postcode_regions pr ON pr.postcode_area = (
            CASE WHEN LEFT(s.postcode, 2) ~ '^[A-Z]{{2}}$'
                 THEN LEFT(s.postcode, 2) ELSE LEFT(s.postcode, 1) END
        )
        LEFT JOIN brand_aliases ba ON ba.raw_brand_name = s.brand_name
        LEFT JOIN station_brand_overrides so ON so.node_id = s.node_id
        LEFT JOIN brand_categories bc
            ON bc.canonical_brand = COALESCE(so.canonical_brand, ba.canonical_brand, s.brand_name)
        LEFT JOIN fuel_type_labels ftl ON ftl.fuel_type_code = fp.fuel_type
        WHERE fp.fuel_type = %s
          {time_filter}
          {node_filter}
          {location_filters}
        ORDER BY fp.observed_at, s.trading_name
    """
    params = (fuel_type, *time_params, *node_params, *location_params)

    columns = [
        "node_id", "trading_name", "raw_brand", "brand", "fuel_type",
        "fuel_name", "original_price", "corrected_price", "price",
        "observed_at", "anomaly_flags",
        "postcode", "city", "county", "country", "region",
        "admin_district", "parliamentary_constituency", "rural_urban",
        "forecourt_type", "latitude", "longitude",
        "is_motorway_service_station", "is_supermarket_service_station",
    ]

    if format == "json":
        def json_stream():
            conn = _pool.getconn()
            try:
                with conn.cursor() as cur:
                    cur.execute(query, params)
                    yield "[\n"
                    first = True
                    while True:
                        rows = cur.fetchmany(1000)
                        if not rows:
                            break
                        for row in rows:
                            if not first:
                                yield ",\n"
                            first = False
                            # Convert anomaly_flags list and datetimes
                            d = dict(row)
                            if d.get("observed_at"):
                                d["observed_at"] = d["observed_at"].isoformat()
                            yield json.dumps(d, default=str)
                    yield "\n]\n"
            finally:
                _pool.putconn(conn)

        return StreamingResponse(
            json_stream(),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="fuel-prices-raw.json"'},
        )
    else:
        def csv_stream():
            conn = _pool.getconn()
            try:
                with conn.cursor() as cur:
                    cur.execute(query, params)
                    buf = io.StringIO()
                    writer = csv.writer(buf)
                    writer.writerow(columns)
                    yield buf.getvalue()
                    buf.seek(0)
                    buf.truncate(0)
                    while True:
                        rows = cur.fetchmany(1000)
                        if not rows:
                            break
                        for row in rows:
                            writer.writerow([row.get(c) for c in columns])
                        yield buf.getvalue()
                        buf.seek(0)
                        buf.truncate(0)
            finally:
                _pool.putconn(conn)

        return StreamingResponse(
            csv_stream(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="fuel-prices-raw.csv"'},
        )


@app.get("/api/prices/map")
def price_map(
    fuel_type: str = Query("E10"),
    region: Optional[str] = Query(None),
    brand: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    exclude_outliers: bool = Query(False),
    db=Depends(get_db),
    _auth=Depends(require_auth),
):
    """Current prices with lat/lng for map display."""
    conditions = [
        "fuel_type = %s",
        "latitude IS NOT NULL",
        "longitude IS NOT NULL",
        "latitude BETWEEN 49 AND 61",
        "longitude BETWEEN -9 AND 2",
        "NOT temporary_closure",
    ]
    params: list = [fuel_type]

    if region:
        vals = [v.strip() for v in region.split(",") if v.strip()]
        if len(vals) == 1:
            conditions.append("region = %s")
            params.append(vals[0])
        elif vals:
            placeholders = ", ".join(["%s"] * len(vals))
            conditions.append(f"region IN ({placeholders})")
            params.extend(vals)
    if brand:
        conditions.append("UPPER(brand_name) LIKE %s")
        params.append("%" + brand.upper() + "%")
    if category:
        cats = [c.strip() for c in category.split(",") if c.strip()]
        if len(cats) == 1:
            conditions.append("forecourt_type = %s")
            params.append(cats[0])
        elif cats:
            placeholders = ", ".join(["%s"] * len(cats))
            conditions.append(f"forecourt_type IN ({placeholders})")
            params.extend(cats)
    if exclude_outliers:
        conditions.append("NOT price_is_outlier")

    where = " AND ".join(conditions)
    with db.cursor() as cur:
        cur.execute(f"""
            SELECT node_id, trading_name, brand_name, raw_brand_name, city, postcode,
                   price, fuel_name, forecourt_type,
                   admin_district, rural_urban, parliamentary_constituency,
                   latitude, longitude,
                   is_motorway_service_station, is_supermarket_service_station,
                   observed_at
            FROM current_prices
            WHERE {where}
            ORDER BY price
        """, params)
        return cur.fetchall()


@app.get("/api/prices/search")
def price_search(
    fuel_type: Optional[str] = Query(None),
    postcode: Optional[str] = Query(None),
    station: Optional[str] = Query(None),
    brand: Optional[str] = Query(None),
    city: Optional[str] = Query(None),
    min_price: Optional[float] = Query(None),
    max_price: Optional[float] = Query(None),
    category: Optional[str] = Query(None),
    district: Optional[str] = Query(None),
    constituency: Optional[str] = Query(None),
    rural_urban: Optional[str] = Query(None),
    region: Optional[str] = Query(None),
    country: Optional[str] = Query(None),
    supermarket_only: bool = Query(False),
    motorway_only: bool = Query(False),
    exclude_outliers: bool = Query(False),
    sort: str = Query("price"),
    order: Optional[str] = Query(None),
    limit: int = Query(50, ge=1),
    offset: int = Query(0, ge=0),
    db=Depends(get_db),
    role: str = Depends(resolve_role),
):
    """Flexible search/filter endpoint for the query builder UI."""
    if role == "readonly":
        limit = min(limit, 200)
    conditions = ["NOT temporary_closure"]
    params: list = []
    if fuel_type:
        conditions.append("fuel_type = %s")
        params.append(fuel_type)

    if postcode:
        conditions.append("UPPER(postcode) LIKE %s")
        params.append(postcode.upper().replace(" ", "") + "%")
    if station:
        conditions.append("UPPER(trading_name) LIKE %s")
        params.append("%" + station.upper() + "%")
    if brand:
        conditions.append("UPPER(brand_name) LIKE %s")
        params.append("%" + brand.upper() + "%")
    if city:
        conditions.append("UPPER(city) LIKE %s")
        params.append("%" + city.upper() + "%")
    if min_price is not None:
        conditions.append("price >= %s")
        params.append(min_price)
    if max_price is not None:
        conditions.append("price <= %s")
        params.append(max_price)
    if supermarket_only:
        conditions.append("is_supermarket_service_station = TRUE")
    if motorway_only:
        conditions.append("is_motorway_service_station = TRUE")
    if exclude_outliers:
        conditions.append("NOT price_is_outlier")
    if category:
        cats = [c.strip() for c in category.split(",") if c.strip()]
        if len(cats) == 1:
            conditions.append("forecourt_type = %s")
            params.append(cats[0])
        elif cats:
            placeholders = ", ".join(["%s"] * len(cats))
            conditions.append(f"forecourt_type IN ({placeholders})")
            params.extend(cats)
    if district:
        conditions.append("admin_district = %s")
        params.append(district)
    if constituency:
        conditions.append("parliamentary_constituency = %s")
        params.append(constituency)
    if rural_urban:
        vals = [v.strip() for v in rural_urban.split(",") if v.strip()]
        if len(vals) == 1:
            conditions.append("rural_urban = %s")
            params.append(vals[0])
        elif vals:
            placeholders = ", ".join(["%s"] * len(vals))
            conditions.append(f"rural_urban IN ({placeholders})")
            params.extend(vals)
    if region:
        vals = [v.strip() for v in region.split(",") if v.strip()]
        if len(vals) == 1:
            conditions.append("region = %s")
            params.append(vals[0])
        elif vals:
            placeholders = ", ".join(["%s"] * len(vals))
            conditions.append(f"region IN ({placeholders})")
            params.extend(vals)
    if country:
        vals = [v.strip() for v in country.split(",") if v.strip()]
        known = {'ENGLAND', 'SCOTLAND', 'WALES', 'NORTHERN IRELAND', 'N. IRELAND'}
        named = [v for v in vals if v != "Other/Unknown"]
        has_other = "Other/Unknown" in vals
        parts = []
        if named:
            placeholders = ", ".join(["UPPER(%s)"] * len(named))
            parts.append(f"UPPER(country) IN ({placeholders})")
            params.extend(named)
        if has_other:
            known_ph = ", ".join(["'" + k + "'" for k in known])
            parts.append(f"(country IS NULL OR UPPER(country) NOT IN ({known_ph}))")
        if parts:
            conditions.append("(" + " OR ".join(parts) + ")")

    where = " AND ".join(conditions)

    allowed_sorts = {"price": "price", "brand": "brand_name", "station": "trading_name", "city": "city", "postcode": "postcode", "district": "admin_district", "rural_urban": "rural_urban", "observed_at": "observed_at"}
    sort_col = allowed_sorts.get(sort, "price")
    sort_dir = "ASC" if order == "asc" else "DESC" if order == "desc" else "ASC"

    with db.cursor() as cur:
        cur.execute(f"""
            SELECT node_id, trading_name, brand_name, raw_brand_name, city, county,
                   postcode, region, price, fuel_name, fuel_category,
                   forecourt_type, admin_district, parliamentary_constituency,
                   rural_urban,
                   latitude, longitude,
                   is_motorway_service_station, is_supermarket_service_station,
                   observed_at
            FROM current_prices
            WHERE {where}
            ORDER BY {sort_col} {sort_dir} NULLS LAST
            LIMIT %s OFFSET %s
        """, params + [limit, offset])
        rows = cur.fetchall()

        # Get total count for pagination
        cur.execute(f"""
            SELECT COUNT(*) AS total FROM current_prices WHERE {where}
        """, params)
        total = cur.fetchone()["total"]

    return {"results": rows, "total": total, "limit": limit, "offset": offset}


@app.get("/api/prices/search/export")
def price_search_export(
    fuel_type: str = Query(...),
    postcode: Optional[str] = Query(None),
    station: Optional[str] = Query(None),
    brand: Optional[str] = Query(None),
    city: Optional[str] = Query(None),
    min_price: Optional[float] = Query(None),
    max_price: Optional[float] = Query(None),
    category: Optional[str] = Query(None),
    district: Optional[str] = Query(None),
    constituency: Optional[str] = Query(None),
    rural_urban: Optional[str] = Query(None),
    region: Optional[str] = Query(None),
    country: Optional[str] = Query(None),
    supermarket_only: bool = Query(False),
    motorway_only: bool = Query(False),
    exclude_outliers: bool = Query(False),
    format: str = Query("csv"),
    _auth=Depends(require_editor),
):
    """Export all historical price records matching the search filters.

    Uses the same filter parameters as /api/prices/search but returns
    every fuel_prices row (not just current) with station and postcode
    enrichment.  Streamed to handle large result sets.
    """
    import csv
    import io

    conditions = ["fp.fuel_type = %s"]
    params: list = [fuel_type]

    if postcode:
        conditions.append("UPPER(s.postcode) LIKE %s")
        params.append(postcode.upper().replace(" ", "") + "%")
    if station:
        conditions.append("UPPER(s.trading_name) LIKE %s")
        params.append("%" + station.upper() + "%")
    if brand:
        conditions.append("UPPER(s.brand_name) LIKE %s")
        params.append("%" + brand.upper() + "%")
    if city:
        conditions.append("UPPER(s.city) LIKE %s")
        params.append("%" + city.upper() + "%")
    if min_price is not None:
        conditions.append("fp.price >= %s")
        params.append(min_price)
    if max_price is not None:
        conditions.append("fp.price <= %s")
        params.append(max_price)
    if supermarket_only:
        conditions.append("s.is_supermarket_service_station = TRUE")
    if motorway_only:
        conditions.append("s.is_motorway_service_station = TRUE")
    if exclude_outliers:
        conditions.append("fp.anomaly_flags IS NULL")
    # Export queries raw tables (not the materialised view), so 'Uncategorised'
    # must be resolved to a NULL brand_categories lookup + non-motorway check.
    if category:
        cats = [c.strip() for c in category.split(",") if c.strip()]
        resolved = []
        has_uncategorised = False
        for c in cats:
            if c == 'Uncategorised':
                has_uncategorised = True
            elif c == 'Motorway':
                resolved.append(('motorway_flag', None))
            else:
                resolved.append(('bc', c))
        parts = []
        bc_vals = [c for tag, c in resolved if tag == 'bc']
        if bc_vals:
            ph = ", ".join(["%s"] * len(bc_vals))
            parts.append(f"bc.forecourt_type IN ({ph})")
            params.extend(bc_vals)
        if any(tag == 'motorway_flag' for tag, _ in resolved):
            parts.append("s.is_motorway_service_station = TRUE")
        if has_uncategorised:
            parts.append("(bc.forecourt_type IS NULL AND NOT s.is_motorway_service_station)")
        if parts:
            conditions.append("(" + " OR ".join(parts) + ")")
    if district:
        conditions.append("pl.admin_district = %s")
        params.append(district)
    if constituency:
        conditions.append("pl.parliamentary_constituency = %s")
        params.append(constituency)
    if rural_urban:
        vals = [v.strip() for v in rural_urban.split(",") if v.strip()]
        if len(vals) == 1:
            conditions.append("pl.rural_urban = %s")
            params.append(vals[0])
        elif vals:
            placeholders = ", ".join(["%s"] * len(vals))
            conditions.append(f"pl.rural_urban IN ({placeholders})")
            params.extend(vals)
    if region:
        vals = [v.strip() for v in region.split(",") if v.strip()]
        if len(vals) == 1:
            conditions.append("COALESCE(pl.ons_region, pr.region) = %s")
            params.append(vals[0])
        elif vals:
            placeholders = ", ".join(["%s"] * len(vals))
            conditions.append(f"COALESCE(pl.ons_region, pr.region) IN ({placeholders})")
            params.extend(vals)
    if country:
        vals = [v.strip() for v in country.split(",") if v.strip()]
        known = {'ENGLAND', 'SCOTLAND', 'WALES', 'NORTHERN IRELAND', 'N. IRELAND'}
        named = [v for v in vals if v != "Other/Unknown"]
        has_other = "Other/Unknown" in vals
        parts = []
        if named:
            placeholders = ", ".join(["UPPER(%s)"] * len(named))
            parts.append(f"UPPER(COALESCE(pl.country, s.country)) IN ({placeholders})")
            params.extend(named)
        if has_other:
            known_ph = ", ".join(["'" + k + "'" for k in known])
            parts.append(f"(COALESCE(pl.country, s.country) IS NULL OR UPPER(COALESCE(pl.country, s.country)) NOT IN ({known_ph}))")
        if parts:
            conditions.append("(" + " OR ".join(parts) + ")")

    where = " AND ".join(conditions) if conditions else "TRUE"
    query = f"""
        SELECT fp.node_id,
               s.trading_name,
               s.brand_name AS raw_brand,
               COALESCE(so.canonical_brand, ba.canonical_brand, s.brand_name) AS brand,
               fp.fuel_type,
               COALESCE(ftl.fuel_name, fp.fuel_type) AS fuel_name,
               fp.price,
               fp.observed_at,
               fp.anomaly_flags,
               s.postcode,
               s.city,
               s.county,
               COALESCE(pl.country, s.country) AS country,
               COALESCE(pl.ons_region, pr.region) AS region,
               pl.admin_district,
               pl.parliamentary_constituency,
               pl.rural_urban,
               CASE WHEN s.is_motorway_service_station THEN 'Motorway'
                    ELSE COALESCE(bc.forecourt_type, 'Uncategorised')
               END AS forecourt_type,
               s.latitude,
               s.longitude,
               s.is_motorway_service_station,
               s.is_supermarket_service_station
        FROM fuel_prices fp
        JOIN stations s ON s.node_id = fp.node_id
        LEFT JOIN postcode_lookups pl ON pl.postcode = s.postcode
        LEFT JOIN postcode_regions pr ON pr.postcode_area = (
            CASE WHEN LEFT(s.postcode, 2) ~ '^[A-Z]{{2}}$'
                 THEN LEFT(s.postcode, 2) ELSE LEFT(s.postcode, 1) END
        )
        LEFT JOIN brand_aliases ba ON ba.raw_brand_name = s.brand_name
        LEFT JOIN station_brand_overrides so ON so.node_id = s.node_id
        LEFT JOIN brand_categories bc
            ON bc.canonical_brand = COALESCE(so.canonical_brand, ba.canonical_brand, s.brand_name)
        LEFT JOIN fuel_type_labels ftl ON ftl.fuel_type_code = fp.fuel_type
        WHERE {where}
        ORDER BY fp.observed_at, s.trading_name
    """

    columns = [
        "node_id", "trading_name", "raw_brand", "brand", "fuel_type",
        "fuel_name", "price", "observed_at", "anomaly_flags",
        "postcode", "city", "county", "country", "region",
        "admin_district", "parliamentary_constituency", "rural_urban",
        "forecourt_type", "latitude", "longitude",
        "is_motorway_service_station", "is_supermarket_service_station",
    ]

    if format == "json":
        def json_stream():
            conn = _pool.getconn()
            try:
                with conn.cursor() as cur:
                    cur.execute(query, params)
                    yield "[\n"
                    first = True
                    while True:
                        rows = cur.fetchmany(1000)
                        if not rows:
                            break
                        for row in rows:
                            if not first:
                                yield ",\n"
                            first = False
                            d = dict(row)
                            if d.get("observed_at"):
                                d["observed_at"] = d["observed_at"].isoformat()
                            yield json.dumps(d, default=str)
                    yield "\n]\n"
            finally:
                _pool.putconn(conn)

        import json
        return StreamingResponse(
            json_stream(),
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="fuel-search-all-history.json"'},
        )
    else:
        def csv_stream():
            conn = _pool.getconn()
            try:
                with conn.cursor() as cur:
                    cur.execute(query, params)
                    buf = io.StringIO()
                    writer = csv.writer(buf)
                    writer.writerow(columns)
                    yield buf.getvalue()
                    buf.seek(0)
                    buf.truncate(0)
                    while True:
                        rows = cur.fetchmany(1000)
                        if not rows:
                            break
                        for row in rows:
                            writer.writerow([row.get(c) for c in columns])
                        yield buf.getvalue()
                        buf.seek(0)
                        buf.truncate(0)
            finally:
                _pool.putconn(conn)

        return StreamingResponse(
            csv_stream(),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="fuel-search-all-history.csv"'},
        )


_ANOMALY_SORT_COLS = {
    "trading_name": "s.trading_name",
    "city": "s.city",
    "fuel_type": "fp.fuel_type",
    "prev_price": "prev.price",
    "prev_observed_at": "prev.observed_at",
    "price": "fp.price",
    "observed_at": "fp.observed_at",
}


@app.get("/api/anomalies")
def anomalies(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    sort: Optional[str] = Query(None),
    order: Optional[str] = Query(None),
    db=Depends(get_db),
    _auth=Depends(require_auth),
):
    """Recent anomaly-flagged price records with previous price context."""
    sort_col = _ANOMALY_SORT_COLS.get(sort, "fp.observed_at")
    sort_dir = "ASC" if order == "asc" else "DESC"

    with db.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) AS total
            FROM fuel_prices fp
            LEFT JOIN price_corrections pc ON pc.fuel_price_id = fp.id
            WHERE fp.anomaly_flags IS NOT NULL
              AND pc.id IS NULL
        """)
        total = cur.fetchone()["total"]
        cur.execute(f"""
            SELECT fp.id, fp.node_id, s.trading_name, s.city,
                   s.brand_name, s.postcode,
                   fp.fuel_type, fp.price, fp.anomaly_flags,
                   fp.observed_at,
                   prev.price AS prev_price,
                   prev.observed_at AS prev_observed_at
            FROM fuel_prices fp
            JOIN stations s ON s.node_id = fp.node_id
            LEFT JOIN price_corrections pc ON pc.fuel_price_id = fp.id
            LEFT JOIN LATERAL (
                SELECT COALESCE(pc2.corrected_price, p2.price) AS price,
                       p2.observed_at
                FROM fuel_prices p2
                LEFT JOIN price_corrections pc2 ON pc2.fuel_price_id = p2.id
                WHERE p2.node_id = fp.node_id
                  AND p2.fuel_type = fp.fuel_type
                  AND p2.observed_at < fp.observed_at
                ORDER BY p2.observed_at DESC
                LIMIT 1
            ) prev ON true
            WHERE fp.anomaly_flags IS NOT NULL
              AND pc.id IS NULL
            ORDER BY {sort_col} {sort_dir} NULLS LAST
            LIMIT %s OFFSET %s
        """, (limit, offset))
        return {"rows": cur.fetchall(), "total": total, "limit": limit, "offset": offset}


@app.get("/api/fuel-types")
def fuel_types(db=Depends(get_db)):
    """List available fuel types."""
    with db.cursor() as cur:
        cur.execute("""
            SELECT fuel_type_code, fuel_name, fuel_category
            FROM fuel_type_labels
            ORDER BY fuel_category, fuel_name
        """)
        rows = cur.fetchall()
        if not rows:
            # Fallback if seed data not loaded
            cur.execute("""
                SELECT DISTINCT fuel_type AS fuel_type_code,
                       fuel_type AS fuel_name,
                       'Unknown' AS fuel_category
                FROM current_prices
                ORDER BY fuel_type
            """)
            rows = cur.fetchall()
        return rows


@app.get("/api/regions")
def regions(db=Depends(get_db)):
    """List available regions."""
    with db.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT region FROM postcode_regions
            WHERE region IS NOT NULL
            ORDER BY region
        """)
        return [r["region"] for r in cur.fetchall()]


@app.get("/api/districts")
def districts(db=Depends(get_db)):
    """List available local authority districts."""
    with db.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT admin_district
            FROM current_prices
            WHERE admin_district IS NOT NULL
            ORDER BY admin_district
        """)
        return [r["admin_district"] for r in cur.fetchall()]


@app.get("/api/constituencies")
def constituencies(db=Depends(get_db)):
    """List available parliamentary constituencies."""
    with db.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT parliamentary_constituency
            FROM current_prices
            WHERE parliamentary_constituency IS NOT NULL
            ORDER BY parliamentary_constituency
        """)
        return [r["parliamentary_constituency"] for r in cur.fetchall()]


@app.get("/api/prices/by-district")
def prices_by_district(
    fuel_type: str = Query("E10"),
    limit: int = Query(30, ge=1, le=500),
    db=Depends(get_db),
    _auth=Depends(require_auth),
):
    """Average price by local authority district."""
    with db.cursor() as cur:
        cur.execute("""
            SELECT admin_district,
                   ROUND(AVG(price)::numeric, 1) AS avg_price,
                   MIN(price) AS min_price,
                   MAX(price) AS max_price,
                   COUNT(*) AS station_count
            FROM current_prices
            WHERE fuel_type = %s
              AND admin_district IS NOT NULL
              AND NOT temporary_closure
              AND NOT price_is_outlier
            GROUP BY admin_district
            HAVING COUNT(*) >= 3
            ORDER BY avg_price DESC
            LIMIT %s
        """, (fuel_type, limit))
        return cur.fetchall()


@app.get("/api/prices/by-rural-urban")
def prices_by_rural_urban(
    fuel_type: str = Query("E10"),
    db=Depends(get_db),
    _auth=Depends(require_auth),
):
    """Average price by rural/urban classification.

    England/Wales use the ONS RUC and Scotland uses the Scottish Government
    classification.  We map both into a unified set of labels so the chart
    is not confusing, while returning the underlying values for drill-down.
    """
    with db.cursor() as cur:
        cur.execute("""
            SELECT unified_label,
                   ROUND(AVG(price)::numeric, 1) AS avg_price,
                   MIN(price) AS min_price,
                   MAX(price) AS max_price,
                   COUNT(*) AS station_count,
                   ARRAY_AGG(DISTINCT rural_urban) AS rural_urban_values
            FROM (
                SELECT price, rural_urban,
                    CASE
                        -- England/Wales ONS RUC
                        WHEN rural_urban LIKE 'Urban:%%' THEN 'Urban'
                        WHEN rural_urban LIKE 'Smaller rural:%%' THEN 'Rural (smaller)'
                        WHEN rural_urban LIKE 'Larger rural:%%' THEN 'Rural (larger)'
                        -- Scotland
                        WHEN rural_urban IN ('Large Urban Areas', 'Other Urban Areas') THEN 'Urban'
                        WHEN rural_urban = 'Accessible Small Towns' THEN 'Small towns'
                        WHEN rural_urban = 'Remote Small Towns' THEN 'Remote small towns'
                        WHEN rural_urban = 'Accessible Rural' THEN 'Rural (accessible)'
                        WHEN rural_urban = 'Remote Rural' THEN 'Remote rural'
                        ELSE rural_urban
                    END AS unified_label
                FROM current_prices
                WHERE fuel_type = %s
                  AND rural_urban IS NOT NULL
                  AND NOT temporary_closure
                  AND NOT price_is_outlier
            ) sub
            GROUP BY unified_label
            ORDER BY avg_price DESC
        """, (fuel_type,))
        return cur.fetchall()


@app.get("/api/prices/by-constituency")
def prices_by_constituency(
    fuel_type: str = Query("E10"),
    limit: int = Query(30, ge=1, le=650),
    db=Depends(get_db),
    _auth=Depends(require_auth),
):
    """Average price by parliamentary constituency."""
    with db.cursor() as cur:
        cur.execute("""
            SELECT parliamentary_constituency,
                   ROUND(AVG(price)::numeric, 1) AS avg_price,
                   MIN(price) AS min_price,
                   MAX(price) AS max_price,
                   COUNT(*) AS station_count
            FROM current_prices
            WHERE fuel_type = %s
              AND parliamentary_constituency IS NOT NULL
              AND NOT temporary_closure
              AND NOT price_is_outlier
            GROUP BY parliamentary_constituency
            HAVING COUNT(*) >= 2
            ORDER BY avg_price DESC
            LIMIT %s
        """, (fuel_type, limit))
        return cur.fetchall()


# ---------------------------------------------------------------------------
# Lookup tables — read/write for brand aliases, categories, overrides
# ---------------------------------------------------------------------------

@app.get("/api/admin/brand-aliases")
def list_brand_aliases(db=Depends(get_db), _auth=Depends(require_auth)):
    """All brand alias mappings."""
    with db.cursor() as cur:
        cur.execute("""
            SELECT raw_brand_name, canonical_brand, created_at
            FROM brand_aliases ORDER BY canonical_brand, raw_brand_name
        """)
        return cur.fetchall()


@app.post("/api/admin/brand-aliases")
def upsert_brand_alias(body: "BrandAliasBody", db=Depends(get_db), _auth=Depends(require_editor)):
    """Create or update a brand alias mapping."""
    raw = body.raw_brand_name.strip()
    canonical = body.canonical_brand.strip()
    if not raw or not canonical:
        raise HTTPException(400, "raw_brand_name and canonical_brand required")
    with db.cursor() as cur:
        cur.execute("""
            INSERT INTO brand_aliases (raw_brand_name, canonical_brand)
            VALUES (%s, %s)
            ON CONFLICT (raw_brand_name) DO UPDATE SET canonical_brand = EXCLUDED.canonical_brand
            RETURNING raw_brand_name, canonical_brand
        """, (raw, canonical))
        db.commit()
        return cur.fetchone()


@app.delete("/api/admin/brand-aliases/{raw_brand_name:path}")
def delete_brand_alias(raw_brand_name: str, db=Depends(get_db), _auth=Depends(require_editor)):
    """Remove a brand alias."""
    with db.cursor() as cur:
        cur.execute("DELETE FROM brand_aliases WHERE raw_brand_name = %s RETURNING raw_brand_name", (raw_brand_name,))
        db.commit()
        if not cur.fetchone():
            raise HTTPException(404, "Alias not found")
        return {"deleted": raw_brand_name}


@app.get("/api/admin/brand-categories")
def list_brand_categories(db=Depends(get_db), _auth=Depends(require_auth)):
    """All brand → forecourt type mappings."""
    with db.cursor() as cur:
        cur.execute("""
            SELECT canonical_brand, forecourt_type
            FROM brand_categories ORDER BY forecourt_type, canonical_brand
        """)
        return cur.fetchall()


@app.post("/api/admin/brand-categories")
def upsert_brand_category(body: "BrandCategoryBody", db=Depends(get_db), _auth=Depends(require_editor)):
    """Create or update a brand category mapping."""
    brand = body.canonical_brand.strip()
    cat = body.forecourt_type.strip()
    allowed = {"Supermarket", "Major Oil", "Motorway Operator", "Fuel Group", "Convenience", "Independent", "Uncategorised"}
    if not brand or not cat:
        raise HTTPException(400, "canonical_brand and forecourt_type required")
    if cat not in allowed:
        raise HTTPException(400, f"forecourt_type must be one of: {', '.join(sorted(allowed))}")
    with db.cursor() as cur:
        cur.execute("""
            INSERT INTO brand_categories (canonical_brand, forecourt_type)
            VALUES (%s, %s)
            ON CONFLICT (canonical_brand) DO UPDATE SET forecourt_type = EXCLUDED.forecourt_type
            RETURNING canonical_brand, forecourt_type
        """, (brand, cat))
        db.commit()
        return cur.fetchone()


@app.delete("/api/admin/brand-categories/{canonical_brand:path}")
def delete_brand_category(canonical_brand: str, db=Depends(get_db), _auth=Depends(require_editor)):
    """Remove a brand category mapping (brand will default to Uncategorised)."""
    with db.cursor() as cur:
        cur.execute("DELETE FROM brand_categories WHERE canonical_brand = %s RETURNING canonical_brand", (canonical_brand,))
        db.commit()
        if not cur.fetchone():
            raise HTTPException(404, "Category mapping not found")
        return {"deleted": canonical_brand}


@app.get("/api/admin/station-overrides")
def list_station_overrides(db=Depends(get_db), _auth=Depends(require_auth)):
    """All per-station brand overrides."""
    with db.cursor() as cur:
        cur.execute("""
            SELECT sbo.node_id, s.trading_name, s.brand_name AS raw_brand_name,
                   sbo.canonical_brand, sbo.notes, sbo.created_at
            FROM station_brand_overrides sbo
            JOIN stations s ON s.node_id = sbo.node_id
            ORDER BY sbo.canonical_brand, s.trading_name
        """)
        return cur.fetchall()


@app.post("/api/admin/station-overrides")
def upsert_station_override(body: "StationOverrideBody", db=Depends(get_db), _auth=Depends(require_editor)):
    """Create or update a per-station brand override."""
    node_id = body.node_id.strip()
    canonical = body.canonical_brand.strip()
    notes = (body.notes or "").strip() or None
    if not node_id or not canonical:
        raise HTTPException(400, "node_id and canonical_brand required")
    with db.cursor() as cur:
        cur.execute("SELECT node_id FROM stations WHERE node_id = %s", (node_id,))
        if not cur.fetchone():
            raise HTTPException(404, f"Station {node_id} not found")
        cur.execute("""
            INSERT INTO station_brand_overrides (node_id, canonical_brand, notes)
            VALUES (%s, %s, %s)
            ON CONFLICT (node_id) DO UPDATE
                SET canonical_brand = EXCLUDED.canonical_brand,
                    notes = EXCLUDED.notes
            RETURNING node_id, canonical_brand, notes
        """, (node_id, canonical, notes))
        db.commit()
        return cur.fetchone()


@app.delete("/api/admin/station-overrides/{node_id}")
def delete_station_override(node_id: str, db=Depends(get_db), _auth=Depends(require_editor)):
    """Remove a per-station brand override."""
    with db.cursor() as cur:
        cur.execute("DELETE FROM station_brand_overrides WHERE node_id = %s RETURNING node_id", (node_id,))
        db.commit()
        if not cur.fetchone():
            raise HTTPException(404, "Override not found")
        return {"deleted": node_id}


@app.get("/api/admin/normalisation-report")
def normalisation_report(
    limit: int = Query(100, ge=1, le=1000),
    filter_type: Optional[str] = Query(None, alias="type"),
    brand_filter: Optional[str] = Query(None, alias="brand"),
    db=Depends(get_db),
    _auth=Depends(require_auth),
):
    """Show how brands were resolved: raw → alias → override → canonical → category.

    filter_type: 'aliased', 'overridden', 'unmapped', 'all' (default: all)
    """
    with db.cursor() as cur:
        conditions = []
        params: list = []

        if filter_type == "aliased":
            conditions.append("ba.canonical_brand IS NOT NULL")
            conditions.append("sbo.canonical_brand IS NULL")
        elif filter_type == "overridden":
            conditions.append("sbo.canonical_brand IS NOT NULL")
        elif filter_type == "unmapped":
            conditions.append("ba.canonical_brand IS NULL")
            conditions.append("sbo.canonical_brand IS NULL")
            conditions.append("bc.forecourt_type IS NULL")

        if brand_filter:
            conditions.append("UPPER(COALESCE(sbo.canonical_brand, ba.canonical_brand, s.brand_name)) LIKE %s")
            params.append("%" + brand_filter.upper() + "%")

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        cur.execute(f"""
            SELECT s.brand_name AS raw_brand,
                   ba.canonical_brand AS alias_resolved,
                   sbo.canonical_brand AS override_resolved,
                   COALESCE(sbo.canonical_brand, ba.canonical_brand, s.brand_name) AS final_brand,
                   CASE
                       WHEN s.is_motorway_service_station THEN 'Motorway'
                       ELSE COALESCE(bc.forecourt_type, 'Uncategorised')
                   END AS forecourt_type,
                   CASE
                       WHEN sbo.canonical_brand IS NOT NULL THEN 'override'
                       WHEN ba.canonical_brand IS NOT NULL THEN 'alias'
                       ELSE 'raw'
                   END AS resolution_method,
                   COUNT(*) AS station_count
            FROM stations s
            LEFT JOIN brand_aliases ba ON ba.raw_brand_name = s.brand_name
            LEFT JOIN station_brand_overrides sbo ON sbo.node_id = s.node_id
            LEFT JOIN brand_categories bc ON bc.canonical_brand =
                COALESCE(sbo.canonical_brand, ba.canonical_brand, s.brand_name)
            {where}
            GROUP BY s.brand_name, ba.canonical_brand, sbo.canonical_brand,
                     s.is_motorway_service_station, bc.forecourt_type
            ORDER BY station_count DESC
            LIMIT %s
        """, params + [limit])
        return cur.fetchall()


@app.get("/api/admin/postcode-issues")
def postcode_issues(
    db=Depends(get_db),
    _auth=Depends(require_auth),
):
    """Stations whose postcodes were not recognised by postcodes.io.

    These may indicate bad source data (typos, missing spaces, invalid
    prefixes) and often correlate with wrong coordinates in the API.
    """
    with db.cursor() as cur:
        cur.execute("""
            SELECT s.node_id, s.trading_name, s.brand_name, s.postcode,
                   s.latitude AS api_latitude, s.longitude AS api_longitude,
                   s.city, s.county,
                   CASE
                       WHEN s.latitude IS NOT NULL AND
                            (s.latitude < 49 OR s.latitude > 61 OR
                             s.longitude < -8 OR s.longitude > 2)
                       THEN true ELSE false
                   END AS coords_outside_uk,
                   CASE
                       WHEN pl.pc_latitude IS NOT NULL AND pl.admin_district IS NULL
                       THEN pl.pc_latitude
                   END AS fixed_latitude,
                   CASE
                       WHEN pl.pc_latitude IS NOT NULL AND pl.admin_district IS NULL
                       THEN pl.pc_longitude
                   END AS fixed_longitude
            FROM stations s
            LEFT JOIN postcode_lookups pl ON pl.postcode = s.postcode
            WHERE pl.postcode IS NULL
               OR pl.pc_latitude IS NULL
               OR (pl.pc_latitude IS NOT NULL AND pl.admin_district IS NULL)
            ORDER BY
                CASE WHEN s.latitude IS NOT NULL AND
                    (s.latitude < 49 OR s.latitude > 61 OR
                     s.longitude < -8 OR s.longitude > 2)
                THEN 0 ELSE 1 END,
                CASE WHEN pl.pc_latitude IS NOT NULL AND pl.admin_district IS NULL
                THEN 1 ELSE 0 END,
                s.postcode
        """)
        return cur.fetchall()


@app.patch("/api/admin/postcode-lookups/{postcode}")
def update_postcode_coords(
    postcode: str,
    body: PostcodeCoordsBody,
    db=Depends(get_db),
    _auth=Depends(require_editor),
):
    """Manually set coordinates for a postcode lookup.

    Useful for postcodes that postcodes.io didn't recognise, where the
    correct location is known (e.g. from fixing sign errors in API coords).
    """
    if not (-90 <= body.latitude <= 90 and -180 <= body.longitude <= 180):
        raise HTTPException(400, "Invalid coordinates")
    with db.cursor() as cur:
        cur.execute("""
            INSERT INTO postcode_lookups (postcode, pc_latitude, pc_longitude)
            VALUES (%s, %s, %s)
            ON CONFLICT (postcode) DO UPDATE SET
                pc_latitude = EXCLUDED.pc_latitude,
                pc_longitude = EXCLUDED.pc_longitude,
                looked_up_at = NOW()
        """, (postcode.upper().strip(), body.latitude, body.longitude))
        db.commit()
    return {"postcode": postcode, "latitude": body.latitude, "longitude": body.longitude}


@app.post("/api/admin/refresh-view")
def refresh_view(db=Depends(get_db), _auth=Depends(require_editor)):
    """Refresh the current_prices materialised view after lookup changes."""
    with db.cursor() as cur:
        cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY current_prices")
        db.commit()
    return {"status": "ok", "message": "current_prices view refreshed"}


_OUTLIER_SORT_COLS = {
    "trading_name": "cp.trading_name",
    "city": "cp.city",
    "postcode": "cp.postcode",
    "brand_name": "cp.brand_name",
    "fuel_type": "cp.fuel_type",
    "price": "cp.price",
    "observed_at": "cp.observed_at",
}


@app.get("/api/outliers")
def outliers(
    fuel_type: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    sort: Optional[str] = Query(None),
    order: Optional[str] = Query(None),
    db=Depends(get_db),
    _auth=Depends(require_auth),
):
    """Prices excluded as statistical outliers, with IQR bounds for context.

    Returns the outlier records alongside the IQR fence values that caused
    exclusion, so users can verify the methodology.
    """
    with db.cursor() as cur:
        # Compute IQR bounds for context
        cur.execute("""
            WITH clean AS (
                SELECT fuel_type, price
                FROM current_prices
                WHERE NOT temporary_closure
                  AND NOT price_is_outlier
                  AND anomaly_flags IS NULL
            ),
            bounds AS (
                SELECT fuel_type,
                       ROUND(PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY price)::numeric, 1) AS q1,
                       ROUND(PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY price)::numeric, 1) AS q3,
                       ROUND((PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY price)
                            - PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY price))::numeric, 1) AS iqr,
                       ROUND((PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY price)
                            - 1.5 * (PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY price)
                            - PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY price)))::numeric, 1) AS lower_fence,
                       ROUND((PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY price)
                            + 1.5 * (PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY price)
                            - PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY price)))::numeric, 1) AS upper_fence,
                       COUNT(*) AS total_stations
                FROM clean
                GROUP BY fuel_type
            ),
            outlier_counts AS (
                SELECT fuel_type, COUNT(*) AS outlier_stations
                FROM current_prices
                WHERE NOT temporary_closure
                  AND price_is_outlier
                  AND anomaly_flags IS NULL
                GROUP BY fuel_type
            )
            SELECT b.*, COALESCE(o.outlier_stations, 0) AS outlier_stations
            FROM bounds b
            LEFT JOIN outlier_counts o USING (fuel_type)
            ORDER BY b.fuel_type
        """)
        bounds = {r["fuel_type"]: r for r in cur.fetchall()}

        conditions = ["cp.price_is_outlier", "NOT cp.temporary_closure", "cp.anomaly_flags IS NULL"]
        params: list = []
        if fuel_type:
            conditions.append("cp.fuel_type = %s")
            params.append(fuel_type)

        where = " AND ".join(conditions)
        cur.execute(f"""
            SELECT COUNT(*) AS total
            FROM current_prices cp
            WHERE {where}
        """, params)
        total = cur.fetchone()["total"]
        cur.execute(f"""
            SELECT cp.node_id, cp.trading_name, cp.city, cp.postcode,
                   cp.fuel_type, cp.fuel_name,
                   cp.price, cp.brand_name, cp.forecourt_type,
                   cp.anomaly_flags, cp.observed_at,
                   'iqr_outlier' AS exclusion_reason,
                   fp_latest.price AS original_price,
                   pc.corrected_price
            FROM current_prices cp
            LEFT JOIN LATERAL (
                SELECT fp.id, fp.price
                FROM fuel_prices fp
                WHERE fp.node_id = cp.node_id
                  AND fp.fuel_type = cp.fuel_type
                ORDER BY fp.observed_at DESC
                LIMIT 1
            ) fp_latest ON true
            LEFT JOIN price_corrections pc ON pc.fuel_price_id = fp_latest.id
            WHERE {where}
            ORDER BY {_OUTLIER_SORT_COLS.get(sort, "cp.fuel_type")} {"ASC" if order == "asc" else "DESC"} NULLS LAST
                     {", cp.price ASC" if not sort or sort == "fuel_type" else ""}
            LIMIT %s OFFSET %s
        """, params + [limit, offset])
        rows = cur.fetchall()

    return {
        "bounds": bounds,
        "outliers": rows,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@app.get("/api/admin/price-distribution")
def price_distribution(
    fuel_type: str = Query(...),
    db=Depends(get_db),
    _auth=Depends(require_auth),
):
    """Price histogram (80 bins) split by outlier/clean status, with IQR fence values.

    Anomaly-flagged prices are excluded entirely.  The remaining prices are
    split into clean (within IQR fences) and outlier (outside fences).

    Bin boundaries are computed from the visible window (lower_fence − IQR to
    upper_fence + IQR) so all 80 bins span the region of interest rather than
    the full price range which is dominated by extreme outliers.
    Prices outside the window are counted in the edge bins.

    IQR fences are computed from non-anomalous prices, matching the
    dashboard's exclusion logic.
    """
    with db.cursor() as cur:
        cur.execute("""
            WITH fences AS (
                SELECT
                    fuel_type,
                    PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY price) AS q1,
                    PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY price) AS q3
                FROM current_prices
                WHERE fuel_type = %s AND NOT temporary_closure AND anomaly_flags IS NULL
                GROUP BY fuel_type
            ),
            params AS (
                SELECT
                    q1,
                    q3,
                    q3 - q1 AS iqr,
                    q1 - 1.5 * (q3 - q1) AS lower_fence,
                    q3 + 1.5 * (q3 - q1) AS upper_fence,
                    -- visible window: fence ± one IQR of padding
                    (q1 - 1.5 * (q3 - q1)) - (q3 - q1) AS wmin,
                    (q3 + 1.5 * (q3 - q1)) + (q3 - q1) AS wmax,
                    ((q3 + 1.5 * (q3 - q1)) + (q3 - q1)
                     - (q1 - 1.5 * (q3 - q1)) + (q3 - q1)) / 80.0 AS step
                FROM fences
            ),
            buckets AS (
                SELECT
                    -- clamp to [1, 80] so edge prices fall into the end bins
                    GREATEST(1, LEAST(80,
                        width_bucket(cp.price, p.wmin, p.wmax + 0.001, 80)
                    )) AS bucket,
                    -- classify: outside fences = outlier, inside = clean
                    CASE WHEN cp.price < p.lower_fence
                              OR cp.price > p.upper_fence
                         THEN 'outlier' ELSE 'clean' END AS status,
                    COUNT(*) AS cnt
                FROM current_prices cp, params p
                WHERE cp.fuel_type = %s AND NOT cp.temporary_closure AND cp.anomaly_flags IS NULL
                GROUP BY bucket, status
            )
            SELECT
                ROUND((p.wmin + (b.bucket - 1) * p.step)::numeric, 2) AS bin_low,
                COALESCE(SUM(b.cnt) FILTER (WHERE b.status = 'clean'), 0) AS clean,
                COALESCE(SUM(b.cnt) FILTER (WHERE b.status = 'outlier'), 0) AS outlier,
                ROUND(p.q1::numeric, 2) AS q1,
                ROUND(p.iqr::numeric, 2) AS iqr,
                ROUND(p.lower_fence::numeric, 2) AS lower_fence,
                ROUND(p.upper_fence::numeric, 2) AS upper_fence
            FROM buckets b, params p
            GROUP BY b.bucket, p.wmin, p.step, p.q1, p.iqr, p.lower_fence, p.upper_fence
            ORDER BY b.bucket
        """, [fuel_type, fuel_type])
        rows = cur.fetchall()
    if not rows:
        raise HTTPException(404, "No data for fuel type")
    first = rows[0]
    fences = {
        "q1": float(first["q1"]),
        "iqr": float(first["iqr"]),
        "lower_fence": float(first["lower_fence"]),
        "upper_fence": float(first["upper_fence"]),
    }
    bins = [
        {"bin_low": float(r["bin_low"]), "clean": r["clean"], "outlier": r["outlier"]}
        for r in rows
    ]
    return {"bins": bins, **fences}


# ---------------------------------------------------------------------------
# Price corrections
# ---------------------------------------------------------------------------

@app.get("/api/prices/station/{node_id}/records")
def station_price_records(
    node_id: str,
    fuel_type: Optional[str] = Query(None),
    limit: int = Query(500, ge=1, le=5000),
    db=Depends(get_db),
    _auth=Depends(require_auth),
):
    """Raw individual price records for a station, with any corrections."""
    conditions = ["fp.node_id = %s"]
    params: list = [node_id]
    if fuel_type:
        conditions.append("fp.fuel_type = %s")
        params.append(fuel_type)
    where = " AND ".join(conditions)
    with db.cursor() as cur:
        cur.execute(f"""
            SELECT fp.id AS fuel_price_id,
                   fp.fuel_type,
                   COALESCE(ftl.fuel_name, fp.fuel_type) AS fuel_name,
                   fp.price AS original_price,
                   pc.corrected_price,
                   COALESCE(pc.corrected_price, fp.price) AS effective_price,
                   fp.anomaly_flags,
                   fp.observed_at,
                   pc.reason AS correction_reason,
                   pc.corrected_by,
                   pc.corrected_at,
                   prev_eff.price AS prev_effective_price
            FROM fuel_prices fp
            LEFT JOIN price_corrections pc ON pc.fuel_price_id = fp.id
            LEFT JOIN fuel_type_labels ftl ON ftl.fuel_type_code = fp.fuel_type
            LEFT JOIN LATERAL (
                SELECT COALESCE(pc2.corrected_price, p2.price) AS price
                FROM fuel_prices p2
                LEFT JOIN price_corrections pc2 ON pc2.fuel_price_id = p2.id
                WHERE p2.node_id = fp.node_id
                  AND p2.fuel_type = fp.fuel_type
                  AND p2.observed_at < fp.observed_at
                ORDER BY p2.observed_at DESC
                LIMIT 1
            ) prev_eff ON true
            WHERE {where}
            ORDER BY fp.observed_at DESC
            LIMIT %s
        """, params + [limit])
        records = cur.fetchall()

        # Compute per-fuel IQR fences from the current_prices materialised view.
        # This matches the fences used by the dashboard and outlier page, and is
        # much faster than scanning the full fuel_prices history table (~8ms vs
        # ~130ms) because it only considers the latest price per station.
        bounds_by_fuel = {}
        fuel_types = sorted({r["fuel_type"] for r in records if r.get("fuel_type")})
        if fuel_types:
            placeholders = ", ".join(["%s"] * len(fuel_types))
            cur.execute(f"""
                SELECT fuel_type,
                       PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY price) AS q1,
                       PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY price) AS q3
                FROM current_prices
                WHERE NOT temporary_closure
                  AND NOT price_is_outlier
                  AND fuel_type IN ({placeholders})
                GROUP BY fuel_type
            """, fuel_types)
            for b in cur.fetchall():
                q1 = float(b["q1"])
                q3 = float(b["q3"])
                iqr = q3 - q1
                bounds_by_fuel[b["fuel_type"]] = {
                    "lower_fence": q1 - 1.5 * iqr,
                    "upper_fence": q3 + 1.5 * iqr,
                }

        # Compute effective_flags: the current anomaly state of the effective
        # price (corrected or original).  For uncorrected records this is just
        # the anomaly_flags set at scrape time (kept accurate by the cascade
        # logic).  For corrected records we re-evaluate from scratch so the
        # Status column reflects whether the correction actually resolved the
        # anomaly.
        for r in records:
            effective_price = float(r["corrected_price"] if r["corrected_price"] is not None else r["original_price"])
            bounds = bounds_by_fuel.get(r["fuel_type"])
            is_iqr_outlier = bool(
                bounds
                and (
                    effective_price < bounds["lower_fence"]
                    or effective_price > bounds["upper_fence"]
                )
            )
            r["effective_is_iqr_outlier"] = is_iqr_outlier

            if r["corrected_price"] is not None:
                eff = effective_price
                flags = []
                if eff < 80.0:
                    flags.append(f"price_below_floor:{eff}<80.0")
                elif eff > 300.0:
                    flags.append(f"price_above_ceiling:{eff}>300.0")
                prev = r["prev_effective_price"]
                if prev and float(prev) > 0:
                    pct = abs(eff - float(prev)) / float(prev) * 100
                    if pct > 30.0:
                        flags.append(f"large_change:{pct:.1f}%_from_{prev}")
                if not flags and is_iqr_outlier:
                    if eff < bounds["lower_fence"]:
                        flags.append(f"current_iqr_outlier:{eff}<{bounds['lower_fence']:.1f}")
                    else:
                        flags.append(f"current_iqr_outlier:{eff}>{bounds['upper_fence']:.1f}")
                r["effective_flags"] = flags or None
            else:
                flags = list(r["anomaly_flags"] or [])
                if not flags and is_iqr_outlier:
                    if effective_price < bounds["lower_fence"]:
                        flags.append(f"current_iqr_outlier:{effective_price}<{bounds['lower_fence']:.1f}")
                    else:
                        flags.append(f"current_iqr_outlier:{effective_price}>{bounds['upper_fence']:.1f}")
                r["effective_flags"] = flags or None

        cur.execute("""
            SELECT DISTINCT ON (node_id)
                   trading_name, brand_name, raw_brand_name,
                   city, postcode, forecourt_type
            FROM current_prices WHERE node_id = %s
        """, (node_id,))
        station = cur.fetchone()

    return {"station": station, "records": records}


# ---------------------------------------------------------------------------
# Correction-cascade: re-evaluate anomaly flags on adjacent prices
# ---------------------------------------------------------------------------

def _reevaluate_adjacent_anomalies(cur, fuel_price_id, effective_price):
    """After correcting/reverting a price, re-evaluate the large_change flag
    on the next price record for the same station+fuel_type.

    Only large_change flags depend on the previous price; floor/ceiling flags
    are intrinsic to the price itself and are left untouched.
    """
    cur.execute("""
        SELECT fp2.id, fp2.price, fp2.anomaly_flags
        FROM fuel_prices fp
        JOIN fuel_prices fp2
          ON fp2.node_id = fp.node_id
         AND fp2.fuel_type = fp.fuel_type
         AND fp2.observed_at > fp.observed_at
        WHERE fp.id = %s
        ORDER BY fp2.observed_at ASC
        LIMIT 1
    """, (fuel_price_id,))
    next_row = cur.fetchone()
    if not next_row:
        return

    old_flags = next_row["anomaly_flags"] or []
    flags = [f for f in old_flags if not f.startswith("large_change:")]

    eff = float(effective_price)
    if eff > 0:
        change_pct = abs(float(next_row["price"]) - eff) / eff * 100
        if change_pct > 30.0:
            flags.append(f"large_change:{change_pct:.1f}%_from_{effective_price}")

    cur.execute("UPDATE fuel_prices SET anomaly_flags = %s WHERE id = %s",
                (flags or None, next_row["id"]))


def _refresh_daily_prices_for(cur, fuel_price_ids):
    """Re-aggregate daily_prices rows affected by the given fuel_price records."""
    if not fuel_price_ids:
        return
    cur.execute("""
        INSERT INTO daily_prices (node_id, fuel_type, price_date,
                                  avg_price, min_price, max_price, sample_count)
        SELECT fp.node_id, fp.fuel_type, DATE(fp.observed_at),
               ROUND(AVG(COALESCE(pc.corrected_price, fp.price))::numeric, 1),
               ROUND(MIN(COALESCE(pc.corrected_price, fp.price))::numeric, 1),
               ROUND(MAX(COALESCE(pc.corrected_price, fp.price))::numeric, 1),
               COUNT(*)
        FROM fuel_prices fp
        LEFT JOIN price_corrections pc ON pc.fuel_price_id = fp.id
        WHERE fp.anomaly_flags IS NULL
          AND (fp.node_id, fp.fuel_type, DATE(fp.observed_at)) IN (
              SELECT node_id, fuel_type, DATE(observed_at)
              FROM fuel_prices WHERE id = ANY(%s)
          )
        GROUP BY fp.node_id, fp.fuel_type, DATE(fp.observed_at)
        ON CONFLICT (node_id, fuel_type, price_date) DO UPDATE SET
            avg_price  = EXCLUDED.avg_price,
            min_price  = EXCLUDED.min_price,
            max_price  = EXCLUDED.max_price,
            sample_count = EXCLUDED.sample_count
    """, ([int(fid) for fid in fuel_price_ids],))


@app.post("/api/corrections")
def create_correction(
    body: PriceCorrectionBody,
    request: Request,
    db=Depends(get_db),
    _auth=Depends(require_editor),
):
    """Create or update a price correction. Original data is preserved."""
    corrected_by = getattr(request.state, "user_email", None) or "admin"
    with db.cursor() as cur:
        # Verify the fuel_price_id exists and get original price + flags
        cur.execute("SELECT price, anomaly_flags FROM fuel_prices WHERE id = %s", (body.fuel_price_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Fuel price record not found")
        original_price = float(row["price"])
        flags = row["anomaly_flags"]
        reason = ", ".join(flags) if flags else "Manual override"

        cur.execute("""
            INSERT INTO price_corrections (fuel_price_id, original_price, corrected_price, reason, corrected_by)
            VALUES (%s, %s, %s, %s, %s)            ON CONFLICT (fuel_price_id) DO UPDATE SET
                corrected_price = EXCLUDED.corrected_price,
                reason = EXCLUDED.reason,
                corrected_by = EXCLUDED.corrected_by,
                corrected_at = NOW()
            RETURNING id
        """, (body.fuel_price_id, original_price, body.corrected_price, reason, corrected_by))
        correction_id = cur.fetchone()["id"]
        _reevaluate_adjacent_anomalies(cur, body.fuel_price_id, body.corrected_price)
        db.commit()
        cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY current_prices")
        _refresh_daily_prices_for(cur, [body.fuel_price_id])
        db.commit()
    return {"id": correction_id, "fuel_price_id": body.fuel_price_id, "original_price": original_price, "corrected_price": body.corrected_price}


@app.post("/api/corrections/batch")
def create_corrections_batch(
    body: BatchCorrectionsBody,
    request: Request,
    db=Depends(get_db),
    _auth=Depends(require_editor),
):
    """Create or update multiple price corrections in one transaction."""
    if not body.corrections:
        raise HTTPException(status_code=400, detail="No corrections provided")
    if len(body.corrections) > 200:
        raise HTTPException(status_code=400, detail="Maximum 200 corrections per batch")
    corrected_by = getattr(request.state, "user_email", None) or "admin"
    results = []
    with db.cursor() as cur:
        for item in body.corrections:
            cur.execute("SELECT price, anomaly_flags FROM fuel_prices WHERE id = %s", (item.fuel_price_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail=f"Fuel price record {item.fuel_price_id} not found")
            original_price = float(row["price"])
            flags = row["anomaly_flags"]
            reason = ", ".join(flags) if flags else "Manual override"
            cur.execute("""
                INSERT INTO price_corrections (fuel_price_id, original_price, corrected_price, reason, corrected_by)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (fuel_price_id) DO UPDATE SET
                    corrected_price = EXCLUDED.corrected_price,
                    reason = EXCLUDED.reason,
                    corrected_by = EXCLUDED.corrected_by,
                    corrected_at = NOW()
                RETURNING id
            """, (item.fuel_price_id, original_price, item.corrected_price, reason, corrected_by))
            correction_id = cur.fetchone()["id"]
            _reevaluate_adjacent_anomalies(cur, item.fuel_price_id, item.corrected_price)
            results.append({"id": correction_id, "fuel_price_id": item.fuel_price_id, "corrected_price": item.corrected_price})
        db.commit()
        cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY current_prices")
        _refresh_daily_prices_for(cur, [r["fuel_price_id"] for r in results])
        db.commit()
    return {"saved": len(results), "corrections": results}


@app.delete("/api/corrections/{fuel_price_id}")
def delete_correction(
    fuel_price_id: int,
    db=Depends(get_db),
    _auth=Depends(require_editor),
):
    """Revert a price correction (restore original price)."""
    with db.cursor() as cur:
        cur.execute("DELETE FROM price_corrections WHERE fuel_price_id = %s RETURNING id, original_price", (fuel_price_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="No correction found for this price record")
        _reevaluate_adjacent_anomalies(cur, fuel_price_id, row["original_price"])
        db.commit()
        cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY current_prices")
        _refresh_daily_prices_for(cur, [fuel_price_id])
        db.commit()
    return {"deleted": True, "fuel_price_id": fuel_price_id}


# ---------------------------------------------------------------------------
# User management (Cognito)
# ---------------------------------------------------------------------------

def _cognito_client():
    region = os.environ.get("COGNITO_REGION", os.environ.get("AWS_REGION", "eu-north-1"))
    return boto3.client("cognito-idp", region_name=region)


def _pool_id():
    return os.environ.get("COGNITO_USER_POOL_ID", "")


class CreateUserBody(BaseModel):
    email: str
    admin: bool = False
    role: Optional[str] = None  # 'admin', 'editor', 'readonly'


@app.get("/api/admin/scrape-runs")
def list_scrape_runs(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db=Depends(get_db),
    _auth=Depends(require_auth),
):
    """Recent scrape runs with timing and record counts."""
    with db.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS total FROM scrape_runs")
        total = cur.fetchone()["total"]
        cur.execute("""
            SELECT id, started_at, finished_at, run_type, status,
                   batches_fetched, stations_count, price_records_count,
                   s3_key, error_message,
                   EXTRACT(EPOCH FROM (finished_at - started_at))::int AS duration_secs
            FROM scrape_runs
            ORDER BY started_at DESC
            LIMIT %s OFFSET %s
        """, (limit, offset))
        return {"rows": cur.fetchall(), "total": total, "limit": limit, "offset": offset}


@app.get("/api/admin/corrections")
def list_corrections(
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db=Depends(get_db),
    _auth=Depends(require_auth),
):
    """History of price corrections with station and fuel context."""
    with db.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS total FROM price_corrections")
        total = cur.fetchone()["total"]
        cur.execute("""
            SELECT pc.corrected_at, pc.original_price, pc.corrected_price,
                   pc.reason, pc.corrected_by,
                   s.trading_name, s.city,
                   fp.fuel_type, fp.observed_at,
                   COALESCE(ftl.fuel_name, fp.fuel_type) AS fuel_name
            FROM price_corrections pc
            JOIN fuel_prices fp ON fp.id = pc.fuel_price_id
            JOIN stations s ON s.node_id = fp.node_id
            LEFT JOIN fuel_type_labels ftl ON ftl.fuel_type_code = fp.fuel_type
            ORDER BY pc.corrected_at DESC
            LIMIT %s OFFSET %s
        """, (limit, offset))
        return {"rows": cur.fetchall(), "total": total, "limit": limit, "offset": offset}


@app.get("/api/admin/users")
def list_users(_auth=Depends(require_admin)):
    """List all Cognito users and their group memberships."""
    client = _cognito_client()
    pool = _pool_id()
    users = []
    paginator = client.get_paginator("list_users")
    for page in paginator.paginate(UserPoolId=pool):
        for u in page["Users"]:
            attrs = {a["Name"]: a["Value"] for a in u.get("Attributes", [])}
            groups_resp = client.admin_list_groups_for_user(
                Username=u["Username"], UserPoolId=pool
            )
            groups = [g["GroupName"] for g in groups_resp.get("Groups", [])]
            users.append({
                "username": u["Username"],
                "email": attrs.get("email", ""),
                "status": u["UserStatus"],
                "enabled": u["Enabled"],
                "groups": groups,
                "created": u["UserCreateDate"].isoformat(),
            })
    return users


@app.post("/api/admin/users")
def create_user(body: CreateUserBody, _auth=Depends(require_admin)):
    """Create a new Cognito user (sends invitation email)."""
    client = _cognito_client()
    pool = _pool_id()
    email = body.email.strip().lower()
    if not email:
        raise HTTPException(400, "email is required")
    try:
        resp = client.admin_create_user(
            UserPoolId=pool,
            Username=email,
            UserAttributes=[{"Name": "email", "Value": email}, {"Name": "email_verified", "Value": "true"}],
            DesiredDeliveryMediums=["EMAIL"],
        )
    except client.exceptions.UsernameExistsException:
        raise HTTPException(409, f"User {email} already exists")
    # Determine role: prefer 'role' field, fall back to legacy 'admin' bool
    role = body.role or ("admin" if body.admin else "editor")
    if role in ("admin", "editor"):
        client.admin_add_user_to_group(
            UserPoolId=pool, Username=email, GroupName=role
        )
    return {"username": email, "status": resp["User"]["UserStatus"]}


@app.post("/api/admin/users/{username}/groups/{group}")
def add_user_to_group(username: str, group: str, _auth=Depends(require_admin)):
    """Add a user to a Cognito group."""
    client = _cognito_client()
    client.admin_add_user_to_group(
        UserPoolId=_pool_id(), Username=username, GroupName=group
    )
    return {"status": "ok"}


@app.delete("/api/admin/users/{username}/groups/{group}")
def remove_user_from_group(username: str, group: str, _auth=Depends(require_admin)):
    """Remove a user from a Cognito group."""
    client = _cognito_client()
    client.admin_remove_user_from_group(
        UserPoolId=_pool_id(), Username=username, GroupName=group
    )
    return {"status": "ok"}


@app.post("/api/admin/users/{username}/disable")
def disable_user(username: str, _auth=Depends(require_admin)):
    """Disable a Cognito user account."""
    client = _cognito_client()
    client.admin_disable_user(UserPoolId=_pool_id(), Username=username)
    return {"status": "ok"}


@app.post("/api/admin/users/{username}/enable")
def enable_user(username: str, _auth=Depends(require_admin)):
    """Re-enable a disabled Cognito user account."""
    client = _cognito_client()
    client.admin_enable_user(UserPoolId=_pool_id(), Username=username)
    return {"status": "ok"}


@app.delete("/api/admin/users/{username}")
def delete_user(username: str, _auth=Depends(require_admin)):
    """Permanently delete a Cognito user account."""
    client = _cognito_client()
    client.admin_delete_user(UserPoolId=_pool_id(), Username=username)
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Serve static frontend
# ---------------------------------------------------------------------------

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def index():
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))

    @app.get("/docs/api")
    def api_docs_page():
        return FileResponse(os.path.join(STATIC_DIR, "api.html"))

    @app.get("/docs/about")
    def about_page():
        return FileResponse(os.path.join(STATIC_DIR, "about.html"))
