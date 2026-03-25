-- Seed fuel type labels.

INSERT INTO fuel_type_labels (fuel_type_code, fuel_name, fuel_category, description) VALUES
    ('E10',         'Unleaded',             'Petrol',   'Standard unleaded petrol (max 10% ethanol). The most common grade since 2021.'),
    ('E5',          'Super Unleaded',       'Petrol',   'Premium unleaded petrol (max 5% ethanol). Higher octane, typically 97-99 RON.'),
    ('B7_STANDARD', 'Diesel',               'Diesel',   'Standard diesel (max 7% biodiesel). The most common diesel grade.'),
    ('B7_PREMIUM',  'Premium Diesel',       'Diesel',   'Premium diesel (max 7% biodiesel). Marketed as higher performance.'),
    ('B10',         'B10 Diesel',           'Diesel',   'Diesel with up to 10% biodiesel content.'),
    ('HVO',         'Renewable Diesel',     'Diesel',   'Hydrotreated Vegetable Oil. A drop-in renewable diesel alternative.')
ON CONFLICT (fuel_type_code) DO NOTHING;
