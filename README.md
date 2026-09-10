# Fuel Finder historical tracking

Pulls snapshots from the [GOV.UK Fuel Finder API](https://www.developer.fuel-finder.service.gov.uk/) for UK fuel prices and station data, stores them in PostgreSQL, and optionally backs up raw JSON to S3.

Designed to build a **historical price database** from an API that only provides live snapshots. Every row in `fuel_prices` represents a genuine price change, enabling time-series analysis across ~7,500 UK fuel stations.

<img width="800" alt="fuel price tracking web UI" src="https://github.com/user-attachments/assets/7302c6ae-a34c-478b-a8c9-46e6f840bf88" />

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

### Isolated local analysis environment

Use the local override to keep a production data copy separate from the default
Compose stack. Requires Docker Compose 2.24.4 or newer for `!override` support.

```bash
FUEL_API_ID= FUEL_API_SECRET= docker compose --env-file /dev/null \
    -f docker-compose.yml -f docker-compose.local.yml \
    up -d --build --wait postgres web
```

The dashboard is at http://localhost:18080 and PostgreSQL is at
`127.0.0.1:15432` (database, username and local password: `fuelfinder`).
Both ports bind only to localhost. Data lives in the separate
`fuel-finder-local_pgdata` volume. The web app uses local no-auth mode; Cognito
user management is not configured. The scraper is behind the optional `scrape`
profile and is not started by this command. S3 uploads are disabled in this stack.

The command ignores `.env`, so production credentials are not loaded. Keep local
database dumps in the Git-ignored `.local/` directory. An RDS snapshot cannot be
restored directly into local PostgreSQL: use a consistent `pg_dump --format=custom`
archive instead. Restore into an empty local database before starting the web app,
which applies pending migrations on startup. A restored copy is static until
explicitly refreshed; it does not track production automatically.

Stop the local stack without deleting its database:

```bash
FUEL_API_ID= FUEL_API_SECRET= docker compose --env-file /dev/null \
    -f docker-compose.yml -f docker-compose.local.yml down
```

### Reconstructed history rollout

`RECONSTRUCTED_HISTORY_ENABLED=true` enables station-weighted last-reported-price
history. It is **off by default** and enabled by `docker-compose.local.yml` only.
Migrations 022-024 create derived daily serving caches; they do not change
original price records. Disable the flag and recreate the web container to return
to the legacy event-weighted history. Production deployment requires a separate
decision after the caches have been warmed and benchmarked.

Open http://localhost:18080/#trends. Age controls remain above the chart alongside
the filters, on Trends and station/Search history pages. All filter edits wait for
**Apply filters**; pending edits are labelled and disable downloads until applied.
The selected series is shown alone. **Compare with no age limit** optionally adds
a lighter dashed reference with matching line samples in the legend; toggling it
uses cached browser data and sends no request. Coverage and differences appear
below the chart. Exclusion breakdowns are fetched only when opened and use the
applied filters. Aggregate series retain the existing Hampel policy; single-station
history retains its no-Hampel policy. **Data coverage and sensitivity** on Dashboard checks
the current snapshot, keeping Tukey IQR and anomaly exclusions unchanged. It sits
below the Dashboard charts and does not alter the headline cards.

Dashboard's daily trend uses the same fuel colours and line styling as Trends,
starting at the first available observation within the existing access-tier cap.
Current-price cards already average one latest price per station/fuel and retain
their snapshot exclusions. Their sparklines and historical percentage-change
baselines use reconstructed history when enabled. The percentage-change cards
still compare those daily baselines with the current IQR-filtered snapshot, so
they are not like-for-like changes within the daily historical series.

`reconstructed_daily_prices` stores exact per-station price sums and hour counts for
completed UTC days and the current partial day, for all five age policies, with no
premature rounding or smoothing. Unfiltered national series read the compact
`reconstructed_daily_totals`; unfiltered region/forecourt breakdowns read
`reconstructed_daily_groups`; filtered requests retain station-level detail. Group
classifications are a signed snapshot of current geography/categories. A stale or
incomplete compact cache falls back to the station path, and classification changes
rebuild the groups without rebuilding historical station-day prices. The partial-day response
reports the cache's `partial_through` timestamp. Hampel is still applied after
aggregation.

Warm the cache after applying migrations and before enabling the rollout on a new
database. For this local stack:

```bash
docker exec fuel-finder-local-postgres-1 psql -U fuelfinder -d fuelfinder \
    -v ON_ERROR_STOP=1 -c 'SELECT refresh_reconstructed_daily(); ANALYZE reconstructed_daily_prices; ANALYZE reconstructed_daily_totals; ANALYZE reconstructed_daily_groups;'
```

The scraper refreshes the current partial day and newly completed days after each
run. Triggers invalidate cached dates from the earliest affected observation
onwards when prices, flags or corrections change. Correction endpoints and the
historical importer refresh after saving. Postcode enrichment and
`POST /api/admin/refresh-view` also synchronize groups; changed classifications
require a full grouped-cache rebuild. Direct SQL can leave invalidated dates on the
slower live fallback until the next refresh. Rebuilds are serialized and committed
atomically. No source values are overwritten. An old correction can require
recomputing many days; plan the first warm-up outside peak traffic. Live fallbacks
are bounded by `RECONSTRUCTED_HISTORY_TIMEOUT_MS` (20 seconds by default) and return
HTTP 503 on timeout, preventing abandoned requests from accumulating indefinitely.
Database requests also wait up to `DB_POOL_WAIT_SECONDS` (5 seconds by default) for
one of the bounded pool's connections, then return HTTP 503 rather than an internal
pool-exhaustion error.

Reconstruction selects each station's latest observation at each UTC hour start.
Daily values average those hours within stations, then weight stations equally.
Flagged latest reports create gaps rather than falling back to older clean prices.
Corrections are applied, while historical eligibility keeps the original anomaly
flags as in the audited implementation. Aggregate histories retain Hampel;
single-station histories retain their existing no-Hampel policy. No-limit values
can include old observations and closed stations. They are not confirmed pump prices.

Ranges are inclusive UTC dates, capped at 90 days for read-only and 365 for other
roles. **All data** starts at the filtered stations' first stored observation within
those caps, not an artificial year of leading blank buckets. Station selections and
applied filters survive reload through browser history state; single-station URLs
also include the node ID. The current day contains only available hour-start samples. Exact age
thresholds are included; "Recorded that day" resets at each historical midnight.
Current-snapshot classification drives geography/category filters and group
breakdowns, not historical station membership. Original report exports remain
raw and include flags; they are not exports of reconstructed/smoothed averages.

See `docs/API.md` for the additive response fields and current-sensitivity endpoint.
The full-archive Hampel audit is retained in the ignored `.local/` workspace data.

Frontend state/legend tests: `node --test tests/test_history_controls.js`.

### Archived local comparison prototype

The optional comparison view uses the frozen, verified weighting audit, not live
queries. It covers unleaded and standard diesel from 11 August to 9 September
2026. It is disabled unless the environment is local and an audit file is configured.

Generate a new sensitivity artifact against the local snapshot (the output
directory must not already exist):

```bash
.venv/bin/python scripts/audit_trend_weighting.py \
    --manifest .local/production-20260910T100344Z.json \
    --output .local/trend-age-sensitivity-20260910 --age-sensitivity
```

This uses `psycopg2-binary` and `matplotlib` in the local virtual environment,
read-only local PostgreSQL at port 15432, and the existing local web API at 18080.
Each age policy is checked against an independent SQL reconstruction; group
breakdowns reconcile with national counts. Then enable the view:

```bash
FUEL_API_ID= FUEL_API_SECRET= docker compose --env-file /dev/null \
    -f docker-compose.yml -f docker-compose.local.yml \
    -f docker-compose.comparison.yml up -d --build --wait web
```

The frozen comparison tab is hidden when reconstructed history is enabled. To
revisit it, set `RECONSTRUCTED_HISTORY_ENABLED=false` for the web container before
opening http://localhost:18080/#trend-comparison. Generating the original audit
also requires the legacy history endpoint for its parity check (flag disabled).
The audit JSON is mounted read-only,
never baked into an image. Fuel, date range, baseline, age policy and breakdown
are preserved in the URL. Age options are no limit, 30/14/7 days and recorded
today. Limits apply at each historical UTC hour; "today" starts at midnight of
that historical day, not today's calendar date or the previous 24 hours.

The view reports selected prices, differences from no age limit, fully excluded
stations, removed station-hours, and exclusion rates by snapshot region or
forecourt type. Partial-day stations retain equal station weight; zero eligible
hours means no price, never a zero price. Group rates count station-days, and
classifications describe the snapshot, not historical membership. CSV downloads
include the selected policy, daily values or group breakdowns, and snapshot/audit
hashes. Record age is not the last confirmation. No independent benchmark is used.
The frozen artifacts are retained separately from the on-demand implementation.

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
│  postcode_overrides │──▶ per-station postcode fixes
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
- **Statistical outlier exclusion**: Dashboard averages and current-snapshot breakdowns exclude prices that fall outside the Tukey IQR (interquartile range) fences — Q1 − 1.5×IQR and Q3 + 1.5×IQR — computed per fuel type at materialisation time. Trend charts use a Hampel filter (rolling median ± 3×MAD) instead, which correctly handles trending data. Anomaly-flagged prices are also excluded. Outlier prices are never deleted; they are flagged (`price_is_outlier = true`) and visible on the Anomalies → Statistical Outliers page for full transparency.
- **Forecourt categories**: The API's `is_supermarket_service_station` flag is unreliable (flags BP, Texaco, Maxol as supermarkets). Instead, `brand_categories` maps canonical brands to forecourt types (Supermarket, Major Oil, Motorway Operator, Fuel Group, Convenience, Independent). Motorway flag always takes priority; unmapped brands default to Uncategorised.
- **Numbered migrations**: Schema changes go through `migrations/NNN_name.sql` files, tracked in `schema_migrations`. No external tools — `migrate.py` handles discovery, ordering, and idempotent application.
- **Postcodes.io enrichment**: Each unique postcode is looked up via the free [postcodes.io](https://postcodes.io) bulk API. Results are cached in `postcode_lookups` and provide authoritative lat/lng (fixing ~85 stations with bad API coordinates), admin district, parliamentary constituency, rural/urban classification, LSOA and MSOA. Failed lookups are recorded (NULL coords + timestamp) so they aren't retried.
- **Coordinate correction**: Stations with coordinates outside the UK (lat 49–61, lon -9–2) are excluded from the map. Unrecognised postcodes are surfaced in the Data tab with a "Fix coords" button for manual correction (e.g. sign errors in the source data).
- **Postcode overrides**: Per-station postcode corrections for mistyped or expired postcodes. The corrected postcode is used for geographic enrichment (`COALESCE(override, station_postcode)`) while the original is preserved. Saving an override triggers a postcodes.io lookup to populate full enrichment data.

## Web UI

The project includes a web dashboard at http://localhost:8080 (started via Docker Compose).

Dashboard uses a single fuel selection across its charts, defaulting to E10.
Other fuel menus support chip-based subsets; clearing all chips selects all fuels.
Charts and outlier distributions keep each fuel separate. Multi-fuel map views
use one neutral marker per station with separate prices and record timestamps in
the popup; single-fuel views retain price colours. CSV/JSON downloads include the
selected fuel subset, and current-result downloads follow all result pages.
The archived comparison is limited to its two audited fuels.
Trend and station-history charts default to daily averages. Hourly detail is
fetched only when selected and applied; daily views do not preload hourly data.

**Tabs:**
- **Dashboard** — headline prices, regional chart, forecourt category chart, cheapest brands, rural/urban price comparison, most/least expensive local authorities
- **Map** — every station on a Leaflet map, colour-coded by price, with admin district and rural/urban classification in popups; CSV/JSON download (editor+)
- **Trends** — daily average price line chart with hourly detail on demand, filterable by region, country, and rural/urban classification; CSV/JSON download (editor+)
- **Search** — query builder with postcode, brand, city, price range, category, local authority, constituency, country, and rural/urban filters; CSV/JSON download (editor+); click station names to view individual price trends; "View trend for selected/all results" for aggregate trend charting
- **Anomalies** — anomaly-flagged price records and statistical outliers excluded from current-snapshot averages (with IQR bounds for transparency); price correction tool (editor+)
- **Data** — normalisation report, brand aliases, brand categories, station overrides, postcode issues (stations with unrecognised postcodes + coordinate fix tool), postcode overrides (per-station postcode corrections with postcodes.io enrichment), and materialised view refresh (editor+)
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
│   ├── 014_current_prices_corrections.sql
│   ├── 015_trim_brand_names.sql
│   ├── 016_normalise_geography.sql
│   ├── 017_uncategorised_fallback.sql
│   ├── 018_daily_prices.sql
│   ├── 019_postcode_overrides.sql
│   ├── 020_fix_uncategorised_fallback.sql
│   ├── 021_backfill_daily_prices.sql
│   ├── 022_reconstructed_daily_cache.sql
│   ├── 023_reconstructed_history_serving_cache.sql
│   └── 024_reconstructed_group_cache.sql
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
├── tests/                    # pytest test suite (152 tests)
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

## Acknowledgments

### GOV.UK Fuel Finder API

This project sources all UK fuel price data from the [GOV.UK Fuel Finder API](https://www.developer.fuel-finder.service.gov.uk/). The API provides live snapshot pricing at ~7,500 fuel stations across the UK. We are grateful for this public data source and our use follows the [GOV.UK Fuel Finder developer guidelines](https://www.developer.fuel-finder.service.gov.uk/dev-guideline), including rate limiting, efficient polling, and safeguards on data redistribution. For full details, see the [Scraper](web/static/about.html#the-scraper) and [API usage guidelines](web/static/about.html#api-usage-guidelines) sections on the About page.

### fuelcosts.co.uk

We are grateful to [fuelcosts.co.uk](https://fuelcosts.co.uk) for providing an archive of data published by the UK Fuel Finder from February 7 2026 to March 26 2026. 

### Postcodes.io

Postcode enrichment is provided via [postcodes.io](https://postcodes.io), a free and open API for UK postcode data. This enrichment adds accurate coordinates (fixing ~85 stations with incorrect data), administrative geography, parliamentary constituency, rural/urban classification, and statistical areas to our database.

The postcodes.io service and source code are provided under the [MIT Licence](https://opensource.org/licenses/MIT). The underlying postcode data is used under the following licences:

- **Great Britain postcode data**: [OS OpenData licence](https://www.ordnancesurvey.co.uk/documents/licensing/os-opendata-licence.pdf) (contains Ordnance Survey data © Crown copyright and database right 2026; Royal Mail data © Crown copyright and database right 2026)
- **Northern Ireland postcode data (BT prefix)**: [ONSPD licence](https://www.ons.gov.uk/methodology/geography/licences) (contains National Statistics data © Crown copyright and database right 2026; NRS data © Crown copyright and database right 2026)

For full details, see the [Postcodes.io enrichment](web/static/about.html#postcodesioenrichment) and [Licensing](web/static/about.html#licensing) sections on the About page.

### Methodology and compliance

The methodologies used in this project — anomaly detection rules, statistical outlier exclusion (Tukey IQR fences for snapshots, Hampel filter for trends), brand normalisation, forecourt categorisation, and regional mapping — are fully documented in the [About page](web/static/about.html) of the web application. All averages and statistics shown in the dashboard exclude anomalous and outlier prices, with full transparency: these excluded prices remain in the database and are visible for inspection on the Anomalies page.
