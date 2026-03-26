"""FastAPI backend for the Fuel Finder web UI.

Provides read-only API endpoints over the PostgreSQL database.
Auth is stubbed out for future use (JWT / API key).
"""

import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

import boto3
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import SimpleConnectionPool
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
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
        yield conn
    finally:
        _pool.putconn(conn)


# ---------------------------------------------------------------------------
# Auth — Cognito JWT / API key / no-auth (see auth.py)
# ---------------------------------------------------------------------------

from auth import require_auth, require_admin, get_auth_config


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

CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "*").split(",")

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
            SELECT finished_at FROM scrape_runs
            WHERE status = 'completed'
            ORDER BY finished_at DESC LIMIT 1
        """)
        row = cur.fetchone()
        last_scrape = row["finished_at"].isoformat() if row and row["finished_at"] else None

    return {
        "by_fuel_type": by_fuel,
        "total_stations": totals["total_stations"],
        "total_prices": totals["total_prices"],
        "last_scrape": last_scrape,
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
    db=Depends(get_db),
    _auth=Depends(require_auth),
):
    """Average price by brand for a given fuel type."""
    with db.cursor() as cur:
        cur.execute("""
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
            ORDER BY avg_price
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


@app.get("/api/prices/history")
def price_history(
    fuel_type: str = Query("E10"),
    days: int = Query(30, ge=1, le=365),
    region: Optional[str] = Query(None),
    db=Depends(get_db),
    _auth=Depends(require_auth),
):
    """Average price over time, optionally filtered by region.

    Uses hourly granularity for ranges <= 30 days, daily for longer.
    Excludes anomaly-flagged records and IQR-based statistical outliers.
    """
    # CTE to compute IQR fences per fuel type from recent non-anomalous data
    bounds_cte = """
        WITH bounds AS (
            SELECT fuel_type,
                   PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY price) AS q1,
                   PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY price) AS q3
            FROM fuel_prices
            WHERE fuel_type = %s
              AND anomaly_flags IS NULL
              AND observed_at >= NOW() - make_interval(days => %s)
            GROUP BY fuel_type
        )
    """
    # Hourly buckets for <= 30 days, daily for longer ranges
    if days <= 30:
        time_col = "date_trunc('hour', fp.observed_at)"
    else:
        time_col = "DATE(fp.observed_at)"
    granularity = "hourly" if days <= 30 else "daily"

    with db.cursor() as cur:
        if region:
            cur.execute(bounds_cte + f"""
                SELECT {time_col} AS bucket,
                       ROUND(AVG(fp.price)::numeric, 1) AS avg_price,
                       COUNT(DISTINCT fp.node_id) AS stations
                FROM fuel_prices fp
                JOIN stations s ON s.node_id = fp.node_id
                LEFT JOIN postcode_regions pr ON pr.postcode_area = (
                    CASE WHEN LEFT(s.postcode, 2) ~ '^[A-Z]{{2}}$'
                         THEN LEFT(s.postcode, 2) ELSE LEFT(s.postcode, 1) END
                )
                LEFT JOIN bounds b ON b.fuel_type = fp.fuel_type
                WHERE fp.fuel_type = %s
                  AND fp.observed_at >= NOW() - make_interval(days => %s)
                  AND pr.region = %s
                  AND fp.anomaly_flags IS NULL
                  AND (b.q1 IS NULL OR fp.price >= b.q1 - 1.5 * (b.q3 - b.q1))
                  AND (b.q3 IS NULL OR fp.price <= b.q3 + 1.5 * (b.q3 - b.q1))
                GROUP BY bucket
                ORDER BY bucket
            """, (fuel_type, days, fuel_type, days, region))
        else:
            cur.execute(bounds_cte + f"""
                SELECT {time_col} AS bucket,
                       ROUND(AVG(fp.price)::numeric, 1) AS avg_price,
                       COUNT(DISTINCT fp.node_id) AS stations
                FROM fuel_prices fp
                LEFT JOIN bounds b ON b.fuel_type = fp.fuel_type
                WHERE fp.fuel_type = %s
                  AND fp.observed_at >= NOW() - make_interval(days => %s)
                  AND fp.anomaly_flags IS NULL
                  AND (b.q1 IS NULL OR fp.price >= b.q1 - 1.5 * (b.q3 - b.q1))
                  AND (b.q3 IS NULL OR fp.price <= b.q3 + 1.5 * (b.q3 - b.q1))
                GROUP BY bucket
                ORDER BY bucket
            """, (fuel_type, days, fuel_type, days))
        return {"granularity": granularity, "data": cur.fetchall()}


