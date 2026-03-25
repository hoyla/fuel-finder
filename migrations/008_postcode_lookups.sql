-- Postcode geographic enrichment from postcodes.io.
-- Caches rich geographic/administrative data per postcode.
-- Used to replace our crude postcode_area->region mapping with
-- authoritative coordinates, local authority, parliamentary constituency,
-- rural/urban classification, LSOA, etc.

CREATE TABLE IF NOT EXISTS postcode_lookups (
    postcode            TEXT PRIMARY KEY,
    -- Authoritative coordinates (replace API-reported lat/lng)
    pc_latitude         DOUBLE PRECISION,
    pc_longitude        DOUBLE PRECISION,
    -- Administrative geography
    admin_district      TEXT,           -- e.g. "Torfaen", "Camden"
    admin_county        TEXT,           -- e.g. "Hampshire" (NULL for unitaries)
    admin_ward          TEXT,           -- e.g. "Pontypool Fawr"
    parish              TEXT,           -- e.g. "Pontymoile"
    -- Political
    parliamentary_constituency TEXT,    -- e.g. "Torfaen"
    -- ONS region (England only; NULL for Scotland/Wales/NI)
    ons_region          TEXT,           -- e.g. "South East"
    country             TEXT,           -- e.g. "England", "Wales", "Scotland"
    -- Rural/Urban classification
    rural_urban         TEXT,           -- e.g. "Urban city and town"
    rural_urban_code    TEXT,           -- e.g. "C1" (2011) or "UN1" (2021)
    -- Statistical areas
    lsoa                TEXT,           -- Lower Super Output Area name
    msoa                TEXT,           -- Middle Super Output Area name
    -- Built-up area
    built_up_area       TEXT,           -- e.g. "Pontypool"
    -- Quality indicator from postcodes.io (1=best, 9=worst)
    quality             INTEGER,
    -- Misc
    looked_up_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_postcode_lookups_district
    ON postcode_lookups (admin_district);
CREATE INDEX IF NOT EXISTS idx_postcode_lookups_constituency
    ON postcode_lookups (parliamentary_constituency);
CREATE INDEX IF NOT EXISTS idx_postcode_lookups_rural_urban
    ON postcode_lookups (rural_urban);
CREATE INDEX IF NOT EXISTS idx_postcode_lookups_country
    ON postcode_lookups (country);
