"""Supabase Auth Admin API client (server-side, service-role only).

Delete-account has two halves: cascading our ``public.*`` rows (handled by the
``ON DELETE CASCADE`` FKs when we delete the ``users`` row) and removing the
Supabase ``auth.users`` identity, which lives outside our schema and can only be
touched with the project's service-role key. This module owns that second half.

The service-role key is a full-access secret: it is read from settings, used only
here, and never returned to a client. When ``supabase_url`` is unset (local dev /
tests, where there is no real Supabase) the admin call is a no-op, so account
deletion still works locally without a project or key.
"""

import httpx

from app.core.config import get_settings


class SupabaseAdminError(RuntimeError):
    """Base class for Supabase Auth Admin API failures."""


class SupabaseAdminNotConfiguredError(SupabaseAdminError):
    """``supabase_url`` is set but the service-role key is missing: a real
    deployment cannot complete delete-account, so this is a misconfiguration."""


async def delete_auth_user(sub: str, *, client: httpx.AsyncClient | None = None) -> None:
    """Delete the Supabase ``auth.users`` record for ``sub`` (the JWT subject).

    No-op when Supabase is not configured (``supabase_url`` unset), so local dev
    and tests need no project. A missing user (404) is treated as success so the
    call is idempotent (a retried delete-account must not fail). Any other non-2xx
    or a transport error raises ``SupabaseAdminError`` so the caller can abort
    before removing local data.

    Pass ``client`` to inject an ``httpx.AsyncClient`` (used by tests); otherwise a
    short-lived one is created.
    """
    settings = get_settings()
    if settings.supabase_url is None:
        # No real Supabase in this environment (local dev / tests): nothing to delete.
        return
    if not settings.supabase_service_role_key:
        raise SupabaseAdminNotConfiguredError(
            "Cannot delete the Supabase auth user: set LIMON_SUPABASE_SERVICE_ROLE_KEY."
        )

    url = f"{settings.supabase_url}/auth/v1/admin/users/{sub}"
    key = settings.supabase_service_role_key
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}

    if client is not None:
        await _send(client, url, headers)
        return
    async with httpx.AsyncClient(timeout=10.0) as owned:
        await _send(owned, url, headers)


async def _send(client: httpx.AsyncClient, url: str, headers: dict[str, str]) -> None:
    try:
        response = await client.delete(url, headers=headers)
    except httpx.TransportError as exc:
        # Do not abort local deletion on a transient network error blindly; surface
        # it so the caller can decide (we abort so the delete stays retryable).
        raise SupabaseAdminError(
            f"Supabase Auth Admin API unreachable: {type(exc).__name__}"
        ) from exc

    # 200/204 = deleted; 404 = already gone (idempotent success).
    if response.status_code in (200, 204, 404):
        return
    # Do not echo the body: it may carry the service-role context / internals.
    raise SupabaseAdminError(f"Supabase Auth Admin API returned status {response.status_code}")
