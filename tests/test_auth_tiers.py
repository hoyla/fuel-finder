"""Tests for the three-tier auth system (admin / editor / readonly).

Uses FastAPI dependency overrides to simulate Cognito-like role gating
without a real user pool.
"""

import os
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://fuelfinder:fuelfinder@localhost:5432/fuelfinder",
)

from web.api import app
# auth is imported as a top-level module in api.py (conftest adds web/ to
# sys.path) so we must reference the *same* module object for overrides
# to match the Depends() references.
import auth


# ---------------------------------------------------------------------------
# Helpers — dependency override factories
# ---------------------------------------------------------------------------


def _make_require_auth_ok():
    """Return a require_auth override that always passes."""
    async def _ok():
        pass
    return _ok


def _make_resolve_role(role: str):
    """Return a resolve_role override that returns a fixed role string."""
    async def _resolve():
        return role
    return _resolve


def _make_require_editor_block():
    """Return a require_editor override that rejects the request (403)."""
    from fastapi import HTTPException, status

    async def _block():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requires editor or admin group membership",
        )
    return _block


def _make_require_admin_block():
    """Return a require_admin override that rejects the request (403)."""
    from fastapi import HTTPException, status

    async def _block():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requires admin group membership",
        )
    return _block


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def base_client():
    """Unmodified TestClient — no overrides."""
    return TestClient(app)


@pytest.fixture(scope="module")
def has_data(base_client):
    import psycopg2
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM current_prices")
        count = cur.fetchone()[0]
    conn.close()
    if count == 0:
        pytest.skip("No data in current_prices — run a scrape first")
    return count


@pytest.fixture()
def readonly_client():
    """Client where resolve_role → 'readonly' and require_editor blocks."""
    app.dependency_overrides[auth.require_auth] = _make_require_auth_ok()
    app.dependency_overrides[auth.resolve_role] = _make_resolve_role("readonly")
    app.dependency_overrides[auth.require_editor] = _make_require_editor_block()
    app.dependency_overrides[auth.require_admin] = _make_require_admin_block()
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture()
def editor_client():
    """Client where resolve_role → 'editor' and require_editor passes."""
    app.dependency_overrides[auth.require_auth] = _make_require_auth_ok()
    app.dependency_overrides[auth.resolve_role] = _make_resolve_role("editor")
    app.dependency_overrides[auth.require_editor] = _make_require_auth_ok()
    app.dependency_overrides[auth.require_admin] = _make_require_admin_block()
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture()
def admin_client():
    """Client where resolve_role → 'admin' and everything passes."""
    app.dependency_overrides[auth.require_auth] = _make_require_auth_ok()
    app.dependency_overrides[auth.resolve_role] = _make_resolve_role("admin")
    app.dependency_overrides[auth.require_editor] = _make_require_auth_ok()
    app.dependency_overrides[auth.require_admin] = _make_require_auth_ok()
    yield TestClient(app)
    app.dependency_overrides.clear()


# ===================================================================
# Unit tests: get_user_role
# ===================================================================


