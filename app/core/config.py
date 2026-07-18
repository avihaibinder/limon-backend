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

    # Google Cloud Storage bucket that upload presign URLs target. Unset means
    # the uploads endpoint 503s — nothing account-specific lives in code, so
    # pointing this (and the signer SA below) at a different GCP account is the
    # only change needed to move environments.
    gcs_bucket: str | None = None
    # Service account whose identity signs the V4 upload URLs. Signing needs a
    # service-account identity; with ADC we call the IAM signBlob API as this
    # SA rather than shipping a private key. On Cloud Run set this to the
    # attached service account; locally, the SA you granted yourself
    # roles/iam.serviceAccountTokenCreator on. Unset => derive from ADC.
    gcs_signer_service_account: str | None = None
    # How long a presigned upload URL stays valid, in seconds (default 15 min).
    gcs_signed_url_ttl_seconds: int = 900


@lru_cache
def get_settings() -> Settings:
    return Settings()
