-- Seed brand aliases for normalisation.

INSERT INTO brand_aliases (raw_brand_name, canonical_brand) VALUES
    ('TESCO', 'Tesco'),
    ('ASDA', 'Asda'),
    ('SAINSBURYS', 'Sainsburys'),
    ('SAINSBURY''S', 'Sainsburys'),
    ('MORRISONS', 'Morrisons'),
    ('WAITROSE', 'Waitrose'),
    ('CO-OP', 'Co-op'),
    ('COSTCO', 'Costco'),
    ('SHELL', 'Shell'),
    ('BP', 'BP'),
    ('ESSO', 'Esso'),
    ('TEXACO', 'Texaco'),
    ('JET', 'Jet'),
    ('GULF', 'Gulf'),
    ('TOTAL', 'TotalEnergies'),
    ('TOTALENERGIES', 'TotalEnergies'),
    ('MURCO', 'Murco'),
    ('MFG', 'Motor Fuel Group'),
    ('SGN', 'SGN'),
    ('RONTEC', 'Rontec'),
    ('APPLEGREEN', 'Applegreen'),
    ('CERTAS ENERGY', 'Certas Energy'),
    ('HARVEST ENERGY', 'Harvest Energy')
ON CONFLICT (raw_brand_name) DO NOTHING;
