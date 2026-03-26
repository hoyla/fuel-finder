# Database Schema Reference

## Tables

### `scrape_runs`

Metadata for each scraper execution. Used to track run history and determine the `since` timestamp for incremental scrapes.

| Column | Type | Notes |
|---|---|---|
| `id` | `BIGSERIAL PK` | |
| `started_at` | `TIMESTAMPTZ` | Auto-set on insert |
| `finished_at` | `TIMESTAMPTZ` | Set on completion/failure |
| `run_type` | `TEXT` | `'full'` or `'incremental'` |
| `batches_fetched` | `INTEGER` | Number of API batches retrieved |
| `stations_count` | `INTEGER` | Stations in API response |
| `price_records_count` | `INTEGER` | Price rows inserted (after dedup) |
| `s3_key` | `TEXT` | S3 key of raw JSON backup (if enabled) |
| `status` | `TEXT` | `'running'`, `'completed'`, `'failed'` |
| `error_message` | `TEXT` | Error details on failure |

### `stations`

Raw station data from the API. Upserted on each full scrape — always reflects the latest API response.

| Column | Type | Notes |
|---|---|---|
| `node_id` | `TEXT PK` | API-assigned station identifier (SHA-256 hash) |
| `trading_name` | `TEXT` | Name of the station |
| `brand_name` | `TEXT` | **Raw** brand from API (may be inconsistent) |
| `is_same_trading_and_brand_name` | `BOOLEAN` | |
| `public_phone_number` | `TEXT` | |
| `temporary_closure` | `BOOLEAN` | |
| `permanent_closure` | `BOOLEAN` | |
| `permanent_closure_date` | `DATE` | |
| `is_motorway_service_station` | `BOOLEAN` | |
| `is_supermarket_service_station` | `BOOLEAN` | |
| `address_line_1` | `TEXT` | |
| `address_line_2` | `TEXT` | |
| `city` | `TEXT` | |
| `county` | `TEXT` | |
| `country` | `TEXT` | ENGLAND, WALES, SCOTLAND, NORTHERN IRELAND |
| `postcode` | `TEXT` | |
| `latitude` | `DOUBLE PRECISION` | WGS84 |
| `longitude` | `DOUBLE PRECISION` | WGS84 |
| `amenities` | `TEXT[]` | e.g. `{car_wash, customer_toilets}` |
| `fuel_types` | `TEXT[]` | Fuel types sold: E5, E10, B7_STANDARD, etc. |
| `opening_times` | `JSONB` | Full weekly hours + bank holiday schedule |
| `first_seen` | `TIMESTAMPTZ` | When this station first appeared in our data |
| `last_updated` | `TIMESTAMPTZ` | Last time the station row was upserted |

### `fuel_prices`

**Append-only** table of price change events. A new row is only inserted when the price has changed from the last stored value for that (node_id, fuel_type) pair. This means every row represents a genuine price change.

| Column | Type | Notes |
|---|---|---|
| `id` | `BIGSERIAL PK` | |
| `node_id` | `TEXT FK → stations` | |
| `fuel_type` | `TEXT` | E5, E10, B7_STANDARD, B7_PREMIUM, B10, HVO |
| `price` | `NUMERIC(6,1)` | In pence per litre (e.g. 139.9) |
| `price_last_updated` | `TIMESTAMPTZ` | When the station reported this price to the API |
| `price_change_effective_timestamp` | `TIMESTAMPTZ` | When the station says the price took effect |
| `observed_at` | `TIMESTAMPTZ` | When **we** scraped this price |
| `scrape_run_id` | `BIGINT FK → scrape_runs` | Which scrape run captured this |
| `anomaly_flags` | `TEXT[]` | `NULL` = no issues. Populated by anomaly checks on insert (e.g. `price_below_floor`, `likely_decimal_error`, `large_price_jump`) |

**Key indexes:**
- `(node_id, fuel_type, observed_at DESC)` — fast per-station history lookups
- `(observed_at DESC)` — recent changes across all stations
- `(fuel_type, observed_at DESC)` — changes by fuel type

### `brand_aliases`

Maps raw API brand strings to canonical brand names. Used for bulk normalisation.

