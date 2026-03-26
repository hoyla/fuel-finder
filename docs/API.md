# API Reference

The Fuel Finder web UI exposes a JSON API at `/api/`. All endpoints return JSON.

**Authentication** (checked in order):
1. **API key** — `X-Api-Key` header. API key holders get admin access.
2. **Cognito JWT** — `Authorization: Bearer <id_token>` header. Admin requires `admin` group membership.
3. **No auth** — when `AUTH_ENABLED=false` (local dev default).

Admin endpoints require either a valid API key or Cognito `admin` group membership.

---

## Price data endpoints

### `GET /api/summary`

Dashboard headline numbers: average/min/max prices per fuel type, station count, country breakdown, last scrape time.

**Response:**
```json
{
  "by_fuel_type": [
    { "fuel_type": "E10", "fuel_name": "Unleaded (E10)", "avg_price": 149.3, "min_price": 135.9, "max_price": 199.9, "station_count": 7277, "outliers_excluded": 12 }
  ],
  "by_country": [
    { "country_name": "England", "station_count": 5854 },
    { "country_name": "Scotland", "station_count": 742 },
    { "country_name": "Wales", "station_count": 452 },
    { "country_name": "Northern Ireland", "station_count": 345 },
    { "country_name": "Other/Unknown", "station_count": 32 }
  ],
  "total_stations": 7466,
  "total_prices": 24551,
  "last_scrape": "2026-03-25T14:00:00Z"
}
```

Averages, min/max, and station counts exclude outliers and anomaly-flagged prices. `outliers_excluded` shows how many prices were excluded per fuel type.

### `GET /api/prices/by-region`

Average price by ONS region.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `fuel_type` | string | `E10` | Fuel type code |

**Response:** Array of `{ region, avg_price, min_price, max_price, station_count }`

### `GET /api/prices/by-brand`

Average price by canonical brand name (minimum 3 stations).

| Parameter | Type | Default | Description |
|---|---|---|---|
| `fuel_type` | string | `E10` | Fuel type code |
| `limit` | int | `20` | Max brands to return (1–100) |

**Response:** Array of `{ brand_name, forecourt_type, avg_price, min_price, max_price, station_count }`

### `GET /api/prices/by-category`

Average price by forecourt category (Supermarket, Major Oil, Motorway Operator, etc.).

| Parameter | Type | Default | Description |
|---|---|---|---|
| `fuel_type` | string | `E10` | Fuel type code |

**Response:** Array of `{ forecourt_type, avg_price, min_price, max_price, station_count }`

### `GET /api/prices/by-district`

Average price by local authority district (minimum 3 stations).

| Parameter | Type | Default | Description |
|---|---|---|---|
| `fuel_type` | string | `E10` | Fuel type code |
| `limit` | int | `30` | Max districts to return (1–500) |

**Response:** Array of `{ admin_district, avg_price, min_price, max_price, station_count }`

### `GET /api/prices/by-constituency`

Average price by parliamentary constituency (minimum 2 stations).

| Parameter | Type | Default | Description |
|---|---|---|---|
| `fuel_type` | string | `E10` | Fuel type code |
| `limit` | int | `30` | Max constituencies to return (1–650) |

**Response:** Array of `{ parliamentary_constituency, avg_price, min_price, max_price, station_count }`

### `GET /api/prices/by-rural-urban`

Average price by rural/urban classification. England/Wales ONS RUC and Scottish Government classifications are unified into common labels.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `fuel_type` | string | `E10` | Fuel type code |

**Response:** Array of `{ unified_label, avg_price, min_price, max_price, station_count, rural_urban_values }`

`rural_urban_values` is an array of the raw classification strings mapped to each unified label.

### `GET /api/prices/history`

Average price over time. Uses **hourly** granularity for ranges of 30 days or fewer, **daily** for longer ranges.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `fuel_type` | string | `E10` | Fuel type code |
| `days` | int | `30` | Number of days back (1–365) |
| `region` | string | — | Optional region filter |

**Response:**
```json
{
  "granularity": "hourly",
  "data": [
    { "bucket": "2026-03-25T13:00:00+00:00", "avg_price": 149.1, "stations": 6998 }
  ]
}
```

- `granularity`: `"hourly"` (≤30 days) or `"daily"` (>30 days)
- `bucket`: ISO timestamp (hourly) or date string (daily)

### `GET /api/prices/map`

