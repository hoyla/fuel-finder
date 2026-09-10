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


class TestFrontendCaching:
    @pytest.mark.parametrize("path", ["/", "/static/js/dashboard.js", "/static/style.css", "/docs/api", "/docs/about"])
    def test_unversioned_frontend_revalidates(self, client, path):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers.get("cache-control") == "no-cache"
        assert response.headers.get("etag")

    @pytest.mark.parametrize("path", ["/static/js/dashboard.js", "/static/style.css"])
    def test_unchanged_assets_keep_conditional_responses(self, client, path):
        response = client.get(path)
        unchanged = client.get(path, headers={"If-None-Match": response.headers["etag"]})
        assert unchanged.status_code == 304
        assert unchanged.headers.get("cache-control") == "no-cache"
        assert unchanged.content == b""

    def test_api_cache_policy_is_unchanged(self, client):
        response = client.get("/auth/config")
        assert response.status_code == 200
        assert "cache-control" not in response.headers


@pytest.fixture
def reconstruction_db():
    from psycopg2.extras import RealDictCursor
    connection = psycopg2.connect(os.environ["DATABASE_URL"], cursor_factory=RealDictCursor)
    with connection.cursor() as cursor:
        for table in ("current_prices", "fuel_prices", "price_corrections"):
            from psycopg2 import sql
            cursor.execute(sql.SQL("CREATE TEMP TABLE {} AS SELECT * FROM public.{} WITH NO DATA").format(sql.Identifier(table), sql.Identifier(table)))
        cursor.execute("""INSERT INTO current_prices (node_id, fuel_type, price, region, forecourt_type, temporary_closure)
                          VALUES ('old', 'E10', 100, 'North', 'Independent', false),
                                 ('changing', 'E10', 220, 'South', 'Supermarket', false)""")
        cursor.execute("""INSERT INTO fuel_prices (id, node_id, fuel_type, price, observed_at)
                          VALUES (1, 'old', 'E10', 100, '2026-08-01T00:00:00Z'),
                                 (2, 'changing', 'E10', 200, '2026-08-11T00:00:00Z'),
                                 (3, 'changing', 'E10', 220, '2026-08-11T12:00:00Z')""")
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()


