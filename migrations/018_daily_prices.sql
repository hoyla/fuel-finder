-- Pre-aggregated daily price summaries per station per fuel type.
-- Avoids repeated GROUP BY on the large fuel_prices table for daily
-- trend queries.  Maintained by the scraper after each run and by the
-- API after price corrections.

CREATE TABLE IF NOT EXISTS daily_prices (
    node_id    TEXT    NOT NULL,
    fuel_type  TEXT    NOT NULL,
    price_date DATE    NOT NULL,
    avg_price  NUMERIC(6,1) NOT NULL,
    min_price  NUMERIC(6,1) NOT NULL,
    max_price  NUMERIC(6,1) NOT NULL,
    sample_count INTEGER NOT NULL,
    PRIMARY KEY (node_id, fuel_type, price_date)
);

-- Covers aggregate trend queries: WHERE fuel_type = X ORDER BY price_date
CREATE INDEX idx_daily_prices_fuel_date
    ON daily_prices (fuel_type, price_date);

-- Backfill from existing data (corrections applied, anomalies excluded)
INSERT INTO daily_prices (node_id, fuel_type, price_date, avg_price, min_price, max_price, sample_count)
SELECT fp.node_id,
       fp.fuel_type,
       DATE(fp.observed_at),
       ROUND(AVG(COALESCE(pc.corrected_price, fp.price))::numeric, 1),
       ROUND(MIN(COALESCE(pc.corrected_price, fp.price))::numeric, 1),
       ROUND(MAX(COALESCE(pc.corrected_price, fp.price))::numeric, 1),
       COUNT(*)
FROM fuel_prices fp
LEFT JOIN price_corrections pc ON pc.fuel_price_id = fp.id
WHERE fp.anomaly_flags IS NULL
GROUP BY fp.node_id, fp.fuel_type, DATE(fp.observed_at)
ON CONFLICT DO NOTHING;