Current prices with coordinates for map display.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `fuel_type` | string | `E10` | Fuel type code |
| `region` | string | — | Region filter |
| `brand` | string | — | Brand name substring filter |
| `category` | string | — | Forecourt type filter |
| `exclude_outliers` | bool | `false` | Exclude statistical outliers |

**Response:** Array of `{ node_id, trading_name, brand_name, city, postcode, price, fuel_name, forecourt_type, admin_district, rural_urban, parliamentary_constituency, latitude, longitude, is_motorway_service_station, is_supermarket_service_station }`

### `GET /api/prices/search`

Flexible search/filter endpoint with pagination.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `fuel_type` | string | `E10` | Fuel type code |
| `postcode` | string | — | Postcode prefix filter (e.g. `SW1`, `M`) |
| `brand` | string | — | Brand name substring filter |
| `city` | string | — | City substring filter |
| `min_price` | float | — | Minimum price in pence |
| `max_price` | float | — | Maximum price in pence |
| `category` | string | — | Forecourt type filter |
| `district` | string | — | Local authority district filter |
| `constituency` | string | — | Parliamentary constituency filter |
| `rural_urban` | string | — | Rural/urban classification filter |
| `region` | string | — | Region filter |
| `country` | string | — | Country filter (England, Scotland, Wales, Northern Ireland, Other/Unknown) |
| `supermarket_only` | bool | `false` | Only stations with API supermarket flag |
| `motorway_only` | bool | `false` | Only motorway service stations |
| `exclude_outliers` | bool | `false` | Exclude statistical outliers |
| `sort` | string | `price` | Sort field: `price`, `brand`, `city`, `postcode`, `district` |
| `limit` | int | `50` | Results per page (1–500) |
| `offset` | int | `0` | Pagination offset |

**Response:**
```json
{
  "results": [
    {
      "node_id": "...", "trading_name": "...", "brand_name": "...",
      "city": "...", "county": "...", "postcode": "...", "region": "...",
      "price": 149.9, "fuel_name": "Unleaded (E10)", "fuel_category": "Petrol",
      "forecourt_type": "Supermarket",
      "admin_district": "...", "parliamentary_constituency": "...", "rural_urban": "...",
      "latitude": 51.5, "longitude": -0.1,
      "is_motorway_service_station": false, "is_supermarket_service_station": true,
      "observed_at": "2026-03-25T14:00:00Z"
    }
  ],
  "total": 7277,
  "limit": 50,
  "offset": 0
}
```

### `GET /api/anomalies`

Recent price records flagged by anomaly detection, with previous price context.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `limit` | int | `50` | Max records (1–500) |

**Response:** Array of `{ id, node_id, trading_name, city, fuel_type, price, prev_price, anomaly_flags, observed_at, prev_observed_at }`

**Anomaly flags:** `price_below_floor`, `price_above_ceiling`, `likely_decimal_error`, `large_price_jump`

### `GET /api/outliers`

Prices excluded as statistical outliers, with IQR bounds for transparency.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `fuel_type` | string | — | Optional fuel type filter |
| `limit` | int | `100` | Max records (1–500) |

**Response:**
```json
{
  "bounds": {
    "E10": { "fuel_type": "E10", "q1": 146.9, "q3": 153.9, "iqr": 7.0, "lower_fence": 136.4, "upper_fence": 164.4, "total_stations": 7277 }
  },
  "outliers": [
    { "node_id": "...", "trading_name": "...", "city": "...", "postcode": "...", "fuel_type": "E10", "fuel_name": "Unleaded (E10)", "price": 1.5, "brand_name": "Shell", "forecourt_type": "Major Oil", "anomaly_flags": null, "observed_at": "2026-03-25T14:00:00Z", "exclusion_reason": "iqr_outlier" }
  ],
  "total": 1
}
```

`exclusion_reason`: `"anomaly_flagged"` (rule-based detection on insert) or `"iqr_outlier"` (Tukey IQR fence method).

---

> **Note on outlier exclusion:** All price aggregation endpoints (summary, by-region, by-brand, by-category, by-district, by-rural-urban, by-constituency, history) exclude statistical outliers and anomaly-flagged prices. The `/api/outliers` endpoint provides full transparency into which prices were excluded and why.

---

## Reference endpoints

### `GET /api/fuel-types`

List all fuel types with human names.

**Response:** Array of `{ fuel_type_code, fuel_name, fuel_category }`

### `GET /api/regions`

