# API Reference

The Fuel Finder web UI exposes a JSON API at `http://localhost:8080/api/`. All endpoints return JSON. No authentication is required by default (set `AUTH_ENABLED=true` and implement token validation for production).

## Price data endpoints

### `GET /api/summary`

Dashboard headline numbers: average/min/max prices per fuel type, station count, last scrape time.

**Response:**
```json
{
  "by_fuel_type": [
    { "fuel_type": "E10", "fuel_name": "Unleaded (E10)", "avg_price": 149.3, "min_price": 135.9, "max_price": 199.9, "station_count": 7277 }
  ],
  "total_stations": 7466,
  "total_prices": 24551,
  "last_scrape": "2026-03-25T14:00:00Z"
}
```

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

**Response:** Array of `{ brand_name, forecourt_type, avg_price, min_price, max_price, station_count }`, sorted by `avg_price` ascending.

### `GET /api/prices/by-category`

Average price by forecourt category (Supermarket, Major Oil, Motorway, etc.).

| Parameter | Type | Default | Description |
|---|---|---|---|
| `fuel_type` | string | `E10` | Fuel type code |

**Response:** Array of `{ forecourt_type, avg_price, min_price, max_price, station_count }`, sorted by `avg_price` ascending.

### `GET /api/prices/history`

Daily average price over time.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `fuel_type` | string | `E10` | Fuel type code |
| `days` | int | `30` | Number of days back (1–365) |
| `region` | string | — | Optional region filter |

**Response:** Array of `{ day, avg_price, stations }`

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
