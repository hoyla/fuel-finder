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

    def test_outliers_excluded_field(self, client, has_data):
        data = client.get("/api/summary").json()
        ft = data["by_fuel_type"][0]
        assert "outliers_excluded" in ft
        assert isinstance(ft["outliers_excluded"], int)


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
        resp = client.get("/api/prices/history?fuel_type=E10&days=365").json()
        assert resp["granularity"] == "daily"
        assert isinstance(resp["data"], list)

    def test_hourly_granularity_for_short_range(self, client, has_data):
        resp = client.get("/api/prices/history?fuel_type=E10&days=7").json()
        assert resp["granularity"] == "hourly"
        assert isinstance(resp["data"], list)


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
        assert len(data["rows"]) <= 5
        assert "total" in data
        assert "limit" in data
        assert "offset" in data


class TestOutliers:
    def test_returns_200(self, client, has_data):
        r = client.get("/api/outliers")
        assert r.status_code == 200

    def test_has_bounds_and_outliers(self, client, has_data):
        data = client.get("/api/outliers").json()
        assert "bounds" in data
        assert "outliers" in data
        assert isinstance(data["bounds"], dict)
        assert isinstance(data["outliers"], list)

    def test_bounds_have_iqr_fields(self, client, has_data):
        data = client.get("/api/outliers").json()
        if data["bounds"]:
            b = next(iter(data["bounds"].values()))
            for field in ("q1", "q3", "iqr", "lower_fence", "upper_fence"):
                assert field in b

    def test_fuel_type_filter(self, client, has_data):
        data = client.get("/api/outliers?fuel_type=E10").json()
        for r in data["outliers"]:
            assert r["fuel_type"] == "E10"

    def test_outlier_has_exclusion_reason(self, client, has_data):
        data = client.get("/api/outliers?limit=5").json()
        for r in data["outliers"]:
            assert r["exclusion_reason"] in ("anomaly_flagged", "iqr_outlier")


