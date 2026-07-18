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

    # Google Cloud Storage bucket for blob storage — server-side byte I/O
    # (BlobStorage) and the target for client-direct upload presign URLs. Unset
    # means the uploads endpoint 503s and get_blob_storage() raises. Nothing
    # account-specific lives in code, so pointing this (and the signer SA below)
    # at a different GCP account is the only change needed to move environments.
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
