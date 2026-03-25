# Fuel Finder Scraper

Scrapes the [GOV.UK Fuel Finder API](https://www.developer.fuel-finder.service.gov.uk/) for UK fuel prices and station data, stores them in PostgreSQL, and optionally backs up raw JSON to S3.

Designed to build a **historical price database** from an API that only provides live snapshots. Every row in `fuel_prices` represents a genuine price change, enabling time-series analysis across ~7,400 UK fuel stations.

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
-- Current prices (materialised view, includes canonical brand names)
SELECT brand_name, city, postcode, fuel_type, price
FROM current_prices
WHERE fuel_type = 'E10'
ORDER BY price
LIMIT 20;

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
│  postcode_regions   │──▶ regional grouping
│  fuel_type_labels   │──▶ human-friendly names
│  current_prices     │──▶ materialised view
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

## File structure

```
fuel-finder-scraper/
├── .env.example              # Template for credentials & config
├── .gitignore                # Excludes .env
├── Dockerfile                # Python 3.11 container for the scraper
├── docker-compose.yml        # Postgres + scraper containers
├── api_client.py             # Fuel Finder API client (OAuth2 + pagination)
├── db.py                     # Database operations (upsert, dedup, anomaly detection)
├── scrape.py                 # Main scraper orchestrator
├── lambda_handler.py         # AWS Lambda entry point
├── schema.sql                # PostgreSQL schema (tables, views, indexes)
├── seed_brand_aliases.sql    # Initial brand name mappings
├── seed_postcode_regions.sql # UK postcode areas → ONS regions
├── seed_fuel_types.sql       # API fuel type codes → human-friendly names
├── queries/                  # Useful SQL queries
│   ├── unmapped_brands.sql   # Find brands needing cleanup
│   ├── regional_analysis.sql # Regional price comparisons for journalism
│   └── anomaly_detection.sql # Spot data-entry errors, outliers
└── docs/
    ├── SCHEMA.md             # Database schema reference
    └── AWS_DEPLOYMENT.md     # AWS deployment guide
```

## See also

- [Database schema reference](docs/SCHEMA.md)
- [AWS deployment guide](docs/AWS_DEPLOYMENT.md)
