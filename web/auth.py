"""
Authentication for the Fuel Finder API.

Supports three modes (in priority order):

1. **Cognito JWT** – when ``COGNITO_USER_POOL_ID`` is set, requests must carry
   a valid Cognito ID token in the ``Authorization: Bearer <token>`` header.
   Admin endpoints require membership of the ``admin`` Cognito group.

2. **Shared API key** – when only ``API_KEY`` is set, requests must send the
   key in the ``X-Api-Key`` header.

3. **No auth** – when neither is configured, auth is disabled.
   This is the local-dev experience.

Public interface:
    - ``require_auth``       – authentication dependency (replaces the old stub)
    - ``require_admin``      – admin-only dependency for mutation endpoints
    - ``get_current_user``   – resolves the caller's identity
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

_USE_COGNITO = bool(COGNITO_USER_POOL_ID)

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

    - Cognito mode:  validates the Bearer JWT.
    - API-key mode:  checks ``X-Api-Key`` header.
    - No-auth mode:  passes through.
    """
    if _USE_COGNITO:
        _extract_claims(request)
        return

    if not API_KEY:
        return

    if x_api_key != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )


# ---------------------------------------------------------------------------
# Admin-only dependency (for mutation endpoints)
# ---------------------------------------------------------------------------


async def require_admin(
    request: Request,
    x_api_key: str = Header(default=""),
) -> None:
    """Require admin-level access for mutation endpoints.

    - Cognito mode:  user must be in the ``admin`` Cognito group.
    - API-key mode:  any valid key is treated as admin.
    - No-auth mode:  passes through.
    """
    if _USE_COGNITO:
        claims = _extract_claims(request)
        groups = set(claims.get("cognito:groups", []))
        if "admin" not in groups:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Requires admin group membership",
            )
        return

    # API-key or no-auth — delegate to require_auth
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


# ---------------------------------------------------------------------------
# Auth config endpoint (for frontend discovery)
# ---------------------------------------------------------------------------


def get_auth_config() -> dict:
    """Return auth configuration for the frontend."""
    if _USE_COGNITO:
        return {
            "mode": "cognito",
            "region": COGNITO_REGION,
            "userPoolId": COGNITO_USER_POOL_ID,
            "clientId": COGNITO_CLIENT_ID,
        }
    if API_KEY:
        return {"mode": "api_key"}
    return {"mode": "none"}
