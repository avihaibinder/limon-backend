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
    # Supabase service-role key, used only server-side to call the Auth Admin API
    # (delete-account removes the auth.users identity, which our public.* cascade
    # cannot reach). A secret; never sent to clients. When supabase_url is unset
    # (local dev/tests, no real Supabase) the admin call is skipped, so this is
    # only required in an environment that actually has Supabase auth.
    supabase_service_role_key: str | None = None

    # Hebrew transcriber: the always-on Oracle VPS box, over its async job API
    # (submit -> callback -> drain -> persist -> ack). See spec/transcription.md.
    #
    # The base URL is a Cloudflare Quick Tunnel and is reminted whenever the
    # tunnel restarts, so it is deployment config that must be changeable without
    # a rebuild, and "it stopped resolving" is an expected operating condition
    # rather than an incident. Unset means the worker treats transcription as
    # unavailable rather than crashing.
    transcriber_base_url: str | None = None
    transcriber_token: str | None = None
    # Client timeout (seconds) for one call to the box. This sizes an *upload*,
    # not a transcription: POST /jobs returns once ffprobe has read the file, and
    # the transcript arrives much later over the callback.
    transcriber_timeout_s: float = 90.0

    # The secret the box presents on its callback. We choose the value; it is set
    # on the box as CALLBACK_SECRET with CALLBACK_AUTH=secret. Unset means the
    # callback endpoint rejects every call -- it fails closed rather than open,
    # unlike `internal_task_token` above, because the contract requires the
    # callback to be authenticated and its caller can always supply the header.
    transcriber_callback_secret: str | None = None
    # The header that secret arrives in. The box's own default; configurable
    # there as CALLBACK_SECRET_HEADER if it ever needs to change.
    transcriber_callback_header: str = "X-Callback-Token"

    # Pre-flight limits, mirroring the box's MAX_AUDIO_DURATION_S and
    # MAX_UPLOAD_MB so a submission that would be refused is never sent. The byte
    # cap is 25 MiB, matching the signed-URL cap in storage.py; the two are
    # deliberately equal and moving one without the other reopens a gap where a
    # file passes GCS and then fails transcription.
    transcriber_max_audio_duration_s: float = 600.0
    transcriber_max_upload_bytes: int = 25 * 1024 * 1024

    # Recovery thresholds. `transcribing` means "submitted, waiting for a
    # callback", which is normal for as long as the box's queue is deep, so the
    # bar for calling one stuck is high. `pending` past its Cloud Tasks retry
    # budget (about three minutes) is a submission that never landed.
    transcriber_stale_submitted_hours: float = 6.0
    transcriber_stale_pending_minutes: float = 30.0
    # How many stale rows one recovery pass will look at. Recovery piggybacks on
    # the callback, so this bounds the work a single callback can turn into: the
    # stuck-row check costs one request to the box each. Whatever is left over is
    # picked up by the next callback -- the audio is still in GCS, so nothing
    # expires while it waits.
    transcriber_recovery_batch_limit: int = 25

    # Local filesystem directory the worker reads audio from in dev/testing
    # instead of GCS. When set, audio_storage.download reads `{dir}/{storage_key}`.
    # Unset in prod, where downloads go to GCS (bucket from `gcs_bucket` below).
    local_audio_dir: str | None = None
    # Interim shared-secret guard for the internal worker endpoints until Cloud
    # Tasks / Pub/Sub OIDC verification is wired (deploy). Unset means the guard is
    # open (local dev only).
    internal_task_token: str | None = None

    # Cloud Tasks: the GCS-finalize handler (POST /internal/uploaded) enqueues one
    # task per uploaded audio object, targeting POST /internal/transcribe, so the
    # worker runs with a capped retry budget rather than a raw Pub/Sub push. All
    # deploy config, not code (Secret Manager / --set-env-vars); unset in dev/tests,
    # where the enqueue seam is mocked and the dev shim replaces the trigger. See
    # spec-local/plan/04-trigger.md.
    tasks_project: str | None = None
    tasks_location: str | None = None
    tasks_queue: str | None = None
    # Base URL of the worker service the task calls (e.g. the Cloud Run URL); the
    # task targets {tasks_worker_url}/internal/transcribe.
    tasks_worker_url: str | None = None
    # Service account the task authenticates as (OIDC) when calling the worker, so
    # the worker can verify the caller. Unset means no OIDC token on the task.
    tasks_oidc_service_account: str | None = None

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

    # GroqCloud auto-tagging endpoint (OpenAI-compatible chat completions).
    # Unset api_key makes the worker treat tagging as temporarily unavailable.
    tagger_api_key: str | None = None
    tagger_model: str = "qwen/qwen3.8-27b"
    tagger_base_url: str = "https://api.groq.com/openai/v1"
    tagger_timeout_s: float = 60.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
