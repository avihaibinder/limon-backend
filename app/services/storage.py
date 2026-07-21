"""Google Cloud Storage boundary — both directions of blob access.

Two complementary ways features touch GCS live here:

* **Client-direct upload** via a short-lived V4 *presigned* URL
  (``presign_audio_upload``): the client PUTs its audio straight to GCS and the
  bytes never pass through this backend. This is how the app captures voice
  notes.
* **Server-side byte I/O** via the ``BlobStorage`` protocol
  (``get_blob_storage``): the backend uploads/downloads/deletes objects itself,
  for anything it generates or must read (e.g. PDF export, transcription
  inputs). Depending on the ``BlobStorage`` protocol rather than the Google SDK
  keeps domain services testable and leaves room for another implementation.

Both rely on Application Default Credentials — no key file anywhere. Nothing is
tied to a specific GCP account: the bucket (and, for signing, an optional signer
SA) come from settings, so repointing at another account is config-only.

Signing note: producing a V4 signed URL needs a *service account* identity and,
because neither runtime identity holds a local private key, routes through the
IAM ``signBlob`` API. Local dev impersonates a configured signer SA; Cloud Run
signs as its own attached SA. See ``_signed_url_signing_kwargs``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

import google.auth
import google.auth.transport.requests
from google.auth import impersonated_credentials
from google.cloud import storage
from google.cloud.storage.retry import DEFAULT_RETRY

from app.core.config import get_settings

_DEFAULT_TIMEOUT_SECONDS = 30
_RETRY = DEFAULT_RETRY.with_deadline(_DEFAULT_TIMEOUT_SECONDS)

# GCS object key layout for audio uploads: one prefix per user keeps listing
# and lifecycle rules simple, and a UUID avoids collisions / guessable keys.
_AUDIO_PREFIX = "audio"

# Content types we let clients presign an upload for. Restricting this keeps a
# presign URL from being repurposed to store arbitrary payloads.
_AUDIO_EXTENSIONS = {
    "audio/mp4": "m4a",  # .m4a — Expo/iOS default
    "audio/aac": "aac",
    "audio/mpeg": "mp3",  # .mp3
    "audio/ogg": "ogg",
    "audio/wav": "wav",
    "audio/webm": "webm",
}
_ALLOWED_AUDIO_CONTENT_TYPES = frozenset(_AUDIO_EXTENSIONS)


class StorageNotConfiguredError(RuntimeError):
    """Raised when no GCS bucket is configured (the router maps this to 503)."""


class UnsupportedContentTypeError(ValueError):
    """Raised for a content type outside the audio allowlist (router -> 400)."""


# ---------------------------------------------------------------------------
# Server-side byte I/O — the BlobStorage protocol and its GCS implementation.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StoredBlob:
    """Provider-neutral metadata returned after an upload."""

    bucket: str
    name: str
    generation: int | None
    size: int | None
    content_type: str | None


class BlobStorage(Protocol):
    """Minimal blob operations required by LimON features."""

    def upload(self, name: str, data: bytes, *, content_type: str) -> StoredBlob: ...

    def download(self, name: str) -> bytes: ...

    def delete(self, name: str, *, generation: int) -> None: ...


class GCSBlobStorage:
    """Private Google Cloud Storage implementation using ambient credentials."""

    def __init__(
        self,
        bucket_name: str,
        *,
        client: storage.Client | None = None,
        timeout: int = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not bucket_name.strip():
            raise ValueError("bucket_name must not be blank")

        # storage.Client() uses Application Default Credentials. On Cloud Run
        # these come from the assigned service account, so no key file is used.
        self._client = client or storage.Client()
        self._bucket = self._client.bucket(bucket_name)
        self._timeout = timeout

    @property
    def bucket_name(self) -> str:
        return self._bucket.name

    def upload(self, name: str, data: bytes, *, content_type: str) -> StoredBlob:
        """Create a new private object and fail if its name already exists."""
        self._validate_name(name)
        if not content_type.strip():
            raise ValueError("content_type must not be blank")

        blob = self._bucket.blob(name)
        blob.upload_from_string(
            data,
            content_type=content_type,
            if_generation_match=0,
            timeout=self._timeout,
            retry=_RETRY,
            checksum="auto",
        )
        return StoredBlob(
            bucket=self.bucket_name,
            name=name,
            generation=self._as_int(blob.generation),
            size=self._as_int(blob.size),
            content_type=blob.content_type or content_type,
        )

    def download(self, name: str) -> bytes:
        self._validate_name(name)
        return self._bucket.blob(name).download_as_bytes(
            timeout=self._timeout,
            retry=_RETRY,
            checksum="auto",
        )

    def delete(self, name: str, *, generation: int) -> None:
        self._validate_name(name)
        self._bucket.blob(name).delete(
            timeout=self._timeout,
            retry=_RETRY,
            if_generation_match=generation,
        )

    @staticmethod
    def _validate_name(name: str) -> None:
        if not name or not name.strip():
            raise ValueError("blob name must not be blank")

    @staticmethod
    def _as_int(value: int | str | None) -> int | None:
        return int(value) if value is not None else None


@lru_cache
def get_blob_storage() -> GCSBlobStorage:
    """Build the production storage adapter lazily from application settings."""
    bucket_name = get_settings().gcs_bucket
    if bucket_name is None:
        raise StorageNotConfiguredError("Blob storage is not configured (set LIMON_GCS_BUCKET).")
    return GCSBlobStorage(bucket_name)


# ---------------------------------------------------------------------------
# Client-direct upload — presigned V4 URLs.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PresignedUpload:
    """Everything the client needs to PUT one object and reference it later."""

    upload_url: str
    object_key: str
    content_type: str
    expires_at: dt.datetime


def _object_key(user_id: str, content_type: str) -> str:
    return f"{_AUDIO_PREFIX}/{user_id}/{uuid.uuid4()}.{_AUDIO_EXTENSIONS[content_type]}"


def _signed_url_signing_kwargs() -> dict:
    """Build the kwargs `blob.generate_signed_url` needs to sign a V4 URL.

    Neither identity we run as can sign with a local private key, so both route
    through the IAM ``signBlob`` API — but they need *different* argument shapes,
    and mixing them up yields a 403 or a "need a private key" error:

    * **Local dev** — ADC is a *user* account, which cannot sign. We impersonate
      the configured signer SA; the impersonated credential is itself a signer,
      so we pass ONLY ``credentials`` and let its ``.sign_bytes()`` call signBlob
      as the target SA. (Passing an ``access_token`` here instead reverts to
      signing as the user, which lacks signBlob on the SA -> 403.)
    * **Cloud Run** — ADC is the attached service account as a *token-only*
      compute credential (no private key). We pass its own ``service_account_email``
      + a fresh ``access_token`` so signBlob signs as the attached SA itself.

    The signing SA needs ``roles/iam.serviceAccountTokenCreator`` — on the target
    SA (local) or on itself (Cloud Run) — for signBlob to be permitted.
    """
    settings = get_settings()
    source_credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )

    signer_sa = settings.gcs_signer_service_account
    if signer_sa is not None:
        credentials = impersonated_credentials.Credentials(
            source_credentials=source_credentials,
            target_principal=signer_sa,
            target_scopes=["https://www.googleapis.com/auth/cloud-platform"],
            lifetime=3600,
        )
        return {"credentials": credentials}

    # Cloud Run / compute: sign as the attached service account itself.
    # Refresh first — a compute credential reports service_account_email as the
    # placeholder "default" until it has hit the metadata server.
    request = google.auth.transport.requests.Request()
    source_credentials.refresh(request)
    signer_email = getattr(source_credentials, "service_account_email", None)
    if signer_email in (None, "default"):
        # Still unresolved: ask the metadata server for the real email directly.
        signer_email = _metadata_service_account_email(request)
    if not signer_email:
        raise StorageNotConfiguredError(
            "Could not resolve a service account email to sign with "
            "(set LIMON_GCS_SIGNER_SERVICE_ACCOUNT, or run on a service "
            "account with a resolvable identity)."
        )
    return {
        "credentials": source_credentials,
        "service_account_email": signer_email,
        "access_token": source_credentials.token,
    }


def _metadata_service_account_email(request) -> str | None:
    """Fetch the attached SA's email from the GCE/Cloud Run metadata server."""
    try:
        from google.auth.compute_engine import _metadata

        return _metadata.get_service_account_info(request).get("email")
    except Exception:
        return None


def _client() -> storage.Client:
    return storage.Client()


def presign_audio_upload(user_id: str, content_type: str) -> PresignedUpload:
    """Create a short-lived V4 signed URL for a single audio PUT upload.

    Raises StorageNotConfiguredError if no bucket is set, and
    UnsupportedContentTypeError if content_type is not an allowed audio type.
    """
    settings = get_settings()
    if settings.gcs_bucket is None:
        raise StorageNotConfiguredError("LIMON_GCS_BUCKET is not set")
    if content_type not in _ALLOWED_AUDIO_CONTENT_TYPES:
        raise UnsupportedContentTypeError(content_type)

    object_key = _object_key(user_id, content_type)
    ttl = dt.timedelta(seconds=settings.gcs_signed_url_ttl_seconds)

    blob = _client().bucket(settings.gcs_bucket).blob(object_key)
    upload_url = blob.generate_signed_url(
        version="v4",
        expiration=ttl,
        method="PUT",
        content_type=content_type,
        **_signed_url_signing_kwargs(),
    )

    return PresignedUpload(
        upload_url=upload_url,
        object_key=object_key,
        content_type=content_type,
        expires_at=dt.datetime.now(dt.UTC) + ttl,
    )
