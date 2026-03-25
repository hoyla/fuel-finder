-- =============================================================================
-- Anomaly detection queries
-- =============================================================================
-- Find suspicious price entries that are likely data entry errors.
-- Anomalies are flagged on insert (anomaly_flags column) but these queries
-- can also be used to scan historical data.
-- =============================================================================


-- ---------------------------------------------------------------------------
-- 1. All flagged anomalies (most recent first)
-- ---------------------------------------------------------------------------

SELECT
    fp.observed_at,
    s.trading_name,
    s.city,
    s.postcode,
    fp.fuel_type,
    fp.price,
    fp.anomaly_flags
FROM fuel_prices fp
JOIN stations s ON s.node_id = fp.node_id
WHERE fp.anomaly_flags IS NOT NULL
ORDER BY fp.observed_at DESC;


-- ---------------------------------------------------------------------------
-- 2. Prices outside plausible range (likely decimal place errors)
-- ---------------------------------------------------------------------------
-- UK fuel hasn't been below 80p since ~2004 or above 300p ever.

SELECT
    fp.observed_at,
    s.trading_name,
    s.city,
    fp.fuel_type,
    fp.price,
    CASE
        WHEN fp.price < 80 AND fp.price * 10 BETWEEN 80 AND 300
            THEN fp.price * 10
        WHEN fp.price > 300 AND fp.price / 10 BETWEEN 80 AND 300
            THEN fp.price / 10
    END AS likely_intended_price,
    CASE
        WHEN fp.price < 80  THEN 'Too low — decimal point likely 1 place right'
        WHEN fp.price > 300 THEN 'Too high — decimal point likely 1 place left'
    END AS diagnosis
FROM fuel_prices fp
JOIN stations s ON s.node_id = fp.node_id
WHERE fp.price < 80 OR fp.price > 300
ORDER BY fp.observed_at DESC;


-- ---------------------------------------------------------------------------
-- 3. Suspiciously large price jumps (>30% change from previous)
-- ---------------------------------------------------------------------------

SELECT
    fp.observed_at,
    s.trading_name,
    s.city,
    fp.fuel_type,
    prev.price AS previous_price,
    fp.price AS new_price,
    ROUND((fp.price - prev.price) / prev.price * 100, 1) AS change_pct
FROM fuel_prices fp
JOIN stations s ON s.node_id = fp.node_id
JOIN LATERAL (
    SELECT price FROM fuel_prices p2
    WHERE p2.node_id = fp.node_id
      AND p2.fuel_type = fp.fuel_type
      AND p2.observed_at < fp.observed_at
    ORDER BY p2.observed_at DESC
    LIMIT 1
) prev ON true
WHERE ABS(fp.price - prev.price) / prev.price > 0.30
ORDER BY ABS(fp.price - prev.price) / prev.price DESC;


-- ---------------------------------------------------------------------------
-- 4. Prices that look like pounds instead of pence (e.g. 1.39 instead of 139)
-- ---------------------------------------------------------------------------

SELECT
    fp.observed_at,
    s.trading_name,
    s.city,
    fp.fuel_type,
    fp.price,
    fp.price * 100 AS if_pounds_to_pence
FROM fuel_prices fp
JOIN stations s ON s.node_id = fp.node_id
WHERE fp.price < 10
ORDER BY fp.observed_at DESC;


-- ---------------------------------------------------------------------------
-- 5. Duplicate/unchanged timestamps (station re-submitting same data)
-- ---------------------------------------------------------------------------

SELECT
    s.trading_name,
    s.city,
    fp.fuel_type,
    fp.price_change_effective_timestamp,
    COUNT(*) AS times_seen
FROM fuel_prices fp
JOIN stations s ON s.node_id = fp.node_id
GROUP BY s.trading_name, s.city, fp.fuel_type, fp.price_change_effective_timestamp
HAVING COUNT(*) > 3
ORDER BY times_seen DESC;


-- ---------------------------------------------------------------------------
-- 6. Summary: anomaly counts by type
-- ---------------------------------------------------------------------------

SELECT
    unnest(anomaly_flags) AS flag,
    COUNT(*) AS occurrences
FROM fuel_prices
WHERE anomaly_flags IS NOT NULL
GROUP BY flag
ORDER BY occurrences DESC;


-- ---------------------------------------------------------------------------
-- 7. Stations with most anomalies (repeat offenders)
-- ---------------------------------------------------------------------------

SELECT
    s.trading_name,
    s.city,
    s.postcode,
    COUNT(*) AS anomaly_count
FROM fuel_prices fp
JOIN stations s ON s.node_id = fp.node_id
WHERE fp.anomaly_flags IS NOT NULL
GROUP BY s.trading_name, s.city, s.postcode
ORDER BY anomaly_count DESC
LIMIT 20;