@app.get("/api/prices/map")
def price_map(
    fuel_type: str = Query("E10"),
    db=Depends(get_db),
    _auth=Depends(require_auth),
):
    """Current prices with lat/lng for map display."""
    with db.cursor() as cur:
        cur.execute("""
            SELECT node_id, trading_name, brand_name, city, postcode,
                   price, fuel_name, forecourt_type,
                   admin_district, rural_urban, parliamentary_constituency,
                   latitude, longitude,
                   is_motorway_service_station, is_supermarket_service_station
            FROM current_prices
            WHERE fuel_type = %s
              AND latitude IS NOT NULL
              AND longitude IS NOT NULL
              AND latitude BETWEEN 49 AND 61
              AND longitude BETWEEN -9 AND 2
              AND NOT temporary_closure
            ORDER BY price
        """, (fuel_type,))
        return cur.fetchall()


@app.get("/api/prices/search")
def price_search(
    fuel_type: str = Query("E10"),
    postcode: Optional[str] = Query(None),
    brand: Optional[str] = Query(None),
    city: Optional[str] = Query(None),
    min_price: Optional[float] = Query(None),
    max_price: Optional[float] = Query(None),
    category: Optional[str] = Query(None),
    district: Optional[str] = Query(None),
    constituency: Optional[str] = Query(None),
    rural_urban: Optional[str] = Query(None),
    region: Optional[str] = Query(None),
    supermarket_only: bool = Query(False),
    motorway_only: bool = Query(False),
    exclude_outliers: bool = Query(False),
    sort: str = Query("price"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db=Depends(get_db),
    _auth=Depends(require_auth),
):
    """Flexible search/filter endpoint for the query builder UI."""
    conditions = ["fuel_type = %s", "NOT temporary_closure"]
    params: list = [fuel_type]

    if postcode:
        conditions.append("UPPER(postcode) LIKE %s")
        params.append(postcode.upper().replace(" ", "") + "%")
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
        conditions.append("forecourt_type = %s")
        params.append(category)
    if district:
        conditions.append("admin_district = %s")
        params.append(district)
    if constituency:
        conditions.append("parliamentary_constituency = %s")
        params.append(constituency)
    if rural_urban:
        conditions.append("rural_urban = %s")
        params.append(rural_urban)
    if region:
        conditions.append("region = %s")
        params.append(region)

    where = " AND ".join(conditions)

    allowed_sorts = {"price": "price", "brand": "brand_name", "city": "city", "postcode": "postcode", "district": "admin_district"}
    order = allowed_sorts.get(sort, "price")

    with db.cursor() as cur:
        cur.execute(f"""
            SELECT node_id, trading_name, brand_name, city, county,
                   postcode, region, price, fuel_name, fuel_category,
                   forecourt_type, admin_district, parliamentary_constituency,
                   rural_urban,
                   latitude, longitude,
                   is_motorway_service_station, is_supermarket_service_station,
                   observed_at
            FROM current_prices
            WHERE {where}
            ORDER BY {order}
            LIMIT %s OFFSET %s
        """, params + [limit, offset])
        rows = cur.fetchall()

        # Get total count for pagination
        cur.execute(f"""
            SELECT COUNT(*) AS total FROM current_prices WHERE {where}
        """, params)
        total = cur.fetchone()["total"]

    return {"results": rows, "total": total, "limit": limit, "offset": offset}


@app.get("/api/anomalies")
def anomalies(
    limit: int = Query(50, ge=1, le=500),
    db=Depends(get_db),
    _auth=Depends(require_auth),
):
    """Recent anomaly-flagged price records with previous price context."""
    with db.cursor() as cur:
        cur.execute("""
            SELECT fp.id, fp.node_id, s.trading_name, s.city,
                   fp.fuel_type, fp.price, fp.anomaly_flags,
                   fp.observed_at,
                   prev.price AS prev_price,
                   prev.observed_at AS prev_observed_at
            FROM fuel_prices fp
            JOIN stations s ON s.node_id = fp.node_id
            LEFT JOIN LATERAL (
                SELECT p2.price, p2.observed_at
                FROM fuel_prices p2
                WHERE p2.node_id = fp.node_id
                  AND p2.fuel_type = fp.fuel_type
                  AND p2.observed_at < fp.observed_at
                ORDER BY p2.observed_at DESC
                LIMIT 1
            ) prev ON true
            WHERE fp.anomaly_flags IS NOT NULL
            ORDER BY fp.observed_at DESC
            LIMIT %s
        """, (limit,))
        return cur.fetchall()


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
def upsert_brand_alias(body: "BrandAliasBody", db=Depends(get_db), _auth=Depends(require_admin)):
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


@app.delete("/api/admin/brand-aliases/{raw_brand_name}")
def delete_brand_alias(raw_brand_name: str, db=Depends(get_db), _auth=Depends(require_admin)):
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
def upsert_brand_category(body: "BrandCategoryBody", db=Depends(get_db), _auth=Depends(require_admin)):
    """Create or update a brand category mapping."""
    brand = body.canonical_brand.strip()
    cat = body.forecourt_type.strip()
    allowed = {"Supermarket", "Major Oil", "Motorway Operator", "Fuel Group", "Convenience", "Independent"}
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


@app.delete("/api/admin/brand-categories/{canonical_brand}")
def delete_brand_category(canonical_brand: str, db=Depends(get_db), _auth=Depends(require_admin)):
    """Remove a brand category mapping (brand will default to Independent)."""
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
def upsert_station_override(body: "StationOverrideBody", db=Depends(get_db), _auth=Depends(require_admin)):
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
def delete_station_override(node_id: str, db=Depends(get_db), _auth=Depends(require_admin)):
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
                       ELSE COALESCE(bc.forecourt_type, 'Independent')
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
    _auth=Depends(require_admin),
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
def refresh_view(db=Depends(get_db), _auth=Depends(require_admin)):
    """Refresh the current_prices materialised view after lookup changes."""
    with db.cursor() as cur:
        cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY current_prices")
        db.commit()
    return {"status": "ok", "message": "current_prices view refreshed"}


@app.get("/api/outliers")
def outliers(
    fuel_type: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
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
            FROM current_prices
            WHERE NOT temporary_closure
              AND NOT price_is_outlier
            GROUP BY fuel_type
            ORDER BY fuel_type
        """)
        bounds = {r["fuel_type"]: r for r in cur.fetchall()}

        conditions = ["price_is_outlier", "NOT temporary_closure"]
        params: list = []
        if fuel_type:
            conditions.append("fuel_type = %s")
            params.append(fuel_type)

        where = " AND ".join(conditions)
        cur.execute(f"""
            SELECT node_id, trading_name, city, postcode, fuel_type, fuel_name,
                   price, brand_name, forecourt_type, anomaly_flags, observed_at,
                   CASE
                       WHEN anomaly_flags IS NOT NULL THEN 'anomaly_flagged'
                       ELSE 'iqr_outlier'
                   END AS exclusion_reason
            FROM current_prices
            WHERE {where}
            ORDER BY fuel_type, price
            LIMIT %s
        """, params + [limit])
        rows = cur.fetchall()

    return {
        "bounds": bounds,
        "outliers": rows,
        "total": len(rows),
    }


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
    if body.admin:
        client.admin_add_user_to_group(
            UserPoolId=pool, Username=email, GroupName="admin"
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
