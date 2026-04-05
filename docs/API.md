# API Reference

The Fuel Finder web UI exposes a JSON API at `/api/`. All endpoints return JSON.

**Authentication** (checked in order):
1. **API key** — `X-Api-Key` header. API key holders get admin access.
2. **Cognito JWT** — `Authorization: Bearer <id_token>` header. Role determined by Cognito group membership.
3. **No auth** — when neither is configured (local dev default). All users get admin access.

**User roles:**

| Role | Cognito group | Access |
|---|---|---|
| Admin | `admin` | Everything — user management, data mutations, exports |
| Editor | `editor` | Data mutations (aliases, categories, overrides, corrections), exports, view refresh |
| Read-only | (no group) | Read-only access with query caps: search limited to 200 results, history limited to 90 days |

Admin endpoints (user management) require `admin` group membership or a valid API key.
Editor endpoints (mutations, exports, view refresh) require `admin` or `editor` group membership.
Read-only endpoints are accessible to all authenticated users.

**Admin tier preview:** Admins can send `X-Role-Override: editor` or `X-Role-Override: readonly` to preview a lower tier's experience (including backend query caps). The header is ignored for non-admin users.

---

## Health check

### `GET /health`

Basic health check. Returns `{ "status": "ok" }`. No authentication required.

---

## Auth endpoints

### `GET /auth/config`

Returns auth configuration for frontend discovery.

**Response:** `{ "mode": "cognito", "region": "eu-north-1", "clientId": "..." }` or `{ "mode": "none" }`

### `GET /auth/me`

Returns the current user's role. Requires authentication.

**Response:**
```json
{
  "role": "editor",
  "real_role": "admin",
  "email": "user@example.com"
}
```

`role` reflects any `X-Role-Override` in effect. `real_role` is always the user's actual role.

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

Averages, min/max, and station counts exclude IQR outliers and anomaly-flagged prices (Tukey IQR applied to current snapshot). `outliers_excluded` shows how many prices were excluded per fuel type.

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

### `GET /api/prices/station/{node_id}/history`

Price history for a single station.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `fuel_type` | string | `E10` | Fuel type code |
| `days` | int | `30` | Days back (1–365; readonly capped at 90) |
| `start_date` | string | — | Start date (YYYY-MM-DD) |
| `end_date` | string | — | End date (YYYY-MM-DD) |
| `granularity` | string | `hourly` | `hourly` or `daily`. Defaults to hourly |

**Response:**
```json
{
  "granularity": "hourly",
  "station": { "trading_name": "Tesco Extra", "brand_name": "Tesco", "city": "Leeds", "postcode": "LS11 5TZ" },
  "data": [
    { "bucket": "2026-03-25T13:00:00+00:00", "avg_price": 139.9 }
  ]
}
```

### `GET /api/prices/history`

Average price over time. Uses **hourly** granularity for ranges under 30 days, **daily** for 30 days or longer. Daily queries use the pre-aggregated `daily_prices` table for faster response times.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `fuel_type` | string | `E10` | Fuel type code |
| `days` | int | `30` | Days back (1–365; readonly capped at 90) |
| `start_date` | string | — | Start date (YYYY-MM-DD) |
| `end_date` | string | — | End date (YYYY-MM-DD) |
| `granularity` | string | auto | `hourly` or `daily`. Auto: hourly for <30 days, daily for ≥30 days |
| `region` | string | — | Region filter (comma-separated for multiple) |
| `country` | string | — | Country filter (comma-separated) |
| `rural_urban` | string | — | Rural/urban classification filter (comma-separated) |
| `node_ids` | string | — | Comma-separated station node IDs |
| `brand` | string | — | Brand name substring filter |
| `category` | string | — | Forecourt type filter (comma-separated) |
| `postcode` | string | — | Postcode prefix filter |
| `city` | string | — | City substring filter |
| `district` | string | — | Local authority district filter |
| `constituency` | string | — | Parliamentary constituency filter |
| `supermarket_only` | bool | `false` | Only supermarket stations |
| `motorway_only` | bool | `false` | Only motorway stations |
| `exclude_outliers` | bool | `false` | Exclude statistical outliers from station selection |

When search-style filters (brand, category, postcode, etc.) are provided, the endpoint uses a subquery against `current_prices` to select matching stations — much faster than passing thousands of node IDs.

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

Anomaly-flagged prices are excluded. A Hampel filter (rolling median ± 3×MAD) smooths remaining outlier bucket averages without distorting legitimate trends. Window size: ±3 days for daily, ±24 hours for hourly.

### `GET /api/prices/history/export`

