"""FastAPI backend for the Fuel Finder web UI.

Provides read-only API endpoints over the PostgreSQL database.
Auth is stubbed out for future use (JWT / API key).
"""

import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# ---------------------------------------------------------------------------
# Database pool (simple: one connection per request, fine for low traffic)
# ---------------------------------------------------------------------------

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://fuelfinder:fuelfinder@localhost:5432/fuelfinder",
)


def get_db():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Auth stub — no-op for local dev, swap in real middleware later
# ---------------------------------------------------------------------------

AUTH_ENABLED = os.environ.get("AUTH_ENABLED", "false").lower() == "true"


async def require_auth(request: Request):
    """Placeholder auth dependency. Enable via AUTH_ENABLED=true env var."""
    if not AUTH_ENABLED:
        return
    token = request.headers.get("Authorization", "")
    if not token.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    # TODO: validate JWT / API key here
    # For now, any bearer token is accepted when auth is enabled
    return


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Fuel Finder", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in production
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.get("/api/summary")
def summary(db=Depends(get_db), _auth=Depends(require_auth)):
    """Dashboard headline numbers."""
    with db.cursor() as cur:
        cur.execute("""
            SELECT
                fuel_type,
                fuel_name,
                ROUND(AVG(price)::numeric, 1) AS avg_price,
                MIN(price) AS min_price,
                MAX(price) AS max_price,
                COUNT(*) AS station_count
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
                   ROUND(AVG(price)::numeric, 1) AS avg_price,
                   MIN(price) AS min_price,
                   MAX(price) AS max_price,
                   COUNT(*) AS station_count
            FROM current_prices
            WHERE fuel_type = %s
              AND brand_name IS NOT NULL
              AND NOT temporary_closure
            GROUP BY brand_name
            HAVING COUNT(*) >= 3
            ORDER BY avg_price
            LIMIT %s
        """, (fuel_type, limit))
        return cur.fetchall()


@app.get("/api/prices/history")
def price_history(
    fuel_type: str = Query("E10"),
    days: int = Query(30, ge=1, le=365),
    region: Optional[str] = Query(None),
    db=Depends(get_db),
    _auth=Depends(require_auth),
):
    """Daily average price over time, optionally filtered by region."""
    with db.cursor() as cur:
        if region:
            cur.execute("""
                SELECT DATE(fp.observed_at) AS day,
                       ROUND(AVG(fp.price)::numeric, 1) AS avg_price,
                       COUNT(DISTINCT fp.node_id) AS stations
                FROM fuel_prices fp
                JOIN stations s ON s.node_id = fp.node_id
                LEFT JOIN postcode_regions pr ON pr.postcode_area = (
                    CASE WHEN LEFT(s.postcode, 2) ~ '^[A-Z]{2}$'
                         THEN LEFT(s.postcode, 2) ELSE LEFT(s.postcode, 1) END
                )
                WHERE fp.fuel_type = %s
                  AND fp.observed_at >= NOW() - make_interval(days => %s)
                  AND pr.region = %s
                  AND fp.anomaly_flags IS NULL
                GROUP BY DATE(fp.observed_at)
                ORDER BY day
            """, (fuel_type, days, region))
        else:
            cur.execute("""
                SELECT DATE(fp.observed_at) AS day,
                       ROUND(AVG(fp.price)::numeric, 1) AS avg_price,
                       COUNT(DISTINCT fp.node_id) AS stations
                FROM fuel_prices fp
                WHERE fp.fuel_type = %s
                  AND fp.observed_at >= NOW() - make_interval(days => %s)
                  AND fp.anomaly_flags IS NULL
                GROUP BY DATE(fp.observed_at)
                ORDER BY day
            """, (fuel_type, days))
        return cur.fetchall()


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
                   price, fuel_name,
                   latitude, longitude,
                   is_motorway_service_station, is_supermarket_service_station
            FROM current_prices
            WHERE fuel_type = %s
              AND latitude IS NOT NULL
              AND longitude IS NOT NULL
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
    supermarket_only: bool = Query(False),
    motorway_only: bool = Query(False),
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

    where = " AND ".join(conditions)

    allowed_sorts = {"price": "price", "brand": "brand_name", "city": "city", "postcode": "postcode"}
    order = allowed_sorts.get(sort, "price")

    with db.cursor() as cur:
        cur.execute(f"""
            SELECT node_id, trading_name, brand_name, city, county,
                   postcode, region, price, fuel_name, fuel_category,
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
    """Recent anomaly-flagged price records."""
    with db.cursor() as cur:
        cur.execute("""
            SELECT fp.id, fp.node_id, s.trading_name, s.city,
                   fp.fuel_type, fp.price, fp.anomaly_flags,
                   fp.observed_at
            FROM fuel_prices fp
            JOIN stations s ON s.node_id = fp.node_id
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


# ---------------------------------------------------------------------------
# Serve static frontend
# ---------------------------------------------------------------------------

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def index():
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))
