-- Price corrections table: stores manual overrides for misreported prices.
-- Original prices in fuel_prices are never modified.

CREATE TABLE IF NOT EXISTS price_corrections (
    id                  BIGSERIAL PRIMARY KEY,
    fuel_price_id       BIGINT NOT NULL REFERENCES fuel_prices(id),
    original_price      NUMERIC(6,1) NOT NULL,
    corrected_price     NUMERIC(6,1) NOT NULL,
    reason              TEXT,
    corrected_by        TEXT,
    corrected_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (fuel_price_id)
);

CREATE INDEX IF NOT EXISTS idx_price_corrections_fuel_price_id
    ON price_corrections (fuel_price_id);

-- Index for looking up corrections by station (via fuel_prices join)
CREATE INDEX IF NOT EXISTS idx_price_corrections_corrected_at
    ON price_corrections (corrected_at DESC);