class TestReconstructedHistory:
    @pytest.mark.parametrize("minimum,expected_fuels", [(0, {"E10", "B7_STANDARD"}), (210, {"E10"})])
    def test_multi_fuel_export_keeps_price_filter_per_fuel(self, client, reconstruction_db, monkeypatch, minimum, expected_fuels):
        from types import SimpleNamespace
        from web import api as api_module
        with reconstruction_db.cursor() as cursor:
            cursor.execute("""INSERT INTO current_prices (node_id, fuel_type, price, temporary_closure)
                              VALUES ('changing', 'B7_STANDARD', 90, false)""")
            cursor.execute("""INSERT INTO fuel_prices (id, node_id, fuel_type, price, observed_at)
                              VALUES (4, 'changing', 'B7_STANDARD', 90, '2026-08-11T00:00:00Z')""")
        monkeypatch.setattr(api_module, "_pool", SimpleNamespace(getconn=lambda: reconstruction_db, putconn=lambda connection: None))
        response = client.get("/api/prices/history/export", params={
            "fuel_type": "E10,B7_STANDARD", "start_date": "2026-08-11", "end_date": "2026-08-11",
            "min_price": minimum, "format": "json",
        })
        assert response.status_code == 200
        assert {row["fuel_type"] for row in response.json()} == expected_fuels

    def test_station_history_keeps_age_control_and_all_data_range(self, client, reconstruction_db):
        from web import api as api_module
        app.dependency_overrides[api_module.get_db] = lambda: reconstruction_db
        try:
            response = client.get("/api/prices/station/old/history?fuel_type=E10&end_date=2026-08-12&granularity=daily&age_limit=7&include_sensitivity=true")
            assert response.status_code == 200
            payload = response.json()
            assert payload["smoothing"] == "none"
            assert payload["age_limit"] == "7"
            assert payload["data"][0]["bucket"] == "2026-08-01"
            assert len(payload["data"]) == 12
            assert payload["data"][-1]["age_price"] is None
            assert float(payload["data"][-1]["avg_price"]) == 100
        finally:
            app.dependency_overrides.pop(api_module.get_db, None)
    def test_all_data_starts_at_selected_stations_first_record(self, client, reconstruction_db):
        from web import api as api_module
        app.dependency_overrides[api_module.get_db] = lambda: reconstruction_db
        try:
            response = client.get("/api/prices/history?fuel_type=E10&end_date=2026-08-12&granularity=daily&node_ids=changing")
            assert response.status_code == 200
            payload = response.json()
            assert payload["range_start"] == "2026-08-11T00:00:00+00:00"
            assert len(payload["data"]) == 2
            assert payload["data"][0]["avg_price"] == 210
            assert not payload["range_capped"]
        finally:
            app.dependency_overrides.pop(api_module.get_db, None)

    def test_all_data_keeps_access_tier_cap(self, reconstruction_db):
        from datetime import datetime, timezone
        from price_history import resolve_history_window
        now = datetime(2026, 9, 10, tzinfo=timezone.utc)
        with reconstruction_db.cursor() as cursor:
            cursor.execute("UPDATE fuel_prices SET observed_at = '2025-01-01T00:00:00Z' WHERE id = 1")
        start, end, capped = resolve_history_window(reconstruction_db, "E10", {}, end_date="2026-09-09", role="readonly", now=now)
        assert (end - start).days == 90
        assert capped

    @pytest.fixture(autouse=True)
    def enable_reconstruction(self, monkeypatch):
        monkeypatch.setenv("RECONSTRUCTED_HISTORY_ENABLED", "true")

    def test_daily_equal_station_weight_and_age_limit(self, reconstruction_db):
        from datetime import datetime, timezone
        from price_history import reconstruct
        start = datetime(2026, 8, 11, tzinfo=timezone.utc)
        end = datetime(2026, 8, 12, tzinfo=timezone.utc)
        result = reconstruct(reconstruction_db, "E10", start, end, age_limit="7", include_groups=True)
        assert float(result["data"][0]["avg_price"]) == 155
        assert float(result["data"][0]["age_price"]) == 210
        assert result["data"][0]["stations"] == 2
        assert result["data"][0]["included_stations"] == 1
        assert result["data"][0]["included_hours"] == 24
        assert sum(row["excluded_stations"] for row in result["groups"] if row["dimension"] == "region") == 1

    def test_hourly_values_and_selected_station_filter(self, reconstruction_db):
        from datetime import datetime, timezone
        from price_history import reconstruct
        start = datetime(2026, 8, 11, tzinfo=timezone.utc)
        end = datetime(2026, 8, 12, tzinfo=timezone.utc)
        result = reconstruct(reconstruction_db, "E10", start, end, "hourly", filters={"node_ids": "changing"})
        assert len(result["data"]) == 24
        assert float(result["data"][0]["avg_price"]) == 200
        assert float(result["data"][12]["avg_price"]) == 220
        assert all(row["stations"] == 1 for row in result["data"])

    def test_endpoint_uses_reconstruction_and_paired_hampel(self, client, reconstruction_db):
        from web import api as api_module
        app.dependency_overrides[api_module.get_db] = lambda: reconstruction_db
        try:
            response = client.get("/api/prices/history?fuel_type=E10&start_date=2026-08-11&end_date=2026-08-11&granularity=daily&age_limit=7&include_sensitivity=true")
            assert response.status_code == 200
            data = response.json()
            assert data["method"] == "last_reported_station_weighted"
            assert data["smoothing"] == "hampel"
            assert data["data"][0]["avg_price"] == 155
            assert data["data"][0]["age_price"] == 210
            assert data["data"][0]["unsmoothed_avg_price"] == 155
            assert data["data"][0]["unsmoothed_age_price"] == 210
            assert len(data["groups"]) == 4
        finally:
            app.dependency_overrides.pop(api_module.get_db, None)

    @pytest.mark.parametrize("query", ["start_date=bad", "start_date=2026-09-09&end_date=2026-08-01", "age_limit=bad", "granularity=bad"])
    def test_invalid_parameters(self, client, query):
        assert client.get("/api/prices/history?" + query).status_code == 422

    def test_current_sensitivity_keeps_snapshot_iqr_exclusions(self, reconstruction_db):
        from datetime import datetime, timezone
        from price_history import snapshot_sensitivity
        with reconstruction_db.cursor() as cursor:
            cursor.execute("UPDATE current_prices SET price_is_outlier = false, observed_at = '2026-08-11T00:00:00Z'")
            cursor.execute("UPDATE current_prices SET price_is_outlier = true WHERE node_id = 'changing'")
        data = snapshot_sensitivity(reconstruction_db, "E10", "none", datetime(2026, 8, 12, tzinfo=timezone.utc))
        assert float(data["data"]["avg_price"]) == 100
        assert data["data"]["stations"] == 1
        assert data["data"]["included_stations"] == 1

    @pytest.mark.parametrize("age_limit", ["none", "30", "14", "7", "today"])
    @pytest.mark.parametrize("granularity", ["daily", "hourly"])
    def test_interval_sql_matches_audited_sampling(self, reconstruction_db, age_limit, granularity):
        from datetime import datetime, timezone
        from price_history import reconstruct
        from scripts.audit_trend_weighting import compare_prices, load_events
        start = datetime(2026, 8, 11, tzinfo=timezone.utc)
        end = datetime(2026, 8, 13, tzinfo=timezone.utc)
        with reconstruction_db.cursor() as cursor:
            cursor.execute("""INSERT INTO fuel_prices (id, node_id, fuel_type, price, observed_at, anomaly_flags)
                              VALUES (4, 'changing', 'E10', 206, '2026-08-11T00:15:00Z', NULL),
                                     (5, 'changing', 'E10', 240, '2026-08-11T18:00:00Z', ARRAY['test_flag']),
                                     (6, 'changing', 'E10', 250, '2026-08-11T23:15:00Z', NULL)""")
            cursor.execute("INSERT INTO price_corrections (id, fuel_price_id, original_price, corrected_price) VALUES (1, 1, 100, 140)")
        events = load_events(reconstruction_db, "E10", start, end)
        selected_hours = []
        selected_days, _ = compare_prices(events, start, end, age_limit, hourly_output=selected_hours)
        reference_hours = []
        reference_days, _ = compare_prices(events, start, end, hourly_output=reference_hours)
        result = reconstruct(reconstruction_db, "E10", start, end, granularity, age_limit)["data"]
        expected = zip(reference_days, selected_days) if granularity == "daily" else zip(reference_hours, selected_hours)
        for row, (reference, selected) in zip(result, expected):
            value = reference["equal_station_hourly"] if granularity == "daily" else reference["avg_price"]
            limited = selected["equal_station_hourly"] if granularity == "daily" else selected["avg_price"]
            assert abs(float(row["avg_price"]) - value) <= 0.051
            if limited is None:
                assert row["age_price"] is None
            else:
                assert abs(float(row["age_price"]) - limited) <= 0.051
            assert row["included_stations"] == selected["reconstructed_stations" if granularity == "daily" else "stations"]

    def test_filter_intersection_and_quotes_are_safe(self, reconstruction_db):
        from datetime import datetime, timezone
        from price_history import reconstruct
        start = datetime(2026, 8, 11, tzinfo=timezone.utc)
        end = datetime(2026, 8, 12, tzinfo=timezone.utc)
        assert reconstruct(reconstruction_db, "E10", start, end, filters={"node_ids": "old", "region": "South"})["data"] == []
        assert reconstruct(reconstruction_db, "E10", start, end, filters={"brand": "' OR 1=1 --"})["data"] == []
        result = reconstruct(reconstruction_db, "E10", start, end, filters={"min_price": 210})
        assert float(result["data"][0]["avg_price"]) == 210

    def test_rollout_disabled_keeps_current_sensitivity_unavailable(self, client, monkeypatch):
        monkeypatch.setenv("RECONSTRUCTED_HISTORY_ENABLED", "false")
        assert not client.get("/auth/config").json()["reconstructed_history"]
        assert client.get("/api/prices/current/sensitivity").status_code == 404

    def test_raw_export_preserves_history_station_filters(self, client, reconstruction_db, monkeypatch):
        from types import SimpleNamespace
        from web import api as api_module
        pool = SimpleNamespace(getconn=lambda: reconstruction_db, putconn=lambda connection: None)
        monkeypatch.setattr(api_module, "_pool", pool)
        response = client.get("/api/prices/history/export?fuel_type=E10&start_date=2026-08-11&end_date=2026-08-11&min_price=210&format=json")
        assert response.status_code == 200
        records = response.json()
        assert len(records) == 2
        assert all(row["node_id"] == "changing" for row in records)
        assert {float(row["original_price"]) for row in records} == {200, 220}


