"""Unit tests for Guardian Google federation policy."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))

import auth


class TestFederatedGoogleDomain:
    @pytest.fixture(autouse=True)
    def configure_google_provider(self, monkeypatch):
        monkeypatch.setattr(auth, "COGNITO_OIDC_PROVIDER", "GuardianGoogle")
        monkeypatch.setattr(auth, "ALLOWED_GOOGLE_DOMAIN", "guardian.co.uk")

    @staticmethod
    def _claims(**overrides):
        claims = {
            "cognito:username": "GuardianGoogle_123",
            "email": "reporter@guardian.co.uk",
            "email_verified": True,
            "custom:google_hd": "guardian.co.uk",
            "identities": [{"providerName": "GuardianGoogle"}],
        }
        claims.update(overrides)
        return claims

    def test_accepts_verified_guardian_google_identity(self):
        auth._validate_federated_domain(self._claims())

    def test_accepts_linked_identity_with_serialised_identities(self):
        auth._validate_federated_domain(self._claims(
            **{
                "cognito:username": "local-cognito-uuid",
                "identities": '[{"providerName":"GuardianGoogle"}]',
            }
        ))

    @pytest.mark.parametrize(
        "claim_overrides",
        [
            {"custom:google_hd": "gmail.com"},
            {"email": "reporter@gmail.com"},
            {"email_verified": False},
            {"custom:google_hd": ""},
        ],
    )
    def test_rejects_non_guardian_or_unverified_google_identity(self, claim_overrides):
        with pytest.raises(auth.HTTPException) as exc:
            auth._validate_federated_domain(self._claims(**claim_overrides))
        assert exc.value.status_code == 403

    def test_native_cognito_password_user_is_unchanged(self):
        auth._validate_federated_domain({
            "cognito:username": "local-cognito-uuid",
            "email": "existing@example.com",
            "email_verified": True,
        })

    def test_auth_config_exposes_public_oauth_settings(self, monkeypatch):
        monkeypatch.setattr(auth, "_USE_COGNITO", True)
        monkeypatch.setattr(auth, "COGNITO_DOMAIN", "https://auth.example.com")
        config = auth.get_auth_config()
        assert config["oauth"] == {
            "domain": "https://auth.example.com",
            "provider": "GuardianGoogle",
            "allowedDomain": "guardian.co.uk",
        }
