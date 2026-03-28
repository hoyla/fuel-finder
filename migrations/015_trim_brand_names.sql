-- Migration 015: trim whitespace from brand_name in stations and raw_brand_name in brand_aliases
--
-- The GOV.UK Fuel Finder API occasionally returns brand strings with leading/
-- trailing whitespace (e.g. "shell " instead of "shell"). The alias upsert
-- endpoint already strips input, but existing rows were stored verbatim.
-- This migration normalises both tables so alias JOINs match correctly.

UPDATE stations
   SET brand_name = TRIM(brand_name)
 WHERE brand_name IS NOT NULL
   AND brand_name != TRIM(brand_name);

UPDATE brand_aliases
   SET raw_brand_name = TRIM(raw_brand_name),
       canonical_brand = TRIM(canonical_brand)
 WHERE raw_brand_name != TRIM(raw_brand_name)
    OR canonical_brand != TRIM(canonical_brand);