| Column | Type | Notes |
|---|---|---|
| `raw_brand_name` | `TEXT PK` | Exact string from `stations.brand_name` |
| `canonical_brand` | `TEXT` | Cleaned-up brand name |
| `created_at` | `TIMESTAMPTZ` | |

### `station_brand_overrides`

Per-station brand overrides for edge cases where the raw brand is wrong or the alias table isn't granular enough.

| Column | Type | Notes |
|---|---|---|
| `node_id` | `TEXT PK FK → stations` | |
| `canonical_brand` | `TEXT` | Correct brand for this station |
| `notes` | `TEXT` | Why this override exists |
| `created_at` | `TIMESTAMPTZ` | |

### `postcode_regions`

Maps UK postcode area prefixes (1-2 letters) to ONS-style regions. Used for regional aggregations and comparisons. Seeded from `seed_postcode_regions.sql`.

| Column | Type | Notes |
|---|---|---|
| `postcode_area` | `TEXT PK` | 1-2 letter prefix, e.g. `SW`, `M`, `BT` |
| `region` | `TEXT` | ONS-style region: London, North West, Scotland, etc. |
| `region_group` | `TEXT` | Broader grouping: North, Midlands, South, London, Wales, Scotland, Northern Ireland |

### `fuel_type_labels`

Maps API fuel type codes to human-friendly names and categories. Seeded from `seed_fuel_types.sql`.

| Column | Type | Notes |
|---|---|---|
| `fuel_type_code` | `TEXT PK` | API code, e.g. `B7_STANDARD`, `E10` |
| `fuel_name` | `TEXT` | Human name, e.g. "Diesel", "Unleaded (E10)" |
| `fuel_category` | `TEXT` | `'Petrol'` or `'Diesel'` |
| `description` | `TEXT` | Longer explanation of the fuel type |

### `brand_categories`

Maps canonical brand names to forecourt types for meaningful price comparisons. The API's `is_supermarket_service_station` flag is unreliable (flags BP, Texaco, etc. as supermarkets), so this table provides accurate classification based on actual brand identity.

| Column | Type | Notes |
|---|---|---|
| `canonical_brand` | `TEXT PK` | Must match a resolved canonical brand name |
| `forecourt_type` | `TEXT` | One of: `Supermarket`, `Major Oil`, `Motorway Operator`, `Fuel Group`, `Convenience`, `Independent` |

Brands not present in this table default to `Independent` in the `current_prices` view. Stations with `is_motorway_service_station = TRUE` are always classified as `Motorway` regardless of brand category.

### `schema_migrations`

Tracks which numbered SQL migrations have been applied. Managed automatically by `migrate.py`.

| Column | Type | Notes |
|---|---|---|
| `version` | `INTEGER PK` | Migration number from filename (e.g. `006`) |
| `filename` | `TEXT` | Full migration filename |
| `applied_at` | `TIMESTAMPTZ` | When migration was applied |

## Materialised View

### `current_prices`

The **current price snapshot** — one row per (station, fuel_type), always the most recently observed price regardless of age. Refreshed after each scrape run.

Joins station info, resolves canonical brand names via `station_override > brand_alias > raw_brand_name`, classifies forecourt type via `brand_categories`, and includes region via postcode area lookup.

| Column | Source | Notes |
|---|---|---|
| `node_id` | `fuel_prices` | |
| `fuel_type` | `fuel_prices` | |
| `fuel_name` | `fuel_type_labels` | Human name, e.g. "Diesel". Falls back to fuel_type code if unmapped |
| `fuel_category` | `fuel_type_labels` | `'Petrol'` or `'Diesel'`. Falls back to `'Unknown'` if unmapped |
| `price` | `fuel_prices` | Current price in pence |
| `price_last_updated` | `fuel_prices` | Station-reported timestamp |
| `price_change_effective_timestamp` | `fuel_prices` | |
| `observed_at` | `fuel_prices` | When we last saw this price |
| `trading_name` | `stations` | |
| `raw_brand_name` | `stations.brand_name` | Original API value |
| `brand_name` | Resolved | `COALESCE(override, alias, raw)` |
| `forecourt_type` | `brand_categories` | `Motorway` if motorway flag set, else from `brand_categories`, else `Independent` |
| `city` | `stations` | |
| `county` | `stations` | |
| `country` | `stations` | |
| `postcode` | `stations` | |
| `region` | `postcode_regions` | e.g. London, North West, Scotland |
| `region_group` | `postcode_regions` | e.g. North, South, Midlands, London |
| `latitude` | `stations` | |
| `longitude` | `stations` | |
| `is_motorway_service_station` | `stations` | |
| `is_supermarket_service_station` | `stations` | |
| `temporary_closure` | `stations` | |
| `anomaly_flags` | `fuel_prices` | `NULL` = no issues. Array of flag strings if anomalous |
| `price_is_outlier` | Computed | `true` if anomaly-flagged OR outside IQR fences (Q1 − 1.5×IQR .. Q3 + 1.5×IQR) |