class TestLocalTrendComparison:
    @pytest.mark.parametrize("environment,configured,enabled", [
        ("local", True, True), ("local", False, False),
        ("production", True, False), ("staging", True, False),
    ])
    def test_local_gate(self, client, monkeypatch, tmp_path, environment, configured, enabled):
        from web import api as api_module

        artifact = tmp_path / "results.json"
        artifact.write_text('{"fuels": {"E10": {"daily": []}}}')
        monkeypatch.setattr(api_module, "import_env", environment)
        if configured:
            monkeypatch.setenv("TREND_COMPARISON_FILE", str(artifact))
        else:
            monkeypatch.delenv("TREND_COMPARISON_FILE", raising=False)
        response = client.get("/api/local/trend-comparison")
        assert response.status_code == (200 if enabled else 404)
        assert client.get("/auth/config").json().get("trend_comparison", False) is enabled
        if enabled:
            assert response.json() == {"fuels": {"E10": {"daily": []}}}
            assert response.headers["cache-control"] == "no-store"

    def test_missing_artifact_is_not_advertised(self, client, monkeypatch, tmp_path):
        monkeypatch.setenv("TREND_COMPARISON_FILE", str(tmp_path / "missing.json"))
        assert client.get("/api/local/trend-comparison").status_code == 404
        assert "trend_comparison" not in client.get("/auth/config").json()


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
    def test_fuel_subset_preserves_total_and_pagination(self, client, has_data):
        fuels = {"E10", "B7_STANDARD"}
        expected = sum(client.get("/api/prices/search", params={"fuel_type": fuel, "limit": 1}).json()["total"] for fuel in fuels)
        for offset in (0, 7):
            response = client.get("/api/prices/search", params={"fuel_type": "E10,B7_STANDARD", "limit": 7, "offset": offset})
            assert response.status_code == 200
            data = response.json()
            assert data["total"] == expected
            assert len(data["results"]) == 7
            assert {row["fuel_type"] for row in data["results"]} <= fuels

    @pytest.mark.parametrize("selection", [None, "E10,B7_STANDARD"])
    def test_history_export_all_or_subset(self, client, has_data, selection):
        node = client.get("/api/prices/search?fuel_type=E10&limit=1").json()["results"][0]["node_id"]
        parameters = {"node_id": node, "format": "json"}
        if selection:
            parameters["fuel_type"] = selection
        response = client.get("/api/prices/search/export", params=parameters)
        assert response.status_code == 200
        records = response.json()
        assert records
        assert all(row["node_id"] == node for row in records)
        if selection:
            assert {row["fuel_type"] for row in records} <= set(selection.split(","))

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


