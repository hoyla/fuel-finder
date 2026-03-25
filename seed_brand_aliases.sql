-- Seed brand_aliases with common mappings.
-- Run after the first full scrape to pre-populate.
-- Review and adjust as needed, then re-run:
--   REFRESH MATERIALIZED VIEW CONCURRENTLY current_prices;

INSERT INTO brand_aliases (raw_brand_name, canonical_brand) VALUES
    -- Supermarkets
    ('TESCO', 'Tesco'),
    ('ASDA', 'Asda'),
    ('SAINSBURYS', 'Sainsburys'),
    ('SAINSBURY''S', 'Sainsburys'),
    ('MORRISONS', 'Morrisons'),
    ('WAITROSE', 'Waitrose'),
    ('CO-OP', 'Co-op'),
    ('COSTCO', 'Costco'),

    -- Major fuel brands
    ('SHELL', 'Shell'),
    ('BP', 'BP'),
    ('ESSO', 'Esso'),
    ('TEXACO', 'Texaco'),
    ('JET', 'Jet'),
    ('GULF', 'Gulf'),
    ('TOTAL', 'TotalEnergies'),
    ('TOTALENERGIES', 'TotalEnergies'),
    ('MURCO', 'Murco'),

    -- Operators / groups
    ('MFG', 'Motor Fuel Group'),
    ('SGN', 'SGN'),
    ('RONTEC', 'Rontec'),
    ('APPLEGREEN', 'Applegreen'),
    ('CERTAS ENERGY', 'Certas Energy'),
    ('HARVEST ENERGY', 'Harvest Energy')
ON CONFLICT (raw_brand_name) DO NOTHING;
