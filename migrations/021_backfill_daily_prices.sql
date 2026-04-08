-- Backfill daily_prices for any dates missing since the initial migration 018
-- backfill.  The scraper image in ECR predated the refresh_daily_prices()
-- call so no new rows were written after the 018 backfill ran.

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
ON CONFLICT (node_id, fuel_type, price_date) DO UPDATE SET
    avg_price    = EXCLUDED.avg_price,
    min_price    = EXCLUDED.min_price,
    max_price    = EXCLUDED.max_price,
    sample_count = EXCLUDED.sample_count;