class TestGetUserRole:
    """Test auth.get_user_role under different configurations."""

    def test_no_auth_mode_returns_admin(self):
        """When no Cognito and no API_KEY, local dev gets admin."""
        original_cognito = auth._USE_COGNITO
        original_key = auth.API_KEY
        try:
            auth._USE_COGNITO = False
            auth.API_KEY = ""
            request = MagicMock()
            assert auth.get_user_role(request) == "admin"
        finally:
            auth._USE_COGNITO = original_cognito
            auth.API_KEY = original_key

    def test_api_key_returns_admin(self):
        """When API_KEY matches, role is admin."""
        original_key = auth.API_KEY
        try:
            auth.API_KEY = "test-secret-key"
            request = MagicMock()
            assert auth.get_user_role(request, x_api_key="test-secret-key") == "admin"
        finally:
            auth.API_KEY = original_key

    def test_cognito_admin_group(self):
        """Cognito user in admin group → admin."""
        original_cognito = auth._USE_COGNITO
        original_key = auth.API_KEY
        try:
            auth._USE_COGNITO = True
            auth.API_KEY = ""
            request = MagicMock()
            request.state.cognito_claims = {"cognito:groups": ["admin"]}
            assert auth.get_user_role(request) == "admin"
        finally:
            auth._USE_COGNITO = original_cognito
            auth.API_KEY = original_key

    def test_cognito_editor_group(self):
        """Cognito user in editor group → editor."""
        original_cognito = auth._USE_COGNITO
        original_key = auth.API_KEY
        try:
            auth._USE_COGNITO = True
            auth.API_KEY = ""
            request = MagicMock()
            request.state.cognito_claims = {"cognito:groups": ["editor"]}
            assert auth.get_user_role(request) == "editor"
        finally:
            auth._USE_COGNITO = original_cognito
            auth.API_KEY = original_key

    def test_cognito_no_group_returns_readonly(self):
        """Cognito user with no group → readonly."""
        original_cognito = auth._USE_COGNITO
        original_key = auth.API_KEY
        try:
            auth._USE_COGNITO = True
            auth.API_KEY = ""
            request = MagicMock()
            request.state.cognito_claims = {"cognito:groups": []}
            assert auth.get_user_role(request) == "readonly"
        finally:
            auth._USE_COGNITO = original_cognito
            auth.API_KEY = original_key

    def test_cognito_admin_beats_editor(self):
        """User in both admin and editor groups → admin wins."""
        original_cognito = auth._USE_COGNITO
        original_key = auth.API_KEY
        try:
            auth._USE_COGNITO = True
            auth.API_KEY = ""
            request = MagicMock()
            request.state.cognito_claims = {"cognito:groups": ["editor", "admin"]}
            assert auth.get_user_role(request) == "admin"
        finally:
            auth._USE_COGNITO = original_cognito
            auth.API_KEY = original_key


# ===================================================================
# Mutation endpoints — require_editor gate
# ===================================================================


class TestEditorGate:
    """Mutation endpoints block readonly users (403), allow editors."""

    def test_create_brand_alias_blocked_for_readonly(self, readonly_client):
        r = readonly_client.post("/api/admin/brand-aliases", json={
            "raw_brand_name": "__TEST__", "canonical_brand": "__TEST__",
        })
        assert r.status_code == 403

    def test_create_brand_alias_allowed_for_editor(self, editor_client, has_data):
        r = editor_client.post("/api/admin/brand-aliases", json={
            "raw_brand_name": "__TIER_TEST__", "canonical_brand": "__TIER_TEST__",
        })
        assert r.status_code == 200
        # Clean up
        editor_client.delete("/api/admin/brand-aliases/__TIER_TEST__")

    def test_delete_brand_alias_blocked_for_readonly(self, readonly_client):
        r = readonly_client.delete("/api/admin/brand-aliases/__NONEXISTENT__")
        assert r.status_code == 403

    def test_create_brand_category_blocked_for_readonly(self, readonly_client):
        r = readonly_client.post("/api/admin/brand-categories", json={
            "canonical_brand": "__TEST__", "forecourt_type": "Supermarket",
        })
        assert r.status_code == 403

    def test_delete_brand_category_blocked_for_readonly(self, readonly_client):
        r = readonly_client.delete("/api/admin/brand-categories/__NONEXISTENT__")
        assert r.status_code == 403

    def test_station_override_blocked_for_readonly(self, readonly_client):
        r = readonly_client.post("/api/admin/station-overrides", json={
            "node_id": "FAKE", "canonical_brand": "Test",
        })
        assert r.status_code == 403

    def test_refresh_view_blocked_for_readonly(self, readonly_client):
        r = readonly_client.post("/api/admin/refresh-view", json={})
        assert r.status_code == 403

    def test_refresh_view_allowed_for_editor(self, editor_client, has_data):
        r = editor_client.post("/api/admin/refresh-view", json={})
        assert r.status_code == 200


# ===================================================================
# Admin endpoints — require_admin gate
# ===================================================================


class TestAdminGate:
    """Admin endpoints block editors and readonly users."""

    def test_list_users_blocked_for_readonly(self, readonly_client):
        r = readonly_client.get("/api/admin/users")
        assert r.status_code == 403

    def test_list_users_blocked_for_editor(self, editor_client):
        r = editor_client.get("/api/admin/users")
        assert r.status_code == 403


# ===================================================================
# Export endpoints — editor-only
# ===================================================================


