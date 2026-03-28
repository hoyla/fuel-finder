# Fuel Finder historical tracking

Pulls snapshots from the [GOV.UK Fuel Finder API](https://www.developer.fuel-finder.service.gov.uk/) for UK fuel prices and station data, stores them in PostgreSQL, and optionally backs up raw JSON to S3.

Designed to build a **historical price database** from an API that only provides live snapshots. Every row in `fuel_prices` represents a genuine price change, enabling time-series analysis across ~7,500 UK fuel stations.

<img width="800" alt="fuel price tracking web UI" src="https://github.com/user-attachments/assets/b70ca4e8-d977-4bff-b877-6f78f1e159ab" />

## Quick start (local)

### Prerequisites

- Docker & Docker Compose
- Fuel Finder API credentials ([register here](https://www.developer.fuel-finder.service.gov.uk/))

### 1. Configure credentials

```bash
cp .env.example .env
# Edit .env with your FUEL_API_ID and FUEL_API_SECRET
```

### 2. Run

```bash
docker compose up --build
```

This starts PostgreSQL and runs a full scrape. The scraper container exits after completion; Postgres keeps running with the data.

### 3. Query the data

```bash
docker compose exec postgres psql -U fuelfinder
```

```sql
-- Current prices (materialised view, includes canonical brand names + forecourt type)
SELECT brand_name, forecourt_type, city, postcode, fuel_type, price
FROM current_prices
WHERE fuel_type = 'E10'
ORDER BY price
LIMIT 20;

-- Average price by forecourt category
SELECT forecourt_type, ROUND(AVG(price), 1) AS avg_price, COUNT(*) AS stations
FROM current_prices
WHERE fuel_type = 'E10' AND NOT temporary_closure
GROUP BY forecourt_type
ORDER BY avg_price;

-- Price history for a station
SELECT fuel_type, price, observed_at
FROM fuel_prices
WHERE node_id = '<node_id>'
ORDER BY observed_at;
```

### 4. Run subsequent scrapes

```bash
# Auto mode: incremental if previous scrape exists, full otherwise
docker compose run --rm scraper python scrape.py auto

# Force a full scrape (re-fetches all stations + prices)
docker compose run --rm scraper python scrape.py full

# Incremental only (prices changed since last scrape)
docker compose run --rm scraper python scrape.py incremental
```

## Architecture

```
┌─────────────────────┐
│   Fuel Finder API   │
│  (GOV.UK, OAuth2)   │
└─────────┬───────────┘
          │ GET /api/v1/pfs/fuel-prices?batch-number=N
          │ GET /api/v1/pfs?batch-number=N
          ▼
┌─────────────────────┐     ┌──────────┐
│      Scraper        │────▶│  S3      │  (raw JSON backup)
│  (Python / Lambda)  │     └──────────┘
└─────────┬───────────┘
          │ INSERT / UPSERT
          ▼
┌─────────────────────┐
│    PostgreSQL       │
│  stations (raw)     │
│  fuel_prices (raw)  │
│  brand_aliases      │──▶ normalisation
│  station_overrides  │
│  brand_categories   │──▶ forecourt type classification
│  postcode_regions   │──▶ regional grouping
│  postcode_lookups   │──▶ postcodes.io enrichment
│  fuel_type_labels   │──▶ human-friendly names
│  current_prices     │──▶ materialised view
└─────────────────────┘
          │
          ▼
┌─────────────────────┐
│    Web UI (FastAPI) │  http://localhost:8080
│  Dashboard, Map,    │
│  Trends, Search,    │
│  Anomalies, Data,   │
│  Logs, Users        │
└─────────────────────┘
```

## API endpoints used

| Endpoint | Purpose |
|---|---|
| `POST /api/v1/oauth/generate_access_token` | OAuth2 client credentials → bearer token (1h TTL) |
| `GET /api/v1/pfs/fuel-prices?batch-number=N` | Fuel prices, 500 stations/batch, 15 batches |
| `GET /api/v1/pfs/fuel-prices?batch-number=N&effective-start-timestamp=<ts>` | Incremental price updates since timestamp |
| `GET /api/v1/pfs?batch-number=N` | Station info (address, brand, amenities, opening times) |

## Scrape modes

| Mode | What it does | When to use |
|---|---|---|
| `full` | Fetches all stations + all prices (15 batches each) | First run, daily refresh |
| `incremental` | Fetches only changed prices since last successful scrape | Regular polling (every 30 min) |
| `auto` | Incremental if a previous scrape exists, otherwise full | Default — set and forget |

## Key design decisions

- **Append-only prices**: `fuel_prices` only stores rows when the price has actually changed from the last stored value. This keeps storage lean and means every row is a genuine price change event.
- **Last-reported = current**: The `current_prices` materialised view treats the most recent observation as the current price, regardless of age. Stations that report infrequently are still included.
- **Raw data preserved**: Normalisation (brand cleanup) is done via lookup tables (`brand_aliases`, `station_brand_overrides`) that sit alongside the raw data. Original values are never modified.
- **Brand resolution**: `COALESCE(station_override, brand_alias, raw_brand_name)` — per-station overrides take priority, then bulk aliases, then the raw API value.
- **Regional grouping**: Postcode area prefixes are mapped to ONS-style regions (London, North West, Scotland, etc.) for regional comparisons. Seeded from `seed_postcode_regions.sql`.
- **Fuel type labels**: API codes like `B7_STANDARD` are mapped to human names like "Diesel" via the `fuel_type_labels` table. Seeded from `seed_fuel_types.sql`.
- **Anomaly detection**: On insert, prices are flagged (not filtered) if they fall outside 80–300p, look like decimal-place errors, or jump by more than 30%. Flags are stored in `anomaly_flags` on each `fuel_prices` row. See `queries/anomaly_detection.sql`.
- **Statistical outlier exclusion**: All dashboard averages exclude prices that fall outside the Tukey IQR (interquartile range) fences — Q1 − 1.5×IQR and Q3 + 1.5×IQR — computed per fuel type at materialisation time. Anomaly-flagged prices are also excluded. Outlier prices are never deleted; they are flagged (`price_is_outlier = true`) and visible on the Anomalies → Statistical Outliers page for full transparency.
- **Forecourt categories**: The API's `is_supermarket_service_station` flag is unreliable (flags BP, Texaco, Maxol as supermarkets). Instead, `brand_categories` maps canonical brands to forecourt types (Supermarket, Major Oil, Motorway Operator, Fuel Group, Convenience). Motorway flag always takes priority; unmapped brands default to Independent.
- **Numbered migrations**: Schema changes go through `migrations/NNN_name.sql` files, tracked in `schema_migrations`. No external tools — `migrate.py` handles discovery, ordering, and idempotent application.
- **Postcodes.io enrichment**: Each unique postcode is looked up via the free [postcodes.io](https://postcodes.io) bulk API. Results are cached in `postcode_lookups` and provide authoritative lat/lng (fixing ~85 stations with bad API coordinates), admin district, parliamentary constituency, rural/urban classification, LSOA and MSOA. Failed lookups are recorded (NULL coords + timestamp) so they aren't retried.
- **Coordinate correction**: Stations with coordinates outside the UK (lat 49–61, lon -9–2) are excluded from the map. Unrecognised postcodes are surfaced in the Data tab with a "Fix coords" button for manual correction (e.g. sign errors in the source data).

## Web UI

The project includes a web dashboard at http://localhost:8080 (started via Docker Compose).

**Tabs:**
- **Dashboard** — headline prices, regional chart, forecourt category chart, cheapest brands, rural/urban price comparison, most/least expensive local authorities
- **Map** — every station on a Leaflet map, colour-coded by price, with admin district and rural/urban classification in popups; CSV/JSON download (editor+)
- **Trends** — average price line chart with hourly granularity for ≤30 days and daily for longer ranges, filterable by region, country, and rural/urban classification; CSV/JSON download (editor+)
- **Search** — query builder with postcode, brand, city, price range, category, local authority, constituency, country, and rural/urban filters; CSV/JSON download (editor+); click station names to view individual price trends; "View trend for selected/all results" for aggregate trend charting
- **Anomalies** — anomaly-flagged price records and statistical outliers excluded from averages (with IQR bounds for transparency); price correction tool (editor+)
- **Data** — normalisation report, brand aliases, brand categories, station overrides, postcode issues (stations with unrecognised postcodes + coordinate fix tool), and materialised view refresh (editor+)
- **Logs** — scrape run history and price correction audit trail
- **Users** — Cognito user management (admin only)

**User roles:**

Three-tier role system via Cognito groups:

| Role | Access |
|---|---|
| **Admin** | Everything — user management, data mutations, exports, tier preview switcher |
| **Editor** | Data mutations (aliases, categories, overrides, corrections), exports, view refresh |
| **Read-only** | View dashboards, map, trends, search (capped at 200 results, 90-day history) — no exports or data changes |

**API documentation:** see the [API docs page](http://localhost:8080/docs/api) (served from the web UI) or [docs/API.md](docs/API.md).

## File structure

```
fuel-finder-scraper/
├── .env.example              # Template for credentials & config
├── .gitignore                # Excludes .env
├── Dockerfile                # Python 3.11 container for the scraper
├── docker-compose.yml        # Postgres + scraper + web containers
├── api_client.py             # Fuel Finder API client (OAuth2 + pagination)
├── db.py                     # Database operations (upsert, dedup, anomaly detection)
├── scrape.py                 # Main scraper orchestrator
├── migrate.py                # Numbered SQL migration runner
├── lambda_handler.py         # AWS Lambda entry point
├── enrich_postcodes.py       # postcodes.io bulk lookup + enrichment
├── schema.sql                # Full schema reference (tables, views, indexes)
├── migrations/               # Numbered SQL migrations (source of truth)
│   ├── 001_base_schema.sql
│   ├── 002_seed_brand_aliases.sql
│   ├── 003_seed_postcode_regions.sql
│   ├── 004_seed_fuel_types.sql
│   ├── 005_current_prices_view.sql
│   ├── 006_brand_categories.sql
│   ├── 007_current_prices_forecourt_type.sql
│   ├── 008_postcode_lookups.sql
│   ├── 009_current_prices_postcode_enrichment.sql
│   ├── 010_update_fuel_names.sql
│   ├── 011_outlier_exclusion.sql
│   ├── 012_performance_indexes.sql
│   ├── 013_price_corrections.sql
│   └── 014_current_prices_corrections.sql
├── seed_brand_aliases.sql    # Legacy seed file (superseded by migrations)
├── seed_postcode_regions.sql # Legacy seed file (superseded by migrations)
├── seed_fuel_types.sql       # Legacy seed file (superseded by migrations)
├── queries/                  # Useful SQL queries
│   ├── unmapped_brands.sql
│   ├── regional_analysis.sql
│   └── anomaly_detection.sql
├── web/                      # FastAPI web UI
│   ├── Dockerfile
│   ├── api.py                # API endpoints (three-tier auth: readonly / editor / admin)
│   ├── auth.py               # Authentication & authorisation (Cognito JWT, API key, roles)
│   └── static/
│       ├── index.html        # HTML shell + tab structure for the SPA
│       ├── style.css         # Main app CSS
│       ├── docs.css          # Shared CSS for documentation pages
│       ├── js/               # Modular JavaScript
│       │   ├── shared.js     # Auth, utilities, delegation handlers
│       │   ├── router.js     # Hash-based tab routing
│       │   ├── dashboard.js  # Dashboard charts and cards
│       │   ├── map.js        # Leaflet map tab
│       │   ├── trends.js     # Price trend charts
│       │   ├── search.js     # Search, station trend, price editor
│       │   ├── admin.js      # Anomalies, data management, logs, users
│       │   └── app.js        # Initialisation
│       ├── api.html          # API documentation page
│       └── about.html        # How the scraper works
├── tests/                    # pytest test suite (151 tests)
│   ├── conftest.py
│   ├── test_anomaly_detection.py
│   ├── test_api.py
│   ├── test_auth_tiers.py    # Three-tier auth tests (role gating, caps, overrides)
│   └── test_migrate.py
├── docs/
│   ├── SCHEMA.md             # Database schema reference
│   ├── API.md                # API endpoint reference
│   └── AWS_DEPLOYMENT.md     # AWS deployment guide
└── pyproject.toml            # pytest config
```

## See also

- [Database schema reference](docs/SCHEMA.md)
- [API endpoint reference](docs/API.md)
- [AWS deployment guide](docs/AWS_DEPLOYMENT.md)
