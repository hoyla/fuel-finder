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
    scrape_run_id       BIGINT REFERENCES scrape_runs(id),
    anomaly_flags       TEXT[]       -- NULL = no issues; populated by anomaly checks
);

-- Index for efficient time-series queries on fuel prices
CREATE INDEX IF NOT EXISTS idx_fuel_prices_node_fuel_observed
    ON fuel_prices (node_id, fuel_type, observed_at DESC);

CREATE INDEX IF NOT EXISTS idx_fuel_prices_observed
    ON fuel_prices (observed_at DESC);

CREATE INDEX IF NOT EXISTS idx_fuel_prices_fuel_observed_covering
    ON fuel_prices (fuel_type, observed_at DESC)
    INCLUDE (price, node_id, anomaly_flags);

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

-- Human-friendly fuel type names.
-- Translates API codes like 'B7_STANDARD' to readable labels.
-- Seeded from seed_fuel_types.sql.

CREATE TABLE IF NOT EXISTS fuel_type_labels (
    fuel_type_code      TEXT PRIMARY KEY,
    fuel_name           TEXT NOT NULL,
    fuel_category       TEXT NOT NULL,  -- 'Petrol' or 'Diesel'
    description         TEXT
);

-- Forecourt type categorisation via canonical brand.
-- Classifies stations into meaningful groups for price comparison.

CREATE TABLE IF NOT EXISTS brand_categories (
    canonical_brand     TEXT PRIMARY KEY,
    forecourt_type      TEXT NOT NULL  -- 'Supermarket', 'Major Oil', 'Motorway Operator', 'Independent'
);

-- Postcode geographic enrichment from postcodes.io.
-- Caches rich geographic/administrative data per postcode.

CREATE TABLE IF NOT EXISTS postcode_lookups (
    postcode            TEXT PRIMARY KEY,
    pc_latitude         DOUBLE PRECISION,
    pc_longitude        DOUBLE PRECISION,
    admin_district      TEXT,
    admin_county        TEXT,
    admin_ward          TEXT,
    parish              TEXT,
    parliamentary_constituency TEXT,
    ons_region          TEXT,
    country             TEXT,
    rural_urban         TEXT,
    rural_urban_code    TEXT,
    lsoa                TEXT,
    msoa                TEXT,
    built_up_area       TEXT,
    quality             INTEGER,
    looked_up_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_postcode_lookups_district
    ON postcode_lookups (admin_district);
CREATE INDEX IF NOT EXISTS idx_postcode_lookups_constituency
    ON postcode_lookups (parliamentary_constituency);
CREATE INDEX IF NOT EXISTS idx_postcode_lookups_rural_urban
    ON postcode_lookups (rural_urban);
CREATE INDEX IF NOT EXISTS idx_postcode_lookups_country
    ON postcode_lookups (country);

-- Materialised view: current price per station + fuel type.
-- The last-reported price IS the current price, regardless of how old it is.
-- Uses canonical brand: override > alias > raw brand_name.
-- Coordinates: prefer postcodes.io authoritative coords, fall back to API.
-- Refresh this after each scrape run.
CREATE MATERIALIZED VIEW IF NOT EXISTS current_prices AS
SELECT DISTINCT ON (fp.node_id, fp.fuel_type)
    fp.node_id,
    fp.fuel_type,
    COALESCE(ftl.fuel_name, fp.fuel_type) AS fuel_name,
    COALESCE(ftl.fuel_category, 'Unknown') AS fuel_category,
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
    CASE
        WHEN s.is_motorway_service_station THEN 'Motorway'
        ELSE COALESCE(bc.forecourt_type, 'Independent')
    END AS forecourt_type,
    s.city,
    s.county,
    COALESCE(pl.country, s.country) AS country,
    s.postcode,
    COALESCE(pl.ons_region, pr.region) AS region,
    pr.region_group,
    COALESCE(pl.pc_latitude, s.latitude) AS latitude,
    COALESCE(pl.pc_longitude, s.longitude) AS longitude,
    s.is_motorway_service_station,
    s.is_supermarket_service_station,
    s.temporary_closure,
    pl.admin_district,
    pl.admin_county,
    pl.admin_ward,
    pl.parliamentary_constituency,
    pl.rural_urban,
    pl.built_up_area,
    pl.lsoa,
    pl.msoa
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
LEFT JOIN fuel_type_labels ftl ON ftl.fuel_type_code = fp.fuel_type
LEFT JOIN brand_categories bc ON bc.canonical_brand = COALESCE(
    sbo.canonical_brand,
    ba.canonical_brand,
    s.brand_name
)
LEFT JOIN postcode_lookups pl ON pl.postcode = s.postcode
ORDER BY fp.node_id, fp.fuel_type, fp.observed_at DESC;

CREATE UNIQUE INDEX IF NOT EXISTS idx_current_prices_node_fuel
    ON current_prices (node_id, fuel_type);

CREATE INDEX IF NOT EXISTS idx_current_prices_fuel_price
    ON current_prices (fuel_type, price);

CREATE INDEX IF NOT EXISTS idx_current_prices_postcode
    ON current_prices (postcode);

CREATE INDEX IF NOT EXISTS idx_current_prices_region
    ON current_prices (region, fuel_type);

CREATE INDEX IF NOT EXISTS idx_current_prices_forecourt_type
    ON current_prices (forecourt_type, fuel_type);

CREATE INDEX IF NOT EXISTS idx_current_prices_admin_district
    ON current_prices (admin_district, fuel_type);

CREATE INDEX IF NOT EXISTS idx_current_prices_constituency
    ON current_prices (parliamentary_constituency, fuel_type);

CREATE INDEX IF NOT EXISTS idx_current_prices_rural_urban
    ON current_prices (rural_urban, fuel_type);

CREATE INDEX IF NOT EXISTS idx_current_prices_brand_lower
    ON current_prices (lower(brand_name) text_pattern_ops);

CREATE INDEX IF NOT EXISTS idx_current_prices_country
    ON current_prices (country, fuel_type);
