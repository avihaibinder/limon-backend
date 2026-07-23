"""Supabase Auth Admin client, exercised with an httpx MockTransport (no network).

Settings are overridden the same way as test_transcriber.py: patch the module's
``get_settings`` to return a configured ``Settings``.
"""

import httpx
import pytest

from app.core.config import Settings
from app.services import supabase_admin
from app.services.supabase_admin import SupabaseAdminError, SupabaseAdminNotConfiguredError

SUPABASE_URL = "https://test-ref.supabase.co"
SERVICE_KEY = "service-role-secret"
SUB = "8f1c2b34-0000-4000-8000-000000000000"


def _configure(monkeypatch: pytest.MonkeyPatch, **overrides) -> None:
    monkeypatch.setattr(
        supabase_admin,
        "get_settings",
        lambda: Settings(
            supabase_url=SUPABASE_URL, supabase_service_role_key=SERVICE_KEY, **overrides
        ),
    )


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_deletes_the_auth_user_with_service_role_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["apikey"] = request.headers.get("apikey")
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200)

    async with _client(handler) as client:
        await supabase_admin.delete_auth_user(SUB, client=client)

    assert seen["method"] == "DELETE"
    assert seen["url"] == f"{SUPABASE_URL}/auth/v1/admin/users/{SUB}"
    assert seen["apikey"] == SERVICE_KEY
    assert seen["auth"] == f"Bearer {SERVICE_KEY}"


async def test_missing_auth_user_is_idempotent_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)

    async with _client(lambda request: httpx.Response(404)) as client:
        # A retried delete-account must not fail because the identity is already gone.
        await supabase_admin.delete_auth_user(SUB, client=client)


async def test_unexpected_status_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)

    with pytest.raises(SupabaseAdminError):
        async with _client(lambda request: httpx.Response(500)) as client:
            await supabase_admin.delete_auth_user(SUB, client=client)


async def test_transport_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(SupabaseAdminError):
        async with _client(handler) as client:
            await supabase_admin.delete_auth_user(SUB, client=client)


async def test_no_op_when_supabase_url_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # Local dev / tests: no real Supabase, so the call must do nothing (and never
    # touch the injected client) rather than fail.
    monkeypatch.setattr(supabase_admin, "get_settings", lambda: Settings())

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        raise AssertionError("no HTTP call should be made when Supabase is unconfigured")

    async with _client(handler) as client:
        await supabase_admin.delete_auth_user(SUB, client=client)


async def test_configured_url_without_service_key_is_a_misconfiguration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(supabase_admin, "get_settings", lambda: Settings(supabase_url=SUPABASE_URL))

    with pytest.raises(SupabaseAdminNotConfiguredError):
        await supabase_admin.delete_auth_user(SUB)
