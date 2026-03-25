-- Make fuel names unambiguous by including the fuel code.

UPDATE fuel_type_labels SET fuel_name = 'Unleaded (E10)'       WHERE fuel_type_code = 'E10';
UPDATE fuel_type_labels SET fuel_name = 'Super Unleaded (E5)'  WHERE fuel_type_code = 'E5';
UPDATE fuel_type_labels SET fuel_name = 'Diesel (B7)'          WHERE fuel_type_code = 'B7_STANDARD';
UPDATE fuel_type_labels SET fuel_name = 'Premium Diesel (B7)'  WHERE fuel_type_code = 'B7_PREMIUM';
UPDATE fuel_type_labels SET fuel_name = 'Biodiesel (B10)'      WHERE fuel_type_code = 'B10';
UPDATE fuel_type_labels SET fuel_name = 'HVO Renewable Diesel' WHERE fuel_type_code = 'HVO';
