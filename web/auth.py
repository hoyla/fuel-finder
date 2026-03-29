"""
Authentication for the Fuel Finder API.

Supports multiple authentication methods (checked in order):

1. **API key** – when ``API_KEY`` is set, requests with a valid
   ``X-Api-Key`` header are accepted.  Works alongside Cognito.
   API key holders are treated as admin for mutation endpoints.

2. **Cognito JWT** – when ``COGNITO_USER_POOL_ID`` is set, requests must
   carry a valid Cognito ID token in ``Authorization: Bearer <token>``.
   Admin endpoints require membership of the ``admin`` Cognito group.

3. **No auth** – when neither is configured, auth is disabled.
   This is the local-dev experience.

Public interface:
    - ``require_auth``       – authentication dependency (replaces the old stub)
    - ``require_editor``     – editor-or-admin dependency for mutation endpoints
    - ``require_admin``      – admin-only dependency (user management)
    - ``get_current_user``   – resolves the caller's identity
    - ``get_user_role``      – returns 'admin', 'editor', or 'readonly'
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from functools import lru_cache

from fastapi import Header, HTTPException, Request, status

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (from environment)
# ---------------------------------------------------------------------------

COGNITO_USER_POOL_ID = os.environ.get("COGNITO_USER_POOL_ID", "")
COGNITO_CLIENT_ID = os.environ.get("COGNITO_CLIENT_ID", "")
COGNITO_REGION = os.environ.get("COGNITO_REGION", os.environ.get("AWS_REGION", "eu-west-1"))
API_KEY = os.environ.get("API_KEY", "")
ENVIRONMENT = os.environ.get("ENVIRONMENT", "local").strip().lower()
ALLOW_NO_AUTH = os.environ.get("ALLOW_NO_AUTH", "").strip().lower() in {"1", "true", "yes", "on"}

_USE_COGNITO = bool(COGNITO_USER_POOL_ID)


def _is_no_auth_mode() -> bool:
    """True when neither Cognito nor API key authentication is configured."""
    return not _USE_COGNITO and not API_KEY


def _is_no_auth_allowed() -> bool:
    """Allow no-auth for local dev, or by explicit operator override."""
    return ENVIRONMENT == "local" or ALLOW_NO_AUTH


if _is_no_auth_mode() and not _is_no_auth_allowed():
    raise RuntimeError(
        "Authentication is not configured for this environment. "
        "Set Cognito or API_KEY, or explicitly set ALLOW_NO_AUTH=true for controlled exceptions."
    )

# ---------------------------------------------------------------------------
# Cognito JWKS (cached once per process)
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _get_jwks() -> dict:
    """Fetch and cache the Cognito JWKS (JSON Web Key Set)."""
    url = (
        f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/"
        f"{COGNITO_USER_POOL_ID}/.well-known/jwks.json"
    )
    with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310
        return json.loads(resp.read())


def _decode_cognito_token(token: str) -> dict:
    """Validate and decode a Cognito ID token.

    Returns the full claims dict on success; raises HTTPException on failure.
    """
    import jwt as pyjwt
    from jwt.exceptions import InvalidTokenError as JWTError

    try:
        jwks = _get_jwks()
    except Exception as exc:
        logger.error("Failed to fetch Cognito JWKS: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service unavailable",
        )

    try:
        unverified_header = pyjwt.get_unverified_header(token)
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    kid = unverified_header.get("kid")
    jwk_dict = _find_jwk(jwks, kid)

    if jwk_dict is None:
        # Force JWKS refresh in case keys were rotated
        _get_jwks.cache_clear()
        try:
            jwks = _get_jwks()
        except Exception:
            pass
        jwk_dict = _find_jwk(jwks, kid)

    if jwk_dict is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token signing key not found",
        )

    key = pyjwt.algorithms.RSAAlgorithm.from_jwk(jwk_dict)
    issuer = (
        f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}"
    )

    try:
        claims = pyjwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=COGNITO_CLIENT_ID,
            issuer=issuer,
        )
    except JWTError as exc:
        logger.warning("JWT validation failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    return claims


def _find_jwk(jwks: dict, kid: str):
    """Find a JWK by key ID."""
    for k in jwks.get("keys", []):
        if k["kid"] == kid:
            return k
    return None


# ---------------------------------------------------------------------------
# Extract claims from request (cached per-request)
# ---------------------------------------------------------------------------


def _extract_claims(request: Request) -> dict | None:
    """Return decoded JWT claims if Cognito mode is active, else None."""
    if not _USE_COGNITO:
        return None

    cached = getattr(request.state, "cognito_claims", None)
    if cached is not None:
        return cached

    auth_header: str = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = auth_header[7:]
    claims = _decode_cognito_token(token)
    request.state.cognito_claims = claims
    return claims


# ---------------------------------------------------------------------------
# Authentication dependency
# ---------------------------------------------------------------------------


async def require_auth(
    request: Request,
    x_api_key: str = Header(default=""),
) -> None:
    """Authenticate the request.

    - Cognito JWT in ``Authorization: Bearer <token>`` header.
    - API key in ``X-Api-Key`` header (works alongside Cognito).
    - No-auth mode:  local/dev only (or explicit ALLOW_NO_AUTH override).
    """
    # Try API key first (works in all modes when API_KEY is set)
    if API_KEY and x_api_key == API_KEY:
        request.state.user_email = "api_key"
        return

    if _USE_COGNITO:
        claims = _extract_claims(request)
        request.state.user_email = claims.get("email") or claims.get("cognito:username") or "unknown"
        return

    if _is_no_auth_mode():
        if _is_no_auth_allowed():
            request.state.user_email = "local-dev"
            return
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured for this environment",
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing credentials",
        headers={"WWW-Authenticate": "Bearer, ApiKey"},
    )


# ---------------------------------------------------------------------------
# Admin-only dependency (for mutation endpoints)
# ---------------------------------------------------------------------------


async def require_editor(
    request: Request,
    x_api_key: str = Header(default=""),
) -> None:
    """Require editor-level access for data mutation endpoints.

    - API key:       any valid key is treated as editor+.
    - Cognito mode:  user must be in the ``admin`` or ``editor`` group.
    - No-auth mode:  passes through.
    """
    if API_KEY and x_api_key == API_KEY:
        return

    if _USE_COGNITO:
        claims = _extract_claims(request)
        groups = set(claims.get("cognito:groups", []))
        if not groups.intersection({"admin", "editor"}):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Requires editor or admin group membership",
            )
        return

    await require_auth(request, x_api_key)


async def require_admin(
    request: Request,
    x_api_key: str = Header(default=""),
) -> None:
    """Require admin-level access for mutation endpoints.

    - API key:       any valid key is treated as admin.
    - Cognito mode:  user must be in the ``admin`` Cognito group.
    - No-auth mode:  passes through.
    """
    # API key holders get admin access
    if API_KEY and x_api_key == API_KEY:
        return

    if _USE_COGNITO:
        claims = _extract_claims(request)
        groups = set(claims.get("cognito:groups", []))
        if "admin" not in groups:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Requires admin group membership",
            )
        return

    # No-auth — delegate to require_auth
    await require_auth(request, x_api_key)


# ---------------------------------------------------------------------------
# User identity
# ---------------------------------------------------------------------------


def get_current_user(request: Request) -> str:
    """Return the email (or username) of the current user."""
    if _USE_COGNITO:
        claims = _extract_claims(request)
        return claims.get("email") or claims.get("cognito:username", "unknown")
    return "anonymous"


def get_user_role(request: Request, x_api_key: str = "") -> str:
    """Return the effective role: 'admin', 'editor', or 'readonly'.

    - API key holders → admin.
    - Cognito users → highest group ('admin' > 'editor' > 'readonly').
    - No-auth mode → admin (local dev).
    """
    if API_KEY and x_api_key == API_KEY:
        return "admin"
    if _USE_COGNITO:
        claims = _extract_claims(request)
        groups = set(claims.get("cognito:groups", []))
        if "admin" in groups:
            return "admin"
        if "editor" in groups:
            return "editor"
        return "readonly"
    return "admin"


async def resolve_role(
    request: Request,
    x_api_key: str = Header(default=""),
) -> str:
    """Authenticate and return the user's role.

    Combines ``require_auth`` (gate) with ``get_user_role`` (role lookup)
    so endpoints can branch on the caller's tier.

    Admins may send ``X-Role-Override: editor|readonly`` to preview
    a lower tier's experience without changing accounts.
    """
    await require_auth(request, x_api_key)
    real_role = get_user_role(request, x_api_key)
    override = request.headers.get("x-role-override", "").lower()
    if override in ("editor", "readonly") and real_role == "admin":
        return override
    return real_role


# ---------------------------------------------------------------------------
# Auth config endpoint (for frontend discovery)
# ---------------------------------------------------------------------------


def get_auth_config() -> dict:
    """Return auth configuration for the frontend."""
    env = os.environ.get("ENVIRONMENT", "local")
    if _USE_COGNITO:
        return {
            "mode": "cognito",
            "region": COGNITO_REGION,
            "userPoolId": COGNITO_USER_POOL_ID,
            "clientId": COGNITO_CLIENT_ID,
            "environment": env,
        }
    if API_KEY:
        return {"mode": "api_key", "environment": env}
    return {"mode": "none", "environment": env}
