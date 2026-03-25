-- =============================================================================
-- Regional fuel price analysis queries
-- =============================================================================
-- These queries join fuel_prices with stations and postcode_regions to enable
-- regional comparisons. Designed for journalism: "prices in X rose faster
-- than in Y during Z period."
--
-- Prerequisites: seed_postcode_regions.sql must be loaded.
-- =============================================================================


-- ---------------------------------------------------------------------------
-- 1. Current average price by region
-- ---------------------------------------------------------------------------
-- "Which region has the most expensive petrol right now?"

SELECT
    pr.region,
    cp.fuel_type,
    ROUND(AVG(cp.price), 1)  AS avg_price,
    ROUND(MIN(cp.price), 1)  AS cheapest,
    ROUND(MAX(cp.price), 1)  AS most_expensive,
    COUNT(*)                  AS stations
FROM current_prices cp
JOIN postcode_regions pr ON pr.postcode_area = (
    CASE WHEN LEFT(cp.postcode, 2) ~ '^[A-Z]{2}$' THEN LEFT(cp.postcode, 2)
         ELSE LEFT(cp.postcode, 1)
    END
)
WHERE cp.fuel_type = 'E10'  -- change to B7_STANDARD for diesel
GROUP BY pr.region, cp.fuel_type
ORDER BY avg_price DESC;


-- ---------------------------------------------------------------------------
-- 2. Regional price comparison (broad groups: North / Midlands / South / etc.)
-- ---------------------------------------------------------------------------

SELECT
    pr.region_group,
    cp.fuel_type,
    ROUND(AVG(cp.price), 1) AS avg_price,
    COUNT(*) AS stations
FROM current_prices cp
JOIN postcode_regions pr ON pr.postcode_area = (
    CASE WHEN LEFT(cp.postcode, 2) ~ '^[A-Z]{2}$' THEN LEFT(cp.postcode, 2)
         ELSE LEFT(cp.postcode, 1)
    END
)
WHERE cp.fuel_type IN ('E10', 'B7_STANDARD')
GROUP BY pr.region_group, cp.fuel_type
ORDER BY cp.fuel_type, avg_price DESC;


-- ---------------------------------------------------------------------------
-- 3. Daily average price by region over time
-- ---------------------------------------------------------------------------
-- "How did prices move in the North West vs London this month?"
-- Best used with several days/weeks of accumulated data.

SELECT
    DATE(fp.observed_at)     AS day,
    pr.region,
    fp.fuel_type,
    ROUND(AVG(fp.price), 1)  AS avg_price,
    COUNT(DISTINCT fp.node_id) AS stations_reporting
FROM fuel_prices fp
JOIN stations s ON s.node_id = fp.node_id
JOIN postcode_regions pr ON pr.postcode_area = (
    CASE WHEN LEFT(s.postcode, 2) ~ '^[A-Z]{2}$' THEN LEFT(s.postcode, 2)
         ELSE LEFT(s.postcode, 1)
    END
)
WHERE fp.fuel_type = 'E10'
  AND pr.region IN ('London', 'North West')
  -- AND fp.observed_at >= '2026-04-01' AND fp.observed_at < '2026-05-01'
GROUP BY day, pr.region, fp.fuel_type
ORDER BY day, pr.region;


-- ---------------------------------------------------------------------------
-- 4. Monthly average price by region (for longer-term trends)
-- ---------------------------------------------------------------------------

SELECT
    DATE_TRUNC('month', fp.observed_at) AS month,
    pr.region,
    fp.fuel_type,
    ROUND(AVG(fp.price), 1) AS avg_price
FROM fuel_prices fp
JOIN stations s ON s.node_id = fp.node_id
JOIN postcode_regions pr ON pr.postcode_area = (
    CASE WHEN LEFT(s.postcode, 2) ~ '^[A-Z]{2}$' THEN LEFT(s.postcode, 2)
         ELSE LEFT(s.postcode, 1)
    END
)
WHERE fp.fuel_type = 'E10'
GROUP BY month, pr.region, fp.fuel_type
ORDER BY month, avg_price DESC;


-- ---------------------------------------------------------------------------
-- 5. Price change velocity: which region saw the fastest rise/fall?
-- ---------------------------------------------------------------------------
-- Compare average price at start vs end of a period.
-- Adjust the date range as needed.

