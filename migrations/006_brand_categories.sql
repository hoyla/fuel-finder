-- Forecourt type categorisation via canonical brand.
-- Classifies stations into meaningful groups for price comparison.
-- Uses canonical brand names (after alias resolution).
-- Stations not matched here default to 'Independent' in the view.

CREATE TABLE IF NOT EXISTS brand_categories (
    canonical_brand     TEXT PRIMARY KEY,
    forecourt_type      TEXT NOT NULL  -- 'Supermarket', 'Major Oil', 'Motorway Operator', 'Independent'
);

INSERT INTO brand_categories (canonical_brand, forecourt_type) VALUES
    -- Supermarkets (the real ones — not the misleading API flag)
    ('Tesco',               'Supermarket'),
    ('Asda',                'Supermarket'),
    ('Sainsburys',          'Supermarket'),
    ('Morrisons',           'Supermarket'),
    ('Waitrose',            'Supermarket'),
    ('Co-op',               'Supermarket'),
    ('CENTRAL CO-OP',       'Supermarket'),
    ('OUR COOP',            'Supermarket'),
    ('COSTCO WHOLESALE',    'Supermarket'),
    ('ASDA EXPRESS',        'Supermarket'),

    -- Major oil companies
    ('Shell',               'Major Oil'),
    ('BP',                  'Major Oil'),
    ('Esso',                'Major Oil'),
    ('Texaco',              'Major Oil'),
    ('Jet',                 'Major Oil'),
    ('Gulf',                'Major Oil'),
    ('TotalEnergies',       'Major Oil'),
    ('Murco',               'Major Oil'),
    ('VALERO',              'Major Oil'),
    ('ESSAR',               'Major Oil'),

    -- Motorway operators
    ('WELCOME BREAK',       'Motorway Operator'),
    ('EG ON THE MOVE',      'Motorway Operator'),
    ('Applegreen',          'Motorway Operator'),

    -- Fuel groups / wholesalers
    ('Motor Fuel Group',    'Fuel Group'),
    ('Rontec',              'Fuel Group'),
    ('Harvest Energy',      'Fuel Group'),
    ('BP HARVEST ENERGY',   'Fuel Group'),
    ('TOTAL HARVEST ENERGY','Fuel Group'),
    ('SHELL HARVEST ENERGY','Fuel Group'),
    ('BREEZE HARVEST ENERGY','Fuel Group'),
    ('Certas Energy',       'Fuel Group'),
    ('SGN',                 'Fuel Group'),

    -- Convenience / forecourt groups
    ('Spar',                'Convenience'),
    ('CIRCLE K',            'Convenience'),
    ('Circle K',            'Convenience'),
    ('SOLO',                'Convenience'),
    ('Solo',                'Convenience'),
    ('SOLO PETROLEUM',      'Convenience'),
    ('Maxol',               'Convenience'),
    ('CENTRAL CONVENIENCE', 'Convenience'),
    ('Pace',                'Convenience'),
    ('PACE',                'Convenience')
ON CONFLICT (canonical_brand) DO NOTHING;