List all available regions.

**Response:** Array of region name strings.

### `GET /api/districts`

List all local authority districts.

**Response:** Array of district name strings.

### `GET /api/constituencies`

List all parliamentary constituencies.

**Response:** Array of constituency name strings.

---

## Admin endpoints

Admin endpoints require a valid API key or Cognito `admin` group membership. Changes to lookup tables take effect after calling `POST /api/admin/refresh-view`.

### Scrape history

`GET /api/admin/scrape-runs`

Recent scrape runs with timing and record counts.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `limit` | int | `50` | Max runs (1–500) |

**Response:** Array of `{ id, started_at, finished_at, run_type, status, batches_fetched, stations_count, price_records_count, s3_key, error_message, duration_secs }`

`status`: `running`, `completed`, or `failed`. `run_type`: `full` or `incremental`.

### Brand aliases

Map raw API brand strings to canonical names.

| Endpoint | Description |
|---|---|
| `GET /api/admin/brand-aliases` | List all aliases |
| `POST /api/admin/brand-aliases` | Create/update alias |
| `DELETE /api/admin/brand-aliases/{raw_brand_name}` | Delete alias |

**POST body:** `{ "raw_brand_name": "TESCO PFS", "canonical_brand": "Tesco" }`

### Brand categories

Map canonical brands to forecourt types.

| Endpoint | Description |
|---|---|
| `GET /api/admin/brand-categories` | List all categories |
| `POST /api/admin/brand-categories` | Create/update category |
| `DELETE /api/admin/brand-categories/{canonical_brand}` | Delete category |

**POST body:** `{ "canonical_brand": "Tesco", "forecourt_type": "Supermarket" }`

**Allowed forecourt types:** `Supermarket`, `Major Oil`, `Motorway Operator`, `Fuel Group`, `Convenience`, `Independent`

### Station overrides

Per-station brand overrides for edge cases.

| Endpoint | Description |
|---|---|
| `GET /api/admin/station-overrides` | List all overrides |
| `POST /api/admin/station-overrides` | Create/update override |
| `DELETE /api/admin/station-overrides/{node_id}` | Delete override |

**POST body:** `{ "node_id": "abc123...", "canonical_brand": "Shell", "notes": "..." }`

### Normalisation report

`GET /api/admin/normalisation-report`

Shows how each brand resolves through the normalisation pipeline.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `limit` | int | `100` | Max rows (1–1000) |
| `type` | string | — | Filter: `aliased`, `overridden`, `unmapped` |
| `brand` | string | — | Brand name substring filter |

**Response:** Array of `{ raw_brand, alias_resolved, override_resolved, final_brand, forecourt_type, resolution_method, station_count }`

`resolution_method`: `raw` (no transformation), `alias` (via brand_aliases), `override` (via station_brand_overrides)

### Postcode issues

`GET /api/admin/postcode-issues`

Stations whose postcodes were not recognised by postcodes.io — may indicate bad source data.

**Response:** Array of `{ node_id, trading_name, brand_name, postcode, api_latitude, api_longitude, city, county, coords_outside_uk, fixed_latitude, fixed_longitude }`

### Postcode coordinate fix

`PATCH /api/admin/postcode-lookups/{postcode}`

Manually set coordinates for a postcode that postcodes.io didn't recognise.

**Body:** `{ "latitude": 51.5, "longitude": -0.1 }`

**Response:** `{ "postcode": "SW1A 1AA", "latitude": 51.5, "longitude": -0.1 }`

### User management (Cognito)

| Endpoint | Method | Description |
|---|---|---|
| `/api/admin/users` | GET | List all Cognito users with group memberships |
| `/api/admin/users` | POST | Create/invite a new user (sends invitation email) |
| `/api/admin/users/{username}/groups/{group}` | POST | Add user to group |
| `/api/admin/users/{username}/groups/{group}` | DELETE | Remove user from group |
| `/api/admin/users/{username}/disable` | POST | Disable user account |
| `/api/admin/users/{username}/enable` | POST | Re-enable user account |

**POST /api/admin/users body:** `{ "email": "user@example.com", "admin": false }`

**GET /api/admin/users response:** Array of `{ username, email, status, enabled, groups, created }`

### Refresh view

`POST /api/admin/refresh-view`

Rebuilds the `current_prices` materialised view. Call after changing any lookup table.

**Response:** `{ "status": "ok", "message": "current_prices view refreshed" }`
