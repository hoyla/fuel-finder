# API Reference

The Fuel Finder web UI exposes a JSON API at `http://localhost:8080/api/`. All endpoints return JSON. No authentication is required by default (set `AUTH_ENABLED=true` and implement token validation for production).

## Price data endpoints

### `GET /api/summary`

Dashboard headline numbers: average/min/max prices per fuel type, station count, last scrape time.

**Response:**
```json
{
  "by_fuel_type": [
    { "fuel_type": "E10", "fuel_name": "Unleaded (E10)", "avg_price": 149.3, "min_price": 135.9, "max_price": 199.9, "station_count": 7277, "outliers_excluded": 12 }
  ],
  "total_stations": 7466,
  "total_prices": 24551,
  "last_scrape": "2026-03-25T14:00:00Z"
}
```

Averages, min/max, and station counts exclude statistical outliers and anomaly-flagged prices. `outliers_excluded` shows how many prices were excluded for that fuel type.
```

### `GET /api/prices/by-region`

Average price by ONS region.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `fuel_type` | string | `E10` | Fuel type code |

**Response:** Array of `{ region, avg_price, min_price, max_price, station_count }`

Excludes outliers and anomaly-flagged prices.

### `GET /api/prices/by-brand`

Average price by canonical brand name (minimum 3 stations).

| Parameter | Type | Default | Description |
|---|---|---|---|
| `fuel_type` | string | `E10` | Fuel type code |
| `limit` | int | `20` | Max brands to return (1–100) |

**Response:** Array of `{ brand_name, forecourt_type, avg_price, min_price, max_price, station_count }`, sorted by `avg_price` ascending. Excludes outliers and anomaly-flagged prices.

### `GET /api/prices/by-category`

Average price by forecourt category (Supermarket, Major Oil, Motorway, etc.).

| Parameter | Type | Default | Description |
|---|---|---|---|
| `fuel_type` | string | `E10` | Fuel type code |

**Response:** Array of `{ forecourt_type, avg_price, min_price, max_price, station_count }`, sorted by `avg_price` ascending. Excludes outliers and anomaly-flagged prices.

### `GET /api/prices/history`

Average price over time. Uses **hourly** granularity for ranges of 30 days or fewer, **daily** for longer ranges. Excludes anomaly-flagged records and IQR-based statistical outliers.

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

**Response:** Array of `{ node_id, trading_name, brand_name, city, postcode, price, fuel_name, forecourt_type, latitude, longitude, is_motorway_service_station, is_supermarket_service_station }`

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
| `category` | string | — | Forecourt type filter (e.g. `Supermarket`, `Major Oil`) |
| `supermarket_only` | bool | `false` | Only stations with API supermarket flag |
| `motorway_only` | bool | `false` | Only motorway service stations |
| `sort` | string | `price` | Sort field: `price`, `brand`, `city`, `postcode` |
| `limit` | int | `50` | Results per page (1–500) |
| `offset` | int | `0` | Pagination offset |

**Response:**
```json
{
  "results": [ { "node_id", "trading_name", "brand_name", "city", "county", "postcode", "region", "price", "fuel_name", "fuel_category", "forecourt_type", "latitude", "longitude", "is_motorway_service_station", "is_supermarket_service_station", "observed_at" } ],
  "total": 7277,
  "limit": 50,
  "offset": 0
}
```

### `GET /api/anomalies`

Recent price records flagged by anomaly detection.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `limit` | int | `50` | Max records (1–500) |

**Response:** Array of `{ id, node_id, trading_name, city, fuel_type, price, anomaly_flags, observed_at }`

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
    { "node_id": "abc...", "trading_name": "Example", "city": "London", "postcode": "SW1A 1AA", "fuel_type": "E10", "fuel_name": "Unleaded (E10)", "price": 1.5, "brand_name": "Shell", "forecourt_type": "Major Oil", "anomaly_flags": null, "observed_at": "2026-03-25T14:00:00Z", "exclusion_reason": "iqr_outlier" }
  ],
  "total": 1
}
```

`exclusion_reason` is either `"anomaly_flagged"` (rule-based detection on insert) or `"iqr_outlier"` (Tukey IQR fence method).

---

> **Note on outlier exclusion:** All price aggregation endpoints (`/api/summary`, `/api/prices/by-region`, `/api/prices/by-brand`, `/api/prices/by-category`, `/api/prices/by-district`, `/api/prices/by-rural-urban`, `/api/prices/by-constituency`, `/api/prices/history`) exclude statistical outliers and anomaly-flagged prices. The `/api/outliers` endpoint provides full transparency into which prices were excluded and why.

## Reference endpoints

### `GET /api/fuel-types`

List all fuel types with human names.

**Response:** Array of `{ fuel_type_code, fuel_name, fuel_category }`

### `GET /api/regions`

List all available regions.

**Response:** Array of region name strings.

## Admin endpoints

These endpoints manage the normalisation lookup tables. Changes take effect in the materialised view after calling `/api/admin/refresh-view`.

### Brand aliases

Map raw API brand strings to canonical names.

| Endpoint | Description |
|---|---|
| `GET /api/admin/brand-aliases` | List all aliases |
| `POST /api/admin/brand-aliases` | Create/update alias |
| `DELETE /api/admin/brand-aliases/{raw_brand_name}` | Delete alias |

**POST body:**
```json
{ "raw_brand_name": "TESCO PFS", "canonical_brand": "Tesco" }
```

### Brand categories

Map canonical brands to forecourt types.

| Endpoint | Description |
|---|---|
| `GET /api/admin/brand-categories` | List all categories |
| `POST /api/admin/brand-categories` | Create/update category |
| `DELETE /api/admin/brand-categories/{canonical_brand}` | Delete category (brand defaults to Independent) |

**POST body:**
```json
{ "canonical_brand": "Tesco", "forecourt_type": "Supermarket" }
```

**Allowed forecourt types:** `Supermarket`, `Major Oil`, `Motorway Operator`, `Fuel Group`, `Convenience`, `Independent`

### Station overrides

Per-station brand overrides for edge cases.

| Endpoint | Description |
|---|---|
| `GET /api/admin/station-overrides` | List all overrides |
| `POST /api/admin/station-overrides` | Create/update override |
| `DELETE /api/admin/station-overrides/{node_id}` | Delete override |

**POST body:**
```json
{ "node_id": "abc123...", "canonical_brand": "Shell", "notes": "Branded Shell but API says independent" }
```

### Normalisation report

Shows how each brand resolves through the pipeline.

`GET /api/admin/normalisation-report`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `limit` | int | `100` | Max rows (1–1000) |
| `type` | string | — | Filter: `aliased`, `overridden`, `unmapped` |
| `brand` | string | — | Brand name substring filter |

**Response:** Array of `{ raw_brand, alias_resolved, override_resolved, final_brand, forecourt_type, resolution_method, station_count }`

- `resolution_method`: `raw` (no alias/override), `alias` (resolved via brand_aliases), `override` (resolved via station_brand_overrides)

### Refresh view

`POST /api/admin/refresh-view`

Rebuilds the `current_prices` materialised view. Call this after making changes to any lookup table so dashboard/search/map reflect the updates.

**Response:** `{ "status": "ok", "message": "current_prices view refreshed" }`