**Indexes:** `(node_id, fuel_type)` unique, `(fuel_type, price)`, `(postcode)`, `(region, fuel_type)`, `(forecourt_type, fuel_type)`, `(admin_district, fuel_type)`, `(parliamentary_constituency, fuel_type)`, `(rural_urban, fuel_type)`, `(fuel_type, price_is_outlier)` partial WHERE NOT price_is_outlier

## Fuel types

| Code | Description |
|---|---|
| `E5` | Petrol — super unleaded (max 5% ethanol) |
| `E10` | Petrol — standard unleaded (max 10% ethanol) |
| `B7_STANDARD` | Diesel — standard (max 7% biodiesel) |
| `B7_PREMIUM` | Diesel — premium (max 7% biodiesel) |
| `B10` | Diesel (max 10% biodiesel) |
| `HVO` | Hydrotreated Vegetable Oil (renewable diesel) |

## Example queries

```sql
-- Average current price by fuel type (excluding outliers)
SELECT fuel_type, ROUND(AVG(price), 1) AS avg_ppl, COUNT(*) AS stations
FROM current_prices
WHERE NOT price_is_outlier
GROUP BY fuel_type
ORDER BY avg_ppl;

-- Cheapest E10 by brand (excluding outliers)
SELECT brand_name, ROUND(AVG(price), 1) AS avg, MIN(price), COUNT(*)
FROM current_prices
WHERE fuel_type = 'E10' AND NOT price_is_outlier
GROUP BY brand_name
ORDER BY avg;

-- Price history for a station
SELECT fuel_type, price, observed_at,
       price - LAG(price) OVER (PARTITION BY fuel_type ORDER BY observed_at) AS change
FROM fuel_prices
WHERE node_id = '<node_id>'
ORDER BY fuel_type, observed_at;

-- Stations with stale prices (>24h old)
SELECT trading_name, city, fuel_type, price, observed_at,
       NOW() - observed_at AS age
FROM current_prices
WHERE observed_at < NOW() - INTERVAL '24 hours'
ORDER BY observed_at;

-- Daily average E10 price (national)
SELECT DATE(observed_at) AS day, ROUND(AVG(price), 1) AS avg_e10
FROM fuel_prices
WHERE fuel_type = 'E10'
GROUP BY DATE(observed_at)
ORDER BY day;

-- Biggest price swings in last 7 days
SELECT fp.node_id, s.trading_name, s.city, fp.fuel_type,
       MAX(fp.price) - MIN(fp.price) AS swing
FROM fuel_prices fp
JOIN stations s ON s.node_id = fp.node_id
WHERE fp.observed_at > NOW() - INTERVAL '7 days'
GROUP BY fp.node_id, s.trading_name, s.city, fp.fuel_type
HAVING MAX(fp.price) - MIN(fp.price) > 0
ORDER BY swing DESC
LIMIT 20;

-- Find unmapped brands
SELECT s.brand_name, COUNT(*) AS station_count
FROM stations s
LEFT JOIN brand_aliases ba ON ba.raw_brand_name = s.brand_name
LEFT JOIN station_brand_overrides sbo ON sbo.node_id = s.node_id
WHERE ba.canonical_brand IS NULL AND sbo.canonical_brand IS NULL
  AND s.brand_name IS NOT NULL
GROUP BY s.brand_name
ORDER BY station_count DESC;
```
