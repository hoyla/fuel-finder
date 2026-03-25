"""Integration tests for the FastAPI web API.

These run against the live local database, so require Docker Compose postgres
to be running with data loaded.
"""

import os

import psycopg2
import pytest
from fastapi.testclient import TestClient

# Ensure DATABASE_URL points to the local dev database
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://fuelfinder:fuelfinder@localhost:5432/fuelfinder",
)

# Import after setting env var
from web.api import app


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


@pytest.fixture(scope="module")
def has_data():
    """Check if the database has scrape data — skip tests if empty."""
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM current_prices")
        count = cur.fetchone()[0]
    conn.close()
    if count == 0:
        pytest.skip("No data in current_prices — run a scrape first")
    return count


class TestSummary:
    def test_returns_200(self, client, has_data):
        r = client.get("/api/summary")
        assert r.status_code == 200

    def test_has_fuel_types(self, client, has_data):
        data = client.get("/api/summary").json()
        assert "by_fuel_type" in data
        assert len(data["by_fuel_type"]) > 0

    def test_has_totals(self, client, has_data):
        data = client.get("/api/summary").json()
        assert data["total_stations"] > 0
        assert data["total_prices"] > 0

    def test_fuel_type_fields(self, client, has_data):
        data = client.get("/api/summary").json()
        ft = data["by_fuel_type"][0]
        assert "fuel_type" in ft
        assert "avg_price" in ft
        assert "min_price" in ft
        assert "station_count" in ft


class TestPricesByRegion:
    def test_returns_200(self, client, has_data):
        r = client.get("/api/prices/by-region?fuel_type=E10")
        assert r.status_code == 200

    def test_returns_regions(self, client, has_data):
        data = client.get("/api/prices/by-region?fuel_type=E10").json()
        assert len(data) > 0
        regions = [d["region"] for d in data]
        assert any(r in regions for r in ["London", "Scotland", "North West"])

    def test_has_avg_price(self, client, has_data):
        data = client.get("/api/prices/by-region?fuel_type=E10").json()
        for row in data:
            assert row["avg_price"] > 0
            assert row["station_count"] > 0


class TestPricesByBrand:
    def test_returns_200(self, client, has_data):
        r = client.get("/api/prices/by-brand?fuel_type=E10")
        assert r.status_code == 200

    def test_respects_limit(self, client, has_data):
        data = client.get("/api/prices/by-brand?fuel_type=E10&limit=5").json()
        assert len(data) <= 5

    def test_sorted_by_price(self, client, has_data):
        data = client.get("/api/prices/by-brand?fuel_type=E10").json()
        prices = [d["avg_price"] for d in data]
        assert prices == sorted(prices)


class TestPriceHistory:
    def test_returns_200(self, client, has_data):
        r = client.get("/api/prices/history?fuel_type=E10&days=30")
        assert r.status_code == 200

    def test_with_region_filter(self, client, has_data):
        r = client.get("/api/prices/history?fuel_type=E10&days=30&region=London")
        assert r.status_code == 200

    def test_returns_days(self, client, has_data):
        data = client.get("/api/prices/history?fuel_type=E10&days=365").json()
        # Should return at least one day if data exists
        assert isinstance(data, list)


class TestPriceMap:
    def test_returns_200(self, client, has_data):
        r = client.get("/api/prices/map?fuel_type=E10")
        assert r.status_code == 200

    def test_has_coordinates(self, client, has_data):
        data = client.get("/api/prices/map?fuel_type=E10").json()
        assert len(data) > 0
        assert data[0]["latitude"] is not None
        assert data[0]["longitude"] is not None

    def test_has_station_info(self, client, has_data):
        data = client.get("/api/prices/map?fuel_type=E10").json()
        row = data[0]
        assert "trading_name" in row
        assert "price" in row
        assert "postcode" in row


class TestSearch:
    def test_returns_200(self, client, has_data):
        r = client.get("/api/prices/search?fuel_type=E10")
        assert r.status_code == 200

    def test_returns_paginated(self, client, has_data):
        data = client.get("/api/prices/search?fuel_type=E10&limit=10").json()
        assert "results" in data
        assert "total" in data
        assert len(data["results"]) <= 10

    def test_postcode_filter(self, client, has_data):
        data = client.get("/api/prices/search?fuel_type=E10&postcode=SW").json()
        for row in data["results"]:
            assert row["postcode"].upper().startswith("SW")

    def test_supermarket_filter(self, client, has_data):
        data = client.get("/api/prices/search?fuel_type=E10&supermarket_only=true").json()
        for row in data["results"]:
            assert row["is_supermarket_service_station"] is True

    def test_price_range_filter(self, client, has_data):
        data = client.get("/api/prices/search?fuel_type=E10&min_price=140&max_price=160").json()
        for row in data["results"]:
            assert 140 <= float(row["price"]) <= 160

    def test_pagination(self, client, has_data):
        page1 = client.get("/api/prices/search?fuel_type=E10&limit=5&offset=0").json()
        page2 = client.get("/api/prices/search?fuel_type=E10&limit=5&offset=5").json()
        if page1["total"] > 5:
            # Different results on different pages
            ids1 = {r["node_id"] for r in page1["results"]}
            ids2 = {r["node_id"] for r in page2["results"]}
            assert ids1 != ids2


class TestFuelTypes:
    def test_returns_200(self, client):
        r = client.get("/api/fuel-types")
        assert r.status_code == 200

    def test_has_entries(self, client, has_data):
        data = client.get("/api/fuel-types").json()
        assert len(data) > 0
        assert "fuel_type_code" in data[0]
        assert "fuel_name" in data[0]


class TestRegions:
    def test_returns_200(self, client):
        r = client.get("/api/regions")
        assert r.status_code == 200

    def test_has_regions(self, client, has_data):
        data = client.get("/api/regions").json()
        assert len(data) > 0
        assert "London" in data


class TestAnomalies:
    def test_returns_200(self, client):
        r = client.get("/api/anomalies")
        assert r.status_code == 200

    def test_respects_limit(self, client):
        data = client.get("/api/anomalies?limit=5").json()
        assert len(data) <= 5


class TestStaticFiles:
    def test_index_html(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "Fuel Finder" in r.text