Export raw individual price records matching the trend filters as a streaming download.
Accepts the same filter parameters as `/api/prices/history`.
**Requires editor or admin role.**

| Parameter | Type | Default | Description |
|---|---|---|---|
| (same filters as `/api/prices/history`) | | | See above |
| `format` | string | `csv` | `csv` or `json` |

Returns every `fuel_prices` row (not averages) for matching stations, with full station and postcode enrichment.

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
| `fuel_type` | string | — | Fuel type code. Omit to search across all fuel types |
| `postcode` | string | — | Postcode prefix filter (e.g. `SW1`, `M`) |
| `station` | string | — | Trading name substring filter |
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
| `limit` | int | `50` | Results per page (minimum 1; readonly capped at 200) |
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

### `GET /api/prices/search/export`

Export all historical price records matching the search filters as a streaming download.
Accepts the same filter parameters as `/api/prices/search` (except `sort`, `limit`, `offset`).
**Requires editor or admin role.**

| Parameter | Type | Default | Description |
|---|---|---|---|
| (same filters as `/api/prices/search`) | | | See above |
| `format` | string | `csv` | `csv` or `json` |

Returns every `fuel_prices` row (not just current) for matching stations, with full station and postcode enrichment.
Streamed to handle large result sets.

**Response:** CSV or JSON file download with columns: `node_id`, `trading_name`, `raw_brand`, `brand`, `fuel_type`, `fuel_name`, `price`, `observed_at`, `anomaly_flags`, `postcode`, `city`, `county`, `country`, `region`, `admin_district`, `parliamentary_constituency`, `rural_urban`, `forecourt_type`, `latitude`, `longitude`, `is_motorway_service_station`, `is_supermarket_service_station`.

### `POST /api/stations/lookup`

Batch lookup for station/location data by node ID, including postcode enrichment fields.
Useful when you already have a large list of node IDs and only need location metadata.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `node_ids` | array[string] | — | Required JSON body field. Ordered list of station node IDs |

**Authentication:** requires any authenticated role (`readonly`, `editor`, `admin`).

**Caps:**
- `readonly`: max 200 IDs per request
- `editor`/`admin`: max 5000 IDs per request

**Request body:**
```json
{
  "node_ids": ["node-a", "node-b", "node-c"]
}
```

**Response:**
```json
{
  "results": [
    {
      "node_id": "node-a",
      "found": true,
      "trading_name": "Example Service Station",
      "raw_brand": "TESCO PFS",
      "brand": "Tesco",
      "forecourt_type": "Supermarket",
      "postcode": "LS11 5TZ",
      "original_postcode": "LS11 5TZ",
      "city": "Leeds",
      "county": "West Yorkshire",
      "country": "England",
      "region": "Yorkshire and The Humber",
      "admin_district": "Leeds",
      "parliamentary_constituency": "Leeds South",
      "rural_urban": "Urban city and town",
      "latitude": 53.775,
      "longitude": -1.545,
      "is_motorway_service_station": false,
      "is_supermarket_service_station": true
    },
    {
      "node_id": "unknown-id",
      "found": false,
      "trading_name": null,
      "raw_brand": null,
      "brand": null,
      "forecourt_type": null,
      "postcode": null,
      "original_postcode": null,
      "city": null,
      "county": null,
      "country": null,
      "region": null,
      "admin_district": null,
      "parliamentary_constituency": null,
      "rural_urban": null,
      "latitude": null,
      "longitude": null,
      "is_motorway_service_station": null,
      "is_supermarket_service_station": null
    }
  ],
  "requested": 2,
  "found": 1,
  "missing": ["unknown-id"]
}
```

Response order matches the `node_ids` request order.

### `GET /api/anomalies`

Recent price records flagged by anomaly detection, with previous price context. Records that have already been corrected are excluded.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `limit` | int | `50` | Max records (1–500) |

**Response:** Array of `{ id, node_id, trading_name, city, brand_name, postcode, fuel_type, price, prev_price, anomaly_flags, observed_at, prev_observed_at }`

`prev_price` reflects any correction applied to the previous record (i.e. uses the corrected price if one exists).

**Anomaly flags:** `price_below_floor`, `price_above_ceiling`, `likely_decimal_error`, `large_price_jump`

### `GET /api/outliers`