class TestStationsLookup:
    def test_returns_200_with_known_node_ids(self, client, has_data):
        seed = client.get("/api/prices/search?fuel_type=E10&limit=2").json()
        node_ids = [r["node_id"] for r in seed["results"]]

        r = client.post("/api/stations/lookup", json={"node_ids": node_ids})
        assert r.status_code == 200

        data = r.json()
        assert data["requested"] == len(node_ids)
        assert len(data["results"]) == len(node_ids)
        assert data["found"] == len(node_ids)
        assert data["missing"] == []

    def test_has_postcode_enrichment_fields(self, client, has_data):
        seed = client.get("/api/prices/search?fuel_type=E10&limit=1").json()
        node_id = seed["results"][0]["node_id"]

        r = client.post("/api/stations/lookup", json={"node_ids": [node_id]})
        assert r.status_code == 200

        row = r.json()["results"][0]
        for field in (
            "node_id",
            "found",
            "trading_name",
            "postcode",
            "country",
            "region",
            "admin_district",
            "parliamentary_constituency",
            "rural_urban",
            "latitude",
            "longitude",
        ):
            assert field in row

    def test_includes_missing_node_ids(self, client, has_data):
        seed = client.get("/api/prices/search?fuel_type=E10&limit=1").json()
        real_id = seed["results"][0]["node_id"]
        missing_id = "__NOT_A_REAL_NODE_ID__"

        r = client.post("/api/stations/lookup", json={"node_ids": [real_id, missing_id]})
        assert r.status_code == 200

        data = r.json()
        assert data["requested"] == 2
        assert data["found"] == 1
        assert data["missing"] == [missing_id]
        assert data["results"][1]["found"] is False


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
    def test_subset_retains_individual_fuel_bounds(self, client, has_data):
        fuels = {"E10", "B7_STANDARD"}
        singles = {fuel: client.get("/api/outliers", params={"fuel_type": fuel}).json() for fuel in fuels}
        response = client.get("/api/outliers?fuel_type=E10,B7_STANDARD&limit=5&offset=2")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == sum(single["total"] for single in singles.values())
        assert set(data["bounds"]) == fuels
        for fuel in fuels:
            assert data["bounds"][fuel] == singles[fuel]["bounds"][fuel]
        assert {row["fuel_type"] for row in data["outliers"]} <= fuels

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
    def test_fuel_subset_records(self, client, has_data):
        node = client.get("/api/prices/search?fuel_type=E10&limit=1").json()["results"][0]["node_id"]
        response = client.get(f"/api/prices/station/{node}/records?fuel_type=E10,B7_STANDARD")
        assert response.status_code == 200
        records = response.json()["records"]
        assert records
        assert {row["fuel_type"] for row in records} <= {"E10", "B7_STANDARD"}

    def test_non_anomalous_iqr_outlier_is_shown_in_status(self, client, has_data):
        conn = psycopg2.connect(os.environ["DATABASE_URL"])
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    WITH bounds AS (
                        SELECT fuel_type,
                               PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY price) AS q1,
                               PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY price) AS q3
                        FROM current_prices
                        WHERE NOT temporary_closure
                          AND NOT price_is_outlier
                        GROUP BY fuel_type
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
        iqr_flags = [f for f in (row["effective_flags"] or []) if f.startswith("current_iqr_outlier")]
        assert len(iqr_flags) == 1
        assert "<" in iqr_flags[0] or ">" in iqr_flags[0]  # includes fence value


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

    def test_uncategorised_in_breakdown(self, client, has_data):
        """Unmapped brands should appear as 'Uncategorised', not 'Independent'."""
        data = client.get("/api/prices/by-category?fuel_type=E10").json()
        types = [d["forecourt_type"] for d in data]
        assert "Uncategorised" in types

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

    def test_uncategorised_type_accepted(self, client):
        """Uncategorised is a valid forecourt type for explicit assignment."""
        r = client.post("/api/admin/brand-categories", json={
            "canonical_brand": "__TEST_UNCAT__",
            "forecourt_type": "Uncategorised",
        })
        assert r.status_code == 200
        client.delete("/api/admin/brand-categories/__TEST_UNCAT__")


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

    def test_batch_invalid_stations_rejected(self, client):
        r = client.post("/api/admin/station-overrides/batch", json={
            "canonical_brand": "Test",
            "node_ids": ["NONEXISTENT_1", "NONEXISTENT_2"],
        })
        assert r.status_code == 404

    def test_batch_empty_node_ids_rejected(self, client):
        r = client.post("/api/admin/station-overrides/batch", json={
            "canonical_brand": "Test",
            "node_ids": [],
        })
        assert r.status_code == 400

    def test_batch_upsert_and_cleanup(self, client, has_data):
        """Batch override real stations, verify, then clean up."""
        conn = psycopg2.connect(os.environ["DATABASE_URL"])
        with conn.cursor() as cur:
            cur.execute("SELECT node_id FROM stations LIMIT 3")
            node_ids = [r[0] for r in cur.fetchall()]
        conn.close()
        if len(node_ids) < 2:
            pytest.skip("Need at least 2 stations for batch test")

        r = client.post("/api/admin/station-overrides/batch", json={
            "canonical_brand": "__BATCH_TEST__",
            "node_ids": node_ids,
            "notes": "integration test",
        })
        assert r.status_code == 200
        assert r.json()["saved"] == len(node_ids)
        assert r.json()["canonical_brand"] == "__BATCH_TEST__"

        # Clean up
        for nid in node_ids:
            client.delete(f"/api/admin/station-overrides/{nid}")


