"""Real token verification, exercised through `anon_client` (no auth override).

HS256 goes through the legacy shared-secret path; ES256 goes through the JWKS
path with the key client faked out (no network in tests).
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from httpx import AsyncClient

from app.core import auth
from app.core.config import Settings

JWT_SECRET = "test-jwt-secret-0123456789abcdef"  # ≥32 bytes, keeps PyJWT quiet
SUPABASE_URL = "https://test-ref.supabase.co"

ME_URL = "/api/v1/users/me"


@pytest.fixture
def auth_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        auth,
        "get_settings",
        lambda: Settings(supabase_url=SUPABASE_URL, supabase_jwt_secret=JWT_SECRET),
    )


def _claims(**overrides) -> dict:
    return {
        "sub": "8f1c2b34-0000-4000-8000-000000000000",
        "aud": "authenticated",
        "exp": datetime.now(UTC) + timedelta(hours=1),
        "email": "lemon@example.com",
        "app_metadata": {"provider": "google"},
        "user_metadata": {"full_name": "Lemon"},
        **overrides,
    }


def _hs256_token(**overrides) -> str:
    return jwt.encode(_claims(**overrides), JWT_SECRET, algorithm="HS256")


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_requests_without_token_are_rejected(anon_client: AsyncClient) -> None:
    for url in (ME_URL, "/api/v1/tags", "/api/v1/events"):
        response = await anon_client.get(url)
        assert response.status_code in (401, 403), url


async def test_valid_token_authenticates_and_provisions(
    anon_client: AsyncClient, auth_settings: None
) -> None:
    response = await anon_client.get(ME_URL, headers=_bearer(_hs256_token()))
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["provider"] == "google"
    assert body["provider_subject"] == "8f1c2b34-0000-4000-8000-000000000000"
    assert body["email"] == "lemon@example.com"
    assert body["display_name"] == "Lemon"

    # Same identity on the next request resolves to the same account.
    again = await anon_client.get(ME_URL, headers=_bearer(_hs256_token()))
    assert again.json()["id"] == body["id"]


async def test_expired_token_is_rejected(anon_client: AsyncClient, auth_settings: None) -> None:
    token = _hs256_token(exp=datetime.now(UTC) - timedelta(minutes=1))
    response = await anon_client.get(ME_URL, headers=_bearer(token))
    assert response.status_code == 401


async def test_wrong_audience_is_rejected(anon_client: AsyncClient, auth_settings: None) -> None:
    response = await anon_client.get(ME_URL, headers=_bearer(_hs256_token(aud="anon")))
    assert response.status_code == 401


async def test_token_signed_with_wrong_secret_is_rejected(
    anon_client: AsyncClient, auth_settings: None
) -> None:
    token = jwt.encode(_claims(), "some-other-secret-0123456789abcd", algorithm="HS256")
    response = await anon_client.get(ME_URL, headers=_bearer(token))
    assert response.status_code == 401


async def test_es256_token_verifies_against_jwks(
    anon_client: AsyncClient, auth_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    private_key = ec.generate_private_key(ec.SECP256R1())
    fake_jwks = SimpleNamespace(
        get_signing_key_from_jwt=lambda token: SimpleNamespace(key=private_key.public_key())
    )
    monkeypatch.setattr(auth, "_jwks_client", lambda supabase_url: fake_jwks)

    token = jwt.encode(_claims(), private_key, algorithm="ES256")
    response = await anon_client.get(ME_URL, headers=_bearer(token))
    assert response.status_code == 200, response.text
