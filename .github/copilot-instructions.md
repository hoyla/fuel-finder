# Copilot Instructions — Fuel Finder

## Project overview

UK fuel price tracker. Scrapes the GOV.UK Fuel Finder API, stores price changes in PostgreSQL, serves a FastAPI web app with a single-page dashboard.

## Architecture

- **web/api.py** — all FastAPI endpoints (~40+), psycopg2 connection pool
- **web/auth.py** — authentication & authorisation (Cognito JWT, API key, no-auth fallback)
- **web/static/index.html** — entire frontend SPA (Chart.js, Leaflet maps, tabbed UI)
- **web/static/api.html** — on-site API reference
- **web/static/about.html** — methodology page
- **scrape.py** — scraper orchestrator (runs on Lambda)
- **migrate.py** — numbered SQL migration runner (migrations/ directory, currently 001–014)
- **docker-compose.yml** — postgres + web containers for local dev

## Three-tier auth model

Roles determined by Cognito group membership:

| Role | Group | Access |
|---|---|---|
| admin | `admin` | Everything — user management, mutations, exports, tier preview |
| editor | `editor` | Mutations (aliases, categories, overrides, corrections), exports, view refresh |
| readonly | (none) | Read with caps: search ≤200 results, history ≤90 days, no exports |

Key auth dependencies in web/auth.py: `require_auth`, `require_editor`, `require_admin`, `resolve_role`.
Admin can preview lower tiers via `X-Role-Override` header.

## Running tests

```bash
# Requires postgres running
docker compose up -d postgres

# Tests run from host, NOT inside Docker
.venv/bin/python -m pytest tests/
```

- `.venv` at project root (Python 3.14)
- conftest.py adds project root + web/ to sys.path
- **Important**: test_auth_tiers.py uses FastAPI dependency overrides — must `import auth` (bare module via sys.path) not `from web import auth`, otherwise overrides don't match the Depends() references

## Database

- **current_prices** materialised view is the main query source for all dashboard/API endpoints
- Price corrections use `COALESCE(corrected_price, original_price)` — originals never modified
- Brand normalisation: `station_override > brand_alias > raw_brand_name`
- Outlier exclusion: Tukey IQR 1.5× fences + rule-based anomaly flags

## Code conventions

- Single-file frontend — all JS/CSS/HTML in index.html
- No build step, no bundler, no framework
- API endpoints use psycopg2 RealDictCursor (returns dicts, not tuples)
- Parameterised queries only — never f-string SQL

## Git workflow

- Never amend existing commits — always prefer separate follow-up commits
- Never push to origin without asking for confirmation first
- Use `git commit -F <file>` for multiline commit messages (heredocs are unreliable in the terminal tool)