class TestAdminPostcodeOverrides:
    def test_list_returns_200(self, client):
        r = client.get("/api/admin/postcode-overrides")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_invalid_station_rejected(self, client):
        r = client.post("/api/admin/postcode-overrides", json={
            "node_id": "NONEXISTENT_NODE_ID_999",
            "corrected_postcode": "SW1A 1AA",
        })
        assert r.status_code == 404

    def test_upsert_and_delete(self, client, has_data):
        """Create a postcode override for a real station, verify, then delete."""
        conn = psycopg2.connect(os.environ["DATABASE_URL"])
        with conn.cursor() as cur:
            cur.execute("SELECT node_id, postcode FROM stations WHERE postcode IS NOT NULL LIMIT 1")
            row = cur.fetchone()
        conn.close()
        if not row:
            pytest.skip("No stations with postcodes")
        node_id, original = row

        r = client.post("/api/admin/postcode-overrides", json={
            "node_id": node_id,
            "corrected_postcode": "SW1A 1AA",
            "notes": "integration test",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["node_id"] == node_id
        assert data["corrected_postcode"] == "SW1A 1AA"
        assert data["lookup_status"] in ("enriched", "not_recognised", "lookup_failed")

        # Verify it appears in the list
        rows = client.get("/api/admin/postcode-overrides").json()
        assert any(o["node_id"] == node_id for o in rows)

        # Delete
        r = client.delete(f"/api/admin/postcode-overrides/{node_id}")
        assert r.status_code == 200
        assert r.json()["deleted"] == node_id

    def test_delete_nonexistent_returns_404(self, client):
        r = client.delete("/api/admin/postcode-overrides/NONEXISTENT_NODE_999")
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

    def test_uncategorised_filter(self, client, has_data):
        """Filtering by Uncategorised should return only unmapped brands."""
        data = client.get("/api/prices/search?fuel_type=E10&category=Uncategorised&limit=10").json()
        for row in data["results"]:
            assert row["forecourt_type"] == "Uncategorised"

    def test_multi_category_filter_with_uncategorised(self, client, has_data):
        data = client.get("/api/prices/search?fuel_type=E10&category=Supermarket,Uncategorised&limit=20").json()
        for row in data["results"]:
            assert row["forecourt_type"] in ("Supermarket", "Uncategorised")

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

    def test_hourly_granularity_for_long_range(self, client, has_data):
        """Station-level history always defaults to hourly regardless of range."""
        node_id = self._get_station_id(client)
        data = client.get(f"/api/prices/station/{node_id}/history?fuel_type=E10&days=60").json()
        assert data["granularity"] == "hourly"

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
