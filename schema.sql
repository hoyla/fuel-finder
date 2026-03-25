CREATE TABLE IF NOT EXISTS scrape_runs (
    id                  BIGSERIAL PRIMARY KEY,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at         TIMESTAMPTZ,
    run_type            TEXT NOT NULL DEFAULT 'full',  -- 'full' or 'incremental'
    batches_fetched     INTEGER,
    stations_count      INTEGER,
    price_records_count INTEGER,
    s3_key              TEXT,
    status              TEXT NOT NULL DEFAULT 'running',  -- 'running', 'completed', 'failed'
    error_message       TEXT
);

CREATE TABLE IF NOT EXISTS stations (
    node_id             TEXT PRIMARY KEY,
    trading_name        TEXT NOT NULL,
    brand_name          TEXT,
    is_same_trading_and_brand_name BOOLEAN,
    public_phone_number TEXT,
    temporary_closure   BOOLEAN DEFAULT FALSE,
    permanent_closure   BOOLEAN,
    permanent_closure_date DATE,
    is_motorway_service_station    BOOLEAN DEFAULT FALSE,
    is_supermarket_service_station BOOLEAN DEFAULT FALSE,
    address_line_1      TEXT,
    address_line_2      TEXT,
    city                TEXT,
    county              TEXT,
    country             TEXT,
    postcode            TEXT,
    latitude            DOUBLE PRECISION,
    longitude           DOUBLE PRECISION,
    amenities           TEXT[],
    fuel_types          TEXT[],
    opening_times       JSONB,
    first_seen          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_updated        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS fuel_prices (
    id                  BIGSERIAL PRIMARY KEY,
    node_id             TEXT NOT NULL REFERENCES stations(node_id),
    fuel_type           TEXT NOT NULL,
    price               NUMERIC(6,1) NOT NULL,
    price_last_updated  TIMESTAMPTZ,
    price_change_effective_timestamp TIMESTAMPTZ,
    observed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    scrape_run_id       BIGINT REFERENCES scrape_runs(id)
);

-- Index for efficient time-series queries on fuel prices
CREATE INDEX IF NOT EXISTS idx_fuel_prices_node_fuel_observed
    ON fuel_prices (node_id, fuel_type, observed_at DESC);

CREATE INDEX IF NOT EXISTS idx_fuel_prices_observed
    ON fuel_prices (observed_at DESC);

CREATE INDEX IF NOT EXISTS idx_fuel_prices_fuel_type
    ON fuel_prices (fuel_type, observed_at DESC);

-- Index for geographic queries on stations
CREATE INDEX IF NOT EXISTS idx_stations_latlon
    ON stations (latitude, longitude);

CREATE INDEX IF NOT EXISTS idx_stations_postcode
    ON stations (postcode);

-- Brand cleanup: two-tier canonical brand resolution.
-- 1. brand_aliases: maps raw API brand strings to a canonical name (bulk cleanup)
-- 2. station_brand_overrides: per-station overrides for edge cases
-- Resolution order: override > alias > raw brand_name

CREATE TABLE IF NOT EXISTS brand_aliases (
    raw_brand_name      TEXT PRIMARY KEY,
    canonical_brand     TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS station_brand_overrides (
    node_id             TEXT PRIMARY KEY REFERENCES stations(node_id),
    canonical_brand     TEXT NOT NULL,
    notes               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Regional grouping via postcode area prefix.
-- Maps the 1-2 letter prefix of a postcode to an ONS-style region.
-- Seeded from seed_postcode_regions.sql.

CREATE TABLE IF NOT EXISTS postcode_regions (
    postcode_area       TEXT PRIMARY KEY,
    region              TEXT NOT NULL,
    region_group        TEXT NOT NULL
);

-- Materialised view: current price per station + fuel type.
-- The last-reported price IS the current price, regardless of how old it is.
-- Uses canonical brand: override > alias > raw brand_name.
-- Refresh this after each scrape run.
CREATE MATERIALIZED VIEW IF NOT EXISTS current_prices AS
SELECT DISTINCT ON (fp.node_id, fp.fuel_type)
    fp.node_id,
    fp.fuel_type,
    fp.price,
    fp.price_last_updated,
    fp.price_change_effective_timestamp,
    fp.observed_at,
    s.trading_name,
    s.brand_name AS raw_brand_name,
    COALESCE(
        sbo.canonical_brand,
        ba.canonical_brand,
        s.brand_name
    ) AS brand_name,
    s.city,
    s.county,
    s.country,
    s.postcode,
    pr.region,
    pr.region_group,
    s.latitude,
    s.longitude,
    s.is_motorway_service_station,
    s.is_supermarket_service_station,
    s.temporary_closure
FROM fuel_prices fp
JOIN stations s ON s.node_id = fp.node_id
LEFT JOIN brand_aliases ba ON ba.raw_brand_name = s.brand_name
LEFT JOIN station_brand_overrides sbo ON sbo.node_id = fp.node_id
LEFT JOIN postcode_regions pr ON pr.postcode_area = (
    CASE
        WHEN LEFT(s.postcode, 2) ~ '^[A-Z]{2}$' THEN LEFT(s.postcode, 2)
        ELSE LEFT(s.postcode, 1)
    END
)
ORDER BY fp.node_id, fp.fuel_type, fp.observed_at DESC;

CREATE UNIQUE INDEX IF NOT EXISTS idx_current_prices_node_fuel
    ON current_prices (node_id, fuel_type);

CREATE INDEX IF NOT EXISTS idx_current_prices_fuel_price
    ON current_prices (fuel_type, price);

CREATE INDEX IF NOT EXISTS idx_current_prices_postcode
    ON current_prices (postcode);

CREATE INDEX IF NOT EXISTS idx_current_prices_region
    ON current_prices (region, fuel_type);
