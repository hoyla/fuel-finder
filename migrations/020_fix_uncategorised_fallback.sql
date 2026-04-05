-- Fix regression introduced in 019_postcode_overrides.sql which reverted
-- the forecourt_type fallback from 'Uncategorised' back to 'Independent'.
--
-- 'Uncategorised' is for unmapped brands; 'Independent' is reserved for
-- brands explicitly assigned that category in brand_categories.

DROP MATERIALIZED VIEW IF EXISTS current_prices;

CREATE MATERIALIZED VIEW current_prices AS
WITH latest AS (
    SELECT DISTINCT ON (fp.node_id, fp.fuel_type)
        fp.node_id,
        fp.fuel_type,
        fp.price AS original_price,
        COALESCE(pc.corrected_price, fp.price) AS price,
        CASE WHEN pc.id IS NOT NULL THEN NULL ELSE fp.anomaly_flags END AS anomaly_flags,
        fp.price_last_updated,
        fp.price_change_effective_timestamp,
        fp.observed_at
    FROM fuel_prices fp
    LEFT JOIN price_corrections pc ON pc.fuel_price_id = fp.id
    ORDER BY fp.node_id, fp.fuel_type, fp.observed_at DESC
),
bounds AS (
    SELECT
        l.fuel_type,
        PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY l.price) AS q1,
        PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY l.price) AS q3
    FROM latest l
    JOIN stations s ON s.node_id = l.node_id
    WHERE l.anomaly_flags IS NULL
      AND NOT s.temporary_closure
    GROUP BY l.fuel_type
)
SELECT
    l.node_id,
    l.fuel_type,
    COALESCE(ftl.fuel_name, l.fuel_type) AS fuel_name,
    COALESCE(ftl.fuel_category, 'Unknown') AS fuel_category,
    l.price,
    l.price_last_updated,
    l.price_change_effective_timestamp,
    l.observed_at,
    s.trading_name,
    s.brand_name AS raw_brand_name,
    COALESCE(
        sbo.canonical_brand,
        ba.canonical_brand,
        s.brand_name
    ) AS brand_name,
    CASE
        WHEN s.is_motorway_service_station THEN 'Motorway'
        ELSE COALESCE(bc.forecourt_type, 'Uncategorised')
    END AS forecourt_type,
    s.city,
    s.county,
    COALESCE(pl.country, s.country) AS country,
    COALESCE(spo.corrected_postcode, s.postcode) AS postcode,
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
    pl.msoa,
    l.anomaly_flags,
    CASE
        WHEN l.anomaly_flags IS NOT NULL THEN true
        WHEN b.q1 IS NOT NULL
             AND l.price < b.q1 - 1.5 * (b.q3 - b.q1) THEN true
        WHEN b.q3 IS NOT NULL
             AND l.price > b.q3 + 1.5 * (b.q3 - b.q1) THEN true
        ELSE false
    END AS price_is_outlier
FROM latest l
JOIN stations s ON s.node_id = l.node_id
LEFT JOIN brand_aliases ba ON ba.raw_brand_name = s.brand_name
LEFT JOIN station_brand_overrides sbo ON sbo.node_id = l.node_id
LEFT JOIN station_postcode_overrides spo ON spo.node_id = l.node_id
LEFT JOIN postcode_regions pr ON pr.postcode_area = (
    CASE
        WHEN LEFT(COALESCE(spo.corrected_postcode, s.postcode), 2) ~ '^[A-Z]{2}$'
        THEN LEFT(COALESCE(spo.corrected_postcode, s.postcode), 2)
        ELSE LEFT(COALESCE(spo.corrected_postcode, s.postcode), 1)
    END
)
LEFT JOIN fuel_type_labels ftl ON ftl.fuel_type_code = l.fuel_type
LEFT JOIN brand_categories bc ON bc.canonical_brand = COALESCE(
    sbo.canonical_brand,
    ba.canonical_brand,
    s.brand_name
)
LEFT JOIN postcode_lookups pl ON pl.postcode = COALESCE(spo.corrected_postcode, s.postcode)
LEFT JOIN bounds b ON b.fuel_type = l.fuel_type;

CREATE UNIQUE INDEX idx_current_prices_node_fuel
    ON current_prices (node_id, fuel_type);
CREATE INDEX idx_current_prices_fuel_price
    ON current_prices (fuel_type, price);
CREATE INDEX idx_current_prices_postcode
    ON current_prices (postcode);
