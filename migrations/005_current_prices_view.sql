-- Materialised view: current price per station + fuel type.
-- Separated from base schema because it depends on all tables existing.

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
LEFT JOIN fuel_type_labels ftl ON ftl.fuel_type_code = fp.fuel_type
ORDER BY fp.node_id, fp.fuel_type, fp.observed_at DESC;

CREATE UNIQUE INDEX IF NOT EXISTS idx_current_prices_node_fuel
    ON current_prices (node_id, fuel_type);
CREATE INDEX IF NOT EXISTS idx_current_prices_fuel_price
    ON current_prices (fuel_type, price);
CREATE INDEX IF NOT EXISTS idx_current_prices_postcode
    ON current_prices (postcode);
CREATE INDEX IF NOT EXISTS idx_current_prices_region
    ON current_prices (region, fuel_type);