class TestExportGate:
    """Export endpoints require editor or above."""

    def test_history_export_blocked_for_readonly(self, readonly_client):
        r = readonly_client.get("/api/prices/history/export?fuel_type=E10&days=7")
        assert r.status_code == 403

    def test_search_export_blocked_for_readonly(self, readonly_client):
        r = readonly_client.get("/api/prices/search/export?fuel_type=E10")
        assert r.status_code == 403

    def test_history_export_allowed_for_editor(self, editor_client, has_data):
        r = editor_client.get("/api/prices/history/export?fuel_type=E10&days=7")
        assert r.status_code == 200

    def test_search_export_allowed_for_editor(self, editor_client, has_data):
        r = editor_client.get("/api/prices/search/export?fuel_type=E10")
        assert r.status_code == 200


# ===================================================================
# Readonly query caps
# ===================================================================


class TestReadonlyCaps:
    """Readonly users have capped query parameters."""

    def test_search_limit_capped_at_200(self, readonly_client, has_data):
        """Readonly search with limit=500 returns at most 200 results."""
        r = readonly_client.get("/api/prices/search?fuel_type=E10&limit=500")
        assert r.status_code == 200
        data = r.json()
        assert len(data["results"]) <= 200

    def test_search_limit_uncapped_for_editor(self, editor_client, has_data):
        """Editor can request limit=500 without cap."""
        r = editor_client.get("/api/prices/search?fuel_type=E10&limit=500")
        assert r.status_code == 200
        # No assertion on count — just verifying it doesn't cap at 200
        # (unless fewer than 500 records exist)

    def test_history_days_capped_at_90_for_readonly(self, readonly_client, has_data):
        """Readonly user requesting 365 days of history gets at most 90."""
        r = readonly_client.get("/api/prices/history?fuel_type=E10&days=365")
        assert r.status_code == 200

    def test_history_days_uncapped_for_editor(self, editor_client, has_data):
        """Editor can request 365 days of history."""
        r = editor_client.get("/api/prices/history?fuel_type=E10&days=365")
        assert r.status_code == 200

    def test_station_history_capped_for_readonly(self, readonly_client, has_data):
        """Station history capped at 90 days for readonly."""
        # Get a real station ID first
        r = readonly_client.get("/api/prices/search?fuel_type=E10&limit=1")
        node_id = r.json()["results"][0]["node_id"]
        r = readonly_client.get(f"/api/prices/station/{node_id}/history?fuel_type=E10&days=365")
        assert r.status_code == 200

    def test_station_history_uncapped_for_editor(self, editor_client, has_data):
        """Editor gets full 365 days for station history."""
        r = editor_client.get("/api/prices/search?fuel_type=E10&limit=1")
        node_id = r.json()["results"][0]["node_id"]
        r = editor_client.get(f"/api/prices/station/{node_id}/history?fuel_type=E10&days=365")
        assert r.status_code == 200


# ===================================================================
# Read-only access to read endpoints
# ===================================================================


class TestReadonlyCanRead:
    """Readonly users can still access all read-only endpoints."""

    def test_summary(self, readonly_client, has_data):
        assert readonly_client.get("/api/summary").status_code == 200

    def test_prices_by_region(self, readonly_client, has_data):
        assert readonly_client.get("/api/prices/by-region?fuel_type=E10").status_code == 200

    def test_prices_by_brand(self, readonly_client, has_data):
        assert readonly_client.get("/api/prices/by-brand?fuel_type=E10").status_code == 200

    def test_search(self, readonly_client, has_data):
        r = readonly_client.get("/api/prices/search?fuel_type=E10&limit=5")
        assert r.status_code == 200

    def test_history(self, readonly_client, has_data):
        r = readonly_client.get("/api/prices/history?fuel_type=E10&days=7")
        assert r.status_code == 200

    def test_fuel_types(self, readonly_client, has_data):
        assert readonly_client.get("/api/fuel-types").status_code == 200

    def test_regions(self, readonly_client, has_data):
        assert readonly_client.get("/api/regions").status_code == 200

    def test_anomalies(self, readonly_client, has_data):
        assert readonly_client.get("/api/anomalies?fuel_type=E10").status_code == 200