Prices excluded as statistical outliers from the current snapshot, with IQR bounds for transparency.

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
    { "node_id": "...", "trading_name": "...", "city": "...", "postcode": "...", "fuel_type": "E10", "fuel_name": "Unleaded (E10)", "price": 1.5, "brand_name": "Shell", "forecourt_type": "Major Oil", "anomaly_flags": null, "observed_at": "2026-03-25T14:00:00Z", "exclusion_reason": "iqr_outlier", "original_price": 1.5, "corrected_price": null }
  ],
  "total": 1
}
```

`exclusion_reason`: `"anomaly_flagged"` (rule-based detection on insert) or `"iqr_outlier"` (Tukey IQR fence applied to the current price snapshot).

`original_price` and `corrected_price` are included when a correction exists, allowing the UI to show what was changed.

### `GET /api/admin/price-distribution`

Price histogram (80 bins) split by outlier/clean status, with IQR fence values. Used by the Statistical Outliers page to render the IQR distribution chart. Anomaly-flagged prices are excluded entirely; remaining prices are split into clean (within IQR fences) and outlier (outside fences).

**Auth:** `require_auth`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `fuel_type` | string | — | **Required.** Fuel type to analyse |

**Response:**
```json
{
  "q1": 146.9,
  "q3": 153.9,
  "iqr": 7.0,
  "lower_fence": 136.4,
  "upper_fence": 164.4,
  "bins": [
    { "bin_min": 129.4, "bin_max": 130.1, "clean": 0, "outlier": 2 },
    { "bin_min": 130.1, "bin_max": 130.8, "clean": 0, "outlier": 1 }
  ]
}
```

Bin boundaries span from `lower_fence − IQR` to `upper_fence + IQR`, focusing on the region of interest. Prices outside this window are counted in the edge bins.

### `GET /api/prices/station/{node_id}/records`

Raw individual price records for a station, with any corrections and computed effective flags. Used by the price editor.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `fuel_type` | string | — | Optional fuel type filter |
| `limit` | int | `500` | Max records (1–5000) |

**Response:**
```json
{
  "station": { "trading_name": "...", "brand_name": "...", "city": "...", "postcode": "..." },
  "records": [
    {
      "fuel_price_id": 12345,
      "fuel_type": "E10",
      "fuel_name": "Unleaded (E10)",
      "original_price": 1.5,
      "corrected_price": 150.0,
      "effective_price": 150.0,
      "anomaly_flags": ["likely_decimal_error:..."],
      "effective_flags": null,
      "observed_at": "2026-03-25T14:00:00Z",
      "correction_reason": "Decimal error",
      "corrected_by": "user@example.com",
      "corrected_at": "2026-03-25T15:00:00Z",
      "prev_effective_price": 149.9
    }
  ]
}
```

`effective_price`: `COALESCE(corrected_price, original_price)`.
`effective_flags`: re-evaluated anomaly flags based on the effective price. For uncorrected records this equals `anomaly_flags`; for corrected records the flags are recalculated to reflect whether the correction resolved the anomaly.

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

Endpoints below require elevated access. **Editor** endpoints (mutations, exports, view refresh) require `admin` or `editor` group membership. **Admin** endpoints (user management) require `admin` group only.

Changes to lookup tables take effect after calling `POST /api/admin/refresh-view`.

### Scrape history

`GET /api/admin/scrape-runs`

Recent scrape runs with timing and record counts.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `limit` | int | `50` | Max runs (1–500) |
| `offset` | int | `0` | Pagination offset |

**Response:** `{ rows: [...], total, limit, offset }`

Each row: `{ id, started_at, finished_at, run_type, status, batches_fetched, stations_count, price_records_count, s3_key, error_message, duration_secs }`

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

**Allowed forecourt types:** `Supermarket`, `Major Oil`, `Motorway Operator`, `Fuel Group`, `Convenience`, `Independent`, `Uncategorised`

### Station overrides

Per-station brand overrides for edge cases.

| Endpoint | Description |
|---|---|
| `GET /api/admin/station-overrides` | List all overrides |
| `POST /api/admin/station-overrides` | Create/update override |
| `POST /api/admin/station-overrides/batch` | Batch create/update overrides (max 500) |
| `DELETE /api/admin/station-overrides/{node_id}` | Delete override |

**POST body:** `{ "node_id": "abc123...", "canonical_brand": "Shell", "notes": "..." }`

**Batch POST body:** `{ "canonical_brand": "Shell", "node_ids": ["abc123", "def456"], "notes": "..." }`

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

Stations whose postcodes were not recognised by postcodes.io — may indicate bad source data. Includes override status when a corrected postcode has been set.

**Response:** Array of `{ node_id, trading_name, brand_name, postcode, api_latitude, api_longitude, city, county, coords_outside_uk, fixed_latitude, fixed_longitude, corrected_postcode, override_notes }`

### Postcode coordinate fix

`PATCH /api/admin/postcode-lookups/{postcode}`

Manually set coordinates for a postcode that postcodes.io didn't recognise.

**Body:** `{ "latitude": 51.5, "longitude": -0.1 }`

**Response:** `{ "postcode": "SW1A 1AA", "latitude": 51.5, "longitude": -0.1 }`

### Retry failed postcode lookups

`POST /api/admin/postcode-lookups/retry-failed`

Re-checks all postcodes that previously failed lookup (where `pc_latitude IS NULL`) against postcodes.io in batches of 100. Updates `looked_up_at` on all retried postcodes. Requires editor or admin role.

**Body:** `{}` (empty)

**Response:** `{ "retried": 12, "resolved": 3, "still_failed": 9 }`

### Postcode issues stats

`GET /api/admin/postcode-issues/stats`

Returns the count of currently-failed postcode lookups and the timestamp of the most recent lookup attempt among them.

**Response:** `{ "failed_count": 12, "last_checked_at": "2026-04-05T14:30:00Z" }`

### Postcode overrides

Per-station postcode corrections for stations with mistyped or expired postcodes. The corrected postcode is used for geographic enrichment (region, constituency, district, etc.) while the original is preserved. On save, the corrected postcode is looked up via postcodes.io for full enrichment.

| Endpoint | Description |
|---|---|
| `GET /api/admin/postcode-overrides` | List all overrides |
| `POST /api/admin/postcode-overrides` | Create/update override |
| `DELETE /api/admin/postcode-overrides/{node_id}` | Delete override |

**GET response:** Array of `{ node_id, trading_name, brand_name, original_postcode, corrected_postcode, notes, created_at, lookup_succeeded }`

**POST body:** `{ "node_id": "abc123...", "corrected_postcode": "SW1A 1AA", "notes": "Typo in source data" }`

**POST response:** `{ "node_id": "...", "original_postcode": "...", "corrected_postcode": "SW1A 1AA", "notes": "...", "lookup_status": "enriched" }`

`lookup_status`: `"enriched"` (postcode recognised, full enrichment stored), `"not_recognised"` (postcodes.io didn't recognise it), or `"lookup_failed"` (network error, override still saved).

`GET /api/admin/postcode-overrides` requires any authenticated role. `POST` and `DELETE` require editor or admin.

### User management (Cognito)

**Admin only** — these endpoints require `admin` group membership.

| Endpoint | Method | Description |
|---|---|---|
| `/api/admin/users` | GET | List all Cognito users with group memberships |
| `/api/admin/users` | POST | Create/invite a new user (sends invitation email) |
| `/api/admin/users/{username}/groups/{group}` | POST | Add user to group |
| `/api/admin/users/{username}/groups/{group}` | DELETE | Remove user from group |
| `/api/admin/users/{username}/disable` | POST | Disable user account |
| `/api/admin/users/{username}/enable` | POST | Re-enable user account |
| `/api/admin/users/{username}` | DELETE | Permanently delete user account |

**POST /api/admin/users body:** `{ "email": "user@example.com", "role": "editor" }`

`role` accepts `"admin"`, `"editor"`, or `"readonly"` (default: no group = readonly).

**GET /api/admin/users response:** Array of `{ username, email, status, enabled, groups, created }`

### Refresh view

`POST /api/admin/refresh-view`

Rebuilds the `current_prices` materialised view. Call after changing any lookup table.

**Response:** `{ "status": "ok", "message": "current_prices view refreshed" }`

### Price corrections

Manual overrides for misreported prices. Original data in `fuel_prices` is never modified — corrections are stored separately and applied in the materialised view. **Requires editor or admin role.**

| Endpoint | Method | Description |
|---|---|---|
| `GET /api/admin/corrections` | GET | List correction history with station context |
| `POST /api/corrections` | POST | Create or update a price correction |
| `POST /api/corrections/batch` | POST | Create or update multiple price corrections |
| `DELETE /api/corrections/{fuel_price_id}` | DELETE | Revert a correction (restore original price) |

**POST /api/corrections body:** `{ "fuel_price_id": 12345, "corrected_price": 139.9 }`

**POST /api/corrections/batch body:** `{ "corrections": [ { "fuel_price_id": 12345, "corrected_price": 139.9 }, ... ] }` (max 200 per batch)

**GET /api/admin/corrections parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `limit` | int | `50` | Max records (1–1000) |
| `offset` | int | `0` | Pagination offset |

**GET response:** `{ rows: [...], total, limit, offset }`

Each row: `{ corrected_at, original_price, corrected_price, reason, corrected_by, trading_name, city, fuel_type, fuel_name, observed_at }`

Creating or deleting a correction automatically refreshes the `current_prices` materialised view and updates affected rows in the `daily_prices` summary table.