WITH period_prices AS (
    SELECT
        pr.region,
        fp.fuel_type,
        ROUND(AVG(fp.price) FILTER (
            WHERE fp.observed_at >= '2026-04-01' AND fp.observed_at < '2026-04-08'
        ), 1) AS avg_price_start,
        ROUND(AVG(fp.price) FILTER (
            WHERE fp.observed_at >= '2026-04-24' AND fp.observed_at < '2026-05-01'
        ), 1) AS avg_price_end
    FROM fuel_prices fp
    JOIN stations s ON s.node_id = fp.node_id
    JOIN postcode_regions pr ON pr.postcode_area = (
        CASE WHEN LEFT(s.postcode, 2) ~ '^[A-Z]{2}$' THEN LEFT(s.postcode, 2)
             ELSE LEFT(s.postcode, 1)
        END
    )
    WHERE fp.fuel_type = 'E10'
    GROUP BY pr.region, fp.fuel_type
)
SELECT
    region,
    fuel_type,
    avg_price_start,
    avg_price_end,
    avg_price_end - avg_price_start AS change_ppl,
    ROUND((avg_price_end - avg_price_start) / avg_price_start * 100, 2) AS change_pct
FROM period_prices
WHERE avg_price_start IS NOT NULL AND avg_price_end IS NOT NULL
ORDER BY change_ppl DESC;


-- ---------------------------------------------------------------------------
-- 6. Supermarket vs non-supermarket by region
-- ---------------------------------------------------------------------------

SELECT
    pr.region,
    CASE WHEN cp.is_supermarket_service_station THEN 'Supermarket' ELSE 'Non-supermarket' END AS station_type,
    cp.fuel_type,
    ROUND(AVG(cp.price), 1) AS avg_price,
    COUNT(*) AS stations
FROM current_prices cp
JOIN postcode_regions pr ON pr.postcode_area = (
    CASE WHEN LEFT(cp.postcode, 2) ~ '^[A-Z]{2}$' THEN LEFT(cp.postcode, 2)
         ELSE LEFT(cp.postcode, 1)
    END
)
WHERE cp.fuel_type = 'E10'
GROUP BY pr.region, station_type, cp.fuel_type
ORDER BY pr.region, station_type;


-- ---------------------------------------------------------------------------
-- 7. Motorway premium by region
-- ---------------------------------------------------------------------------
-- "How much more do motorists pay at motorway services?"

SELECT
    pr.region,
    ROUND(AVG(cp.price) FILTER (WHERE cp.is_motorway_service_station), 1) AS motorway_avg,
    ROUND(AVG(cp.price) FILTER (WHERE NOT cp.is_motorway_service_station), 1) AS non_motorway_avg,
    ROUND(
        AVG(cp.price) FILTER (WHERE cp.is_motorway_service_station) -
        AVG(cp.price) FILTER (WHERE NOT cp.is_motorway_service_station),
    1) AS motorway_premium_ppl
FROM current_prices cp
JOIN postcode_regions pr ON pr.postcode_area = (
    CASE WHEN LEFT(cp.postcode, 2) ~ '^[A-Z]{2}$' THEN LEFT(cp.postcode, 2)
         ELSE LEFT(cp.postcode, 1)
    END
)
WHERE cp.fuel_type = 'E10'
GROUP BY pr.region
HAVING COUNT(*) FILTER (WHERE cp.is_motorway_service_station) > 0
ORDER BY motorway_premium_ppl DESC;


-- ---------------------------------------------------------------------------
-- 8. Stations with unmapped postcodes (data quality check)
-- ---------------------------------------------------------------------------

SELECT s.postcode, s.city, s.country, COUNT(*) AS stations
FROM stations s
LEFT JOIN postcode_regions pr ON pr.postcode_area = (
    CASE WHEN LEFT(s.postcode, 2) ~ '^[A-Z]{2}$' THEN LEFT(s.postcode, 2)
         ELSE LEFT(s.postcode, 1)
    END
)
WHERE pr.postcode_area IS NULL
GROUP BY s.postcode, s.city, s.country
ORDER BY stations DESC;
