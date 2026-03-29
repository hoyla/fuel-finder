-- Postcode area to region mapping.
-- Postcode areas are the 1-2 letter prefix of a UK postcode (e.g. "SW" from "SW1A 1AA").
-- Maps to ONS-style regions for England, plus Wales, Scotland, Northern Ireland.
--
-- This is a normalisation/lookup table — raw postcodes in stations are never modified.
-- Join via: LEFT JOIN postcode_regions pr ON pr.postcode_area = LEFT(s.postcode, ...)

CREATE TABLE IF NOT EXISTS postcode_regions (
    postcode_area       TEXT PRIMARY KEY,
    region              TEXT NOT NULL,
    region_group         TEXT NOT NULL  -- broader grouping: "North", "Midlands", "South", etc.
);

INSERT INTO postcode_regions (postcode_area, region, region_group) VALUES
    -- London
    ('E',   'London', 'London'),
    ('EC',  'London', 'London'),
    ('N',   'London', 'London'),
    ('NW',  'London', 'London'),
    ('SE',  'London', 'London'),
    ('SW',  'London', 'London'),
    ('W',   'London', 'London'),
    ('WC',  'London', 'London'),

    -- South East
    ('BN',  'South East', 'South'),
    ('BR',  'South East', 'South'),
    ('CM',  'South East', 'South'),
    ('CR',  'South East', 'South'),
    ('CT',  'South East', 'South'),
    ('DA',  'South East', 'South'),
    ('EN',  'South East', 'South'),
    ('GU',  'South East', 'South'),
    ('HA',  'South East', 'South'),
    ('HP',  'South East', 'South'),
    ('IG',  'South East', 'South'),
    ('KT',  'South East', 'South'),
    ('LU',  'South East', 'South'),
    ('ME',  'South East', 'South'),
    ('MK',  'South East', 'South'),
    ('OX',  'South East', 'South'),
    ('PO',  'South East', 'South'),
    ('RG',  'South East', 'South'),
    ('RH',  'South East', 'South'),
    ('RM',  'South East', 'South'),
    ('SG',  'South East', 'South'),
    ('SL',  'South East', 'South'),
    ('SM',  'South East', 'South'),
    ('SS',  'South East', 'South'),
    ('TN',  'South East', 'South'),
    ('TW',  'South East', 'South'),
    ('UB',  'South East', 'South'),
    ('WD',  'South East', 'South'),

    -- South West
    ('BA',  'South West', 'South'),
    ('BH',  'South West', 'South'),
    ('BS',  'South West', 'South'),
    ('DT',  'South West', 'South'),
    ('EX',  'South West', 'South'),
    ('GL',  'South West', 'South'),
    ('PL',  'South West', 'South'),
    ('SN',  'South West', 'South'),
    ('SP',  'South West', 'South'),
    ('TA',  'South West', 'South'),
    ('TQ',  'South West', 'South'),
    ('TR',  'South West', 'South'),

    -- East of England
    ('AL',  'East of England', 'South'),
    ('CB',  'East of England', 'South'),
    ('CO',  'East of England', 'South'),
    ('IP',  'East of England', 'South'),
    ('NR',  'East of England', 'South'),
    ('PE',  'East of England', 'South'),

    -- East Midlands
    ('DE',  'East Midlands', 'Midlands'),
    ('DN',  'East Midlands', 'Midlands'),
    ('LE',  'East Midlands', 'Midlands'),
    ('LN',  'East Midlands', 'Midlands'),
    ('NG',  'East Midlands', 'Midlands'),
    ('NN',  'East Midlands', 'Midlands'),

    -- West Midlands
    ('B',   'West Midlands', 'Midlands'),
    ('CV',  'West Midlands', 'Midlands'),
    ('DY',  'West Midlands', 'Midlands'),
    ('HR',  'West Midlands', 'Midlands'),
    ('ST',  'West Midlands', 'Midlands'),
    ('SY',  'West Midlands', 'Midlands'),
    ('TF',  'West Midlands', 'Midlands'),
    ('WR',  'West Midlands', 'Midlands'),
    ('WS',  'West Midlands', 'Midlands'),
    ('WV',  'West Midlands', 'Midlands'),

    -- North West
    ('BB',  'North West', 'North'),
    ('BL',  'North West', 'North'),
    ('CA',  'North West', 'North'),
    ('CH',  'North West', 'North'),
    ('CW',  'North West', 'North'),
    ('FY',  'North West', 'North'),
    ('L',   'North West', 'North'),
    ('LA',  'North West', 'North'),
    ('M',   'North West', 'North'),
    ('OL',  'North West', 'North'),
    ('PR',  'North West', 'North'),
    ('SK',  'North West', 'North'),
    ('WA',  'North West', 'North'),
    ('WN',  'North West', 'North'),

    -- Yorkshire and The Humber
    ('BD',  'Yorkshire and The Humber', 'North'),
    ('HD',  'Yorkshire and The Humber', 'North'),
    ('HG',  'Yorkshire and The Humber', 'North'),
    ('HU',  'Yorkshire and The Humber', 'North'),
    ('HX',  'Yorkshire and The Humber', 'North'),
    ('LS',  'Yorkshire and The Humber', 'North'),
    ('S',   'Yorkshire and The Humber', 'North'),
    ('WF',  'Yorkshire and The Humber', 'North'),
    ('YO',  'Yorkshire and The Humber', 'North'),

    -- North East
    ('DH',  'North East', 'North'),
    ('DL',  'North East', 'North'),
    ('NE',  'North East', 'North'),
    ('SR',  'North East', 'North'),
    ('TS',  'North East', 'North'),

    -- Wales
    ('CF',  'Wales', 'Wales'),
    ('LD',  'Wales', 'Wales'),
    ('LL',  'Wales', 'Wales'),
    ('NP',  'Wales', 'Wales'),
    ('SA',  'Wales', 'Wales'),

    -- Scotland
    ('AB',  'Scotland', 'Scotland'),
    ('DD',  'Scotland', 'Scotland'),
    ('DG',  'Scotland', 'Scotland'),
    ('EH',  'Scotland', 'Scotland'),
    ('FK',  'Scotland', 'Scotland'),
    ('G',   'Scotland', 'Scotland'),
    ('HS',  'Scotland', 'Scotland'),
    ('IV',  'Scotland', 'Scotland'),
    ('KA',  'Scotland', 'Scotland'),
    ('KW',  'Scotland', 'Scotland'),
    ('KY',  'Scotland', 'Scotland'),
    ('ML',  'Scotland', 'Scotland'),
    ('PA',  'Scotland', 'Scotland'),
    ('PH',  'Scotland', 'Scotland'),
    ('TD',  'Scotland', 'Scotland'),
    ('ZE',  'Scotland', 'Scotland'),

    -- Northern Ireland
    ('BT',  'Northern Ireland', 'Northern Ireland')
ON CONFLICT (postcode_area) DO NOTHING;
