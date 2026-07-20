from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="LIMON_",
        extra="ignore",
    )

    app_name: str = "LimON Backend"
    debug: bool = False
    database_url: str = "sqlite+aiosqlite:///./limon.db"
    cors_origins: list[str] = ["*"]

    # Supabase project URL (https://<ref>.supabase.co). Tokens are verified
    # against its JWKS endpoint; unset means every authenticated route 401s.
    supabase_url: str | None = None
    # Only for legacy Supabase projects still signing with the shared HS256
    # secret; projects on asymmetric signing keys don't need it.
    supabase_jwt_secret: str | None = None

    # Nebius transcription endpoint (raised on demand; see
    # spec-local/BACKEND_INTEGRATION.md). URL and token change on every
    # (re)create, so they are deployment config / secrets, not constants. Unset
    # means the worker treats transcription as unavailable rather than crashing.
    transcriber_endpoint_url: str | None = None
    transcriber_endpoint_token: str | None = None
    # Client timeout (seconds) for one transcription call. Sized from the L40S
    # rtf (~0.09 * audio_seconds): a 5-minute clip is about 30s of processing,
    # so 90s leaves headroom for a warming endpoint.
    transcriber_timeout_s: float = 90.0

    # Local filesystem directory the worker reads audio from in dev/testing
    # instead of GCS. When set, audio_storage.download reads `{dir}/{storage_key}`.
    # Unset in prod, where downloads go to GCS (bucket from `gcs_bucket`, added by
    # the storage PR / domain 08).
    local_audio_dir: str | None = None
    # Interim shared-secret guard for the internal worker endpoint until Cloud
    # Tasks OIDC verification is wired (domain 04). Unset means the guard is open
    # (local dev only).
    internal_task_token: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
