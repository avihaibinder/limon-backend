"""Tests for audio-upload presigning.

There is no GCP account in CI, so the GCS SDK boundary is mocked: the service's
own logic (config-gating, content-type allowlist, object-key layout) runs for
real, and only the network-touching pieces (ADC resolution + the signed-URL
call) are faked.
"""

import datetime as dt

import pytest
from httpx import AsyncClient

from app.core.config import get_settings
from app.services import storage as storage_service

PRESIGN_URL = "/api/v1/uploads/audio/presign"


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    """get_settings() is lru_cached; clear it so per-test env changes apply."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def _fake_gcs(monkeypatch):
    """Stub the GCS/ADC boundary so signing needs no real credentials.

    Records the object key the service asked GCS to sign, so tests can assert
    on the key layout the client will actually receive.
    """
    captured: dict = {}

    class _FakeBlob:
        def __init__(self, key: str) -> None:
            captured["object_key"] = key

        def generate_signed_url(self, **kwargs):
            captured["kwargs"] = kwargs
            return f"https://storage.googleapis.com/signed/{captured['object_key']}?sig=fake"

    class _FakeBucket:
        def blob(self, key: str) -> _FakeBlob:
            return _FakeBlob(key)

    class _FakeClient:
        def bucket(self, name: str) -> _FakeBucket:
            captured["bucket"] = name
            return _FakeBucket()

    monkeypatch.setattr(storage_service, "_client", lambda: _FakeClient())
    # Skip real ADC/impersonation resolution; the fake blob ignores signing args.
    monkeypatch.setattr(storage_service, "_signed_url_signing_kwargs", dict)
    return captured


async def test_presign_returns_signed_url_and_key(
    client: AsyncClient, monkeypatch, _fake_gcs
) -> None:
    monkeypatch.setenv("LIMON_GCS_BUCKET", "limon-dev-bucket")

    response = await client.post(PRESIGN_URL, json={"content_type": "audio/mp4"})

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["upload_url"].startswith("https://storage.googleapis.com/signed/")
    assert body["content_type"] == "audio/mp4"
    # Key is namespaced by user and ends with the audio extension.
    assert body["object_key"].startswith("audio/")
    assert body["object_key"].endswith(".m4a")
    assert _fake_gcs["bucket"] == "limon-dev-bucket"
    # The URL was signed as a PUT for the requested content type.
    assert _fake_gcs["kwargs"]["method"] == "PUT"
    assert _fake_gcs["kwargs"]["content_type"] == "audio/mp4"


async def test_presign_key_is_scoped_to_the_authenticated_user(
    client: AsyncClient, monkeypatch, _fake_gcs
) -> None:
    monkeypatch.setenv("LIMON_GCS_BUCKET", "limon-dev-bucket")
    me = (await client.get("/api/v1/users/me")).json()

    await client.post(PRESIGN_URL, json={"content_type": "audio/mp4"})

    assert _fake_gcs["object_key"].startswith(f"audio/{me['id']}/")


async def test_presign_rejects_non_audio_content_type(
    client: AsyncClient, monkeypatch, _fake_gcs
) -> None:
    monkeypatch.setenv("LIMON_GCS_BUCKET", "limon-dev-bucket")

    response = await client.post(PRESIGN_URL, json={"content_type": "application/pdf"})

    assert response.status_code == 400, response.text


async def test_presign_503_when_bucket_not_configured(
    client: AsyncClient, monkeypatch, _fake_gcs
) -> None:
    # Force an unconfigured bucket regardless of any local .env by overriding
    # the settings object the service reads.
    settings = get_settings()
    monkeypatch.setattr(settings, "gcs_bucket", None)

    response = await client.post(PRESIGN_URL, json={"content_type": "audio/mp4"})

    assert response.status_code == 503, response.text


async def test_presign_requires_authentication(anon_client: AsyncClient, monkeypatch) -> None:
    monkeypatch.setenv("LIMON_GCS_BUCKET", "limon-dev-bucket")

    response = await anon_client.post(PRESIGN_URL, json={"content_type": "audio/mp4"})

    assert response.status_code == 401, response.text


def test_expiry_reflects_configured_ttl(monkeypatch, _fake_gcs) -> None:
    monkeypatch.setenv("LIMON_GCS_BUCKET", "limon-dev-bucket")
    monkeypatch.setenv("LIMON_GCS_SIGNED_URL_TTL_SECONDS", "300")

    before = dt.datetime.now(dt.UTC)
    result = storage_service.presign_audio_upload(user_id="u1", content_type="audio/mp4")

    delta = result.expires_at - before
    assert dt.timedelta(seconds=290) <= delta <= dt.timedelta(seconds=310)
