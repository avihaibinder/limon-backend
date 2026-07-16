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

    # Cloud Run provides Application Default Credentials through its runtime
    # service account. Only the private bucket name belongs in configuration.
    gcs_bucket: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
