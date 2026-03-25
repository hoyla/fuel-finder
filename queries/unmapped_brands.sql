-- Find brand names that have no alias and no station-level override.
-- Use this to identify new brands that need mapping.
-- Run periodically after scrapes to catch new entries.

SELECT
    s.brand_name AS unmapped_brand,
    COUNT(*) AS station_count
FROM stations s
LEFT JOIN brand_aliases ba ON ba.raw_brand_name = s.brand_name
LEFT JOIN station_brand_overrides sbo ON sbo.node_id = s.node_id
WHERE ba.canonical_brand IS NULL
  AND sbo.canonical_brand IS NULL
  AND s.brand_name IS NOT NULL
GROUP BY s.brand_name
ORDER BY station_count DESC;
