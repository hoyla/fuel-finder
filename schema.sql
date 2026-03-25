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
