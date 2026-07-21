import json
from functools import lru_cache
from typing import Annotated, Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="LIMON_",
        extra="ignore",
    )

    app_name: str = "LimON Backend"
    debug: bool = False
    database_url: str = "sqlite+aiosqlite:///./limon.db"
    cors_origins: Annotated[list[str], NoDecode] = ["*"]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: Any) -> Any:
        # Accept both a JSON array (["https://a", "https://b"]) and a plain
        # comma-separated string (https://a,https://b) so the env var is easy to
        # set from a shell or a Cloud Run --set-env-vars flag.
        if not isinstance(value, str):
            return value

        value = value.strip()
        if value.startswith("["):
            return json.loads(value)

        return [origin.strip() for origin in value.split(",") if origin.strip()]

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
    # Unset in prod, where downloads go to GCS (bucket from `gcs_bucket` below).
    local_audio_dir: str | None = None
    # Interim shared-secret guard for the internal worker endpoint until Cloud
    # Tasks OIDC verification is wired (domain 04). Unset means the guard is open
    # (local dev only).
    internal_task_token: str | None = None

    # Google Cloud Storage bucket for blob storage — server-side byte I/O
    # (BlobStorage) and the target for client-direct upload presign URLs. Unset
    # means POST /events for an audio event 503s and get_blob_storage() raises.
    # Nothing account-specific lives in code, so pointing this (and the signer SA
    # below) at a different GCP account is the only change needed to move envs.
    gcs_bucket: str | None = None
    # Service account whose identity signs the V4 upload URLs. Signing needs a
    # service-account identity; with ADC we call the IAM signBlob API as this
    # SA rather than shipping a private key. Set this for local dev (the SA you
    # granted yourself roles/iam.serviceAccountTokenCreator on). On Cloud Run
    # leave it unset — ADC is the attached service account, which signs itself.
    gcs_signer_service_account: str | None = None
    # How long a presigned upload URL stays valid, in seconds (default 15 min).
    gcs_signed_url_ttl_seconds: int = 900


@lru_cache
def get_settings() -> Settings:
    return Settings()