class TestStationPriceRecords:
    def test_non_anomalous_iqr_outlier_is_shown_in_status(self, client, has_data):
        conn = psycopg2.connect(os.environ["DATABASE_URL"])
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    WITH bounds AS (
                        SELECT fp.fuel_type,
                               PERCENTILE_CONT(0.25) WITHIN GROUP (
                                   ORDER BY COALESCE(pc.corrected_price, fp.price)
                               ) AS q1,
                               PERCENTILE_CONT(0.75) WITHIN GROUP (
                                   ORDER BY COALESCE(pc.corrected_price, fp.price)
                               ) AS q3
                        FROM fuel_prices fp
                        LEFT JOIN price_corrections pc ON pc.fuel_price_id = fp.id
                        WHERE fp.anomaly_flags IS NULL
                        GROUP BY fp.fuel_type
                    )
                    SELECT fp.node_id, fp.fuel_type, fp.id
                    FROM fuel_prices fp
                    LEFT JOIN price_corrections pc ON pc.fuel_price_id = fp.id
                    JOIN bounds b ON b.fuel_type = fp.fuel_type
                    WHERE fp.anomaly_flags IS NULL
                      AND (
                          COALESCE(pc.corrected_price, fp.price) < b.q1 - 1.5 * (b.q3 - b.q1)
                          OR COALESCE(pc.corrected_price, fp.price) > b.q3 + 1.5 * (b.q3 - b.q1)
                      )
                    ORDER BY fp.observed_at DESC
                    LIMIT 1
                """)
                candidate = cur.fetchone()
        finally:
            conn.close()

        if not candidate:
            pytest.skip("No non-anomalous IQR outlier records found in fixture data")

        node_id, fuel_type, fuel_price_id = candidate
        resp = client.get(f"/api/prices/station/{node_id}/records?fuel_type={fuel_type}&limit=500")
        assert resp.status_code == 200
        payload = resp.json()

        row = next((r for r in payload["records"] if r["fuel_price_id"] == fuel_price_id), None)
        assert row is not None
        assert row["effective_is_iqr_outlier"] is True
        assert "iqr_outlier" in (row["effective_flags"] or [])


class TestStaticFiles:
    def test_index_html(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "UK fuel price tracker" in r.text


class TestPricesByCategory:
    def test_returns_200(self, client, has_data):
        r = client.get("/api/prices/by-category?fuel_type=E10")
        assert r.status_code == 200

    def test_has_forecourt_types(self, client, has_data):
        data = client.get("/api/prices/by-category?fuel_type=E10").json()
        types = [d["forecourt_type"] for d in data]
        assert "Supermarket" in types
        assert "Major Oil" in types

    def test_sorted_by_price(self, client, has_data):
        data = client.get("/api/prices/by-category?fuel_type=E10").json()
        prices = [d["avg_price"] for d in data]
        assert prices == sorted(prices)


class TestAdminBrandAliases:
    def test_list_returns_200(self, client, has_data):
        r = client.get("/api/admin/brand-aliases")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_has_fields(self, client, has_data):
        data = client.get("/api/admin/brand-aliases").json()
        assert len(data) > 0
        assert "raw_brand_name" in data[0]
        assert "canonical_brand" in data[0]

    def test_create_and_delete(self, client, has_data):
        # Create
        r = client.post("/api/admin/brand-aliases", json={
            "raw_brand_name": "__TEST_RAW__",
            "canonical_brand": "__TEST_CANONICAL__",
        })
        assert r.status_code == 200
        assert r.json()["raw_brand_name"] == "__TEST_RAW__"
        # Delete
        r = client.delete("/api/admin/brand-aliases/__TEST_RAW__")
        assert r.status_code == 200

    def test_create_empty_rejected(self, client):
        r = client.post("/api/admin/brand-aliases", json={
            "raw_brand_name": "", "canonical_brand": "",
        })
        assert r.status_code == 400


class TestAdminBrandCategories:
    def test_list_returns_200(self, client, has_data):
        r = client.get("/api/admin/brand-categories")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_create_and_delete(self, client, has_data):
        r = client.post("/api/admin/brand-categories", json={
            "canonical_brand": "__TEST_BRAND__",
            "forecourt_type": "Supermarket",
        })
        assert r.status_code == 200
        r = client.delete("/api/admin/brand-categories/__TEST_BRAND__")
        assert r.status_code == 200

    def test_invalid_type_rejected(self, client):
        r = client.post("/api/admin/brand-categories", json={
            "canonical_brand": "__TEST__", "forecourt_type": "InvalidType",
        })
        assert r.status_code == 400


class TestAdminStationOverrides:
    def test_list_returns_200(self, client):
        r = client.get("/api/admin/station-overrides")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_invalid_station_rejected(self, client):
        r = client.post("/api/admin/station-overrides", json={
            "node_id": "NONEXISTENT_NODE_ID_999",
            "canonical_brand": "Test",
        })
        assert r.status_code == 404


class TestNormalisationReport:
    def test_returns_200(self, client, has_data):
        r = client.get("/api/admin/normalisation-report")
        assert r.status_code == 200

    def test_has_fields(self, client, has_data):
        data = client.get("/api/admin/normalisation-report?limit=5").json()
        assert len(data) > 0
        row = data[0]
        assert "raw_brand" in row
        assert "final_brand" in row
        assert "forecourt_type" in row
        assert "resolution_method" in row
        assert "station_count" in row

    def test_filter_unmapped(self, client, has_data):
        data = client.get("/api/admin/normalisation-report?type=unmapped&limit=5").json()
        for row in data:
            assert row["resolution_method"] == "raw"

    def test_filter_aliased(self, client, has_data):
        data = client.get("/api/admin/normalisation-report?type=aliased&limit=5").json()
        for row in data:
            assert row["resolution_method"] == "alias"


class TestRefreshView:
    def test_returns_200(self, client, has_data):
        r = client.post("/api/admin/refresh-view", json={})
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


class TestSearchCategory:
    def test_category_filter(self, client, has_data):
        data = client.get("/api/prices/search?fuel_type=E10&category=Supermarket&limit=10").json()
        for row in data["results"]:
            assert row["forecourt_type"] == "Supermarket"

    def test_results_have_forecourt_type(self, client, has_data):
        data = client.get("/api/prices/search?fuel_type=E10&limit=5").json()
        for row in data["results"]:
            assert "forecourt_type" in row


class TestSearchExtendedFilters:
    def test_country_filter(self, client, has_data):
        data = client.get("/api/prices/search?fuel_type=E10&country=England&limit=5").json()
        assert data["total"] > 0

    def test_motorway_only_filter(self, client, has_data):
        data = client.get("/api/prices/search?fuel_type=E10&motorway_only=true&limit=10").json()
        for row in data["results"]:
            assert row["is_motorway_service_station"] is True

    def test_district_filter(self, client, has_data):
        data = client.get("/api/prices/search?fuel_type=E10&limit=1").json()
        if data["results"] and data["results"][0].get("admin_district"):
            dist = data["results"][0]["admin_district"]
            filtered = client.get(f"/api/prices/search?fuel_type=E10&district={dist}&limit=5").json()
            for row in filtered["results"]:
                assert row["admin_district"] == dist

    def test_exclude_outliers_filter(self, client, has_data):
        r = client.get("/api/prices/search?fuel_type=E10&exclude_outliers=true&limit=5")
        assert r.status_code == 200

    def test_no_upper_limit_cap(self, client, has_data):
        """Search limit has no upper bound."""
        r = client.get("/api/prices/search?fuel_type=E10&limit=1000")
        assert r.status_code == 200


class TestStationHistory:
    def _get_station_id(self, client):
        data = client.get("/api/prices/search?fuel_type=E10&limit=1").json()
        return data["results"][0]["node_id"] if data["results"] else None

    def test_returns_200(self, client, has_data):
        node_id = self._get_station_id(client)
        r = client.get(f"/api/prices/station/{node_id}/history?fuel_type=E10&days=30")
        assert r.status_code == 200

    def test_has_station_info(self, client, has_data):
        node_id = self._get_station_id(client)
        data = client.get(f"/api/prices/station/{node_id}/history?fuel_type=E10&days=7").json()
        assert "station" in data
        assert data["station"]["trading_name"] is not None

    def test_has_granularity_and_data(self, client, has_data):
        node_id = self._get_station_id(client)
        data = client.get(f"/api/prices/station/{node_id}/history?fuel_type=E10&days=7").json()
        assert data["granularity"] in ("hourly", "daily")
        assert isinstance(data["data"], list)

    def test_daily_granularity_for_long_range(self, client, has_data):
        node_id = self._get_station_id(client)
        data = client.get(f"/api/prices/station/{node_id}/history?fuel_type=E10&days=60").json()
        assert data["granularity"] == "daily"

    def test_explicit_granularity_override(self, client, has_data):
        node_id = self._get_station_id(client)
        data = client.get(f"/api/prices/station/{node_id}/history?fuel_type=E10&days=7&granularity=daily").json()
        assert data["granularity"] == "daily"

    def test_date_range_params(self, client, has_data):
        node_id = self._get_station_id(client)
        r = client.get(f"/api/prices/station/{node_id}/history?fuel_type=E10&start_date=2026-01-01&end_date=2026-03-01")
        assert r.status_code == 200


class TestHistoryExtendedFilters:
    def test_country_filter(self, client, has_data):
        resp = client.get("/api/prices/history?fuel_type=E10&days=7&country=England").json()
        assert resp["granularity"] in ("hourly", "daily")
        assert isinstance(resp["data"], list)

    def test_brand_filter(self, client, has_data):
        resp = client.get("/api/prices/history?fuel_type=E10&days=7&brand=tesco").json()
        assert isinstance(resp["data"], list)

    def test_supermarket_only_filter(self, client, has_data):
        resp = client.get("/api/prices/history?fuel_type=E10&days=7&supermarket_only=true").json()
        assert isinstance(resp["data"], list)

    def test_node_ids_filter(self, client, has_data):
        search = client.get("/api/prices/search?fuel_type=E10&limit=3").json()
        ids = ",".join(r["node_id"] for r in search["results"])
        resp = client.get(f"/api/prices/history?fuel_type=E10&days=7&node_ids={ids}").json()
        assert isinstance(resp["data"], list)

    def test_date_range_params(self, client, has_data):
        resp = client.get("/api/prices/history?fuel_type=E10&start_date=2026-01-01&end_date=2026-03-01").json()
        assert resp["granularity"] == "daily"
        assert isinstance(resp["data"], list)

    def test_explicit_granularity_override(self, client, has_data):
        resp = client.get("/api/prices/history?fuel_type=E10&days=7&granularity=daily").json()
        assert resp["granularity"] == "daily"

    def test_multiple_regions(self, client, has_data):
        resp = client.get("/api/prices/history?fuel_type=E10&days=7&region=London,Scotland").json()
        assert isinstance(resp["data"], list)

    def test_category_filter(self, client, has_data):
        resp = client.get("/api/prices/history?fuel_type=E10&days=7&category=Supermarket").json()
        assert isinstance(resp["data"], list)


class TestDistricts:
    def test_returns_200(self, client, has_data):
        r = client.get("/api/districts")
        assert r.status_code == 200

    def test_has_entries(self, client, has_data):
        data = client.get("/api/districts").json()
        assert len(data) > 0
        assert isinstance(data[0], str)


class TestConstituencies:
    def test_returns_200(self, client, has_data):
        r = client.get("/api/constituencies")
        assert r.status_code == 200

    def test_has_entries(self, client, has_data):
        data = client.get("/api/constituencies").json()
        assert len(data) > 0
        assert isinstance(data[0], str)
