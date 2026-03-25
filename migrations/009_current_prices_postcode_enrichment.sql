-- Rebuild current_prices view to include postcodes.io enrichment.
-- Key changes:
--   1. Use authoritative lat/lng from postcode_lookups (fixes North Sea stations)
--   2. Add admin_district, parliamentary_constituency, rural_urban, built_up_area
--   3. Replace crude postcode_area region with postcodes.io ons_region + country
--   4. Keep fallback to API-reported coords if postcodes.io lookup failed

DROP MATERIALIZED VIEW IF EXISTS current_prices;

CREATE MATERIALIZED VIEW current_prices AS
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
    -- Region: prefer postcodes.io ons_region, fall back to postcode_regions table
    COALESCE(pl.ons_region, pr.region) AS region,
    pr.region_group,
    -- Coordinates: prefer postcodes.io authoritative coords, fall back to API
    COALESCE(pl.pc_latitude, s.latitude) AS latitude,
    COALESCE(pl.pc_longitude, s.longitude) AS longitude,
    s.is_motorway_service_station,
    s.is_supermarket_service_station,
    s.temporary_closure,
    -- New enrichment fields from postcodes.io
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

CREATE UNIQUE INDEX idx_current_prices_node_fuel
    ON current_prices (node_id, fuel_type);
CREATE INDEX idx_current_prices_fuel_price
    ON current_prices (fuel_type, price);
CREATE INDEX idx_current_prices_postcode
    ON current_prices (postcode);
CREATE INDEX idx_current_prices_region
    ON current_prices (region, fuel_type);
CREATE INDEX idx_current_prices_forecourt_type
    ON current_prices (forecourt_type, fuel_type);
CREATE INDEX idx_current_prices_admin_district
    ON current_prices (admin_district, fuel_type);
CREATE INDEX idx_current_prices_constituency
    ON current_prices (parliamentary_constituency, fuel_type);
CREATE INDEX idx_current_prices_rural_urban
    ON current_prices (rural_urban, fuel_type);
