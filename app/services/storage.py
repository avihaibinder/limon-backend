"""Google Cloud Storage upload presigning.

The client asks us for a short-lived URL, then uploads its audio file straight
to GCS with a single PUT — the bytes never pass through this backend.

Signing a V4 URL requires a *service account* identity. Rather than shipping a
private key (see the "no key-style credentials in settings" decision in
CLAUDE.md), we sign through the IAM ``signBlob`` API using whatever identity
Application Default Credentials resolves to at runtime:

* locally, ADC is your user login (``gcloud auth application-default login``)
  impersonating the signer SA you granted ``roles/iam.serviceAccountTokenCreator``;
* on Cloud Run, ADC is the attached service account, signing as itself.

Nothing here is tied to a specific GCP account — the bucket and signer SA come
from settings, so repointing at another (free-tier) account is config-only.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

import google.auth
import google.auth.transport.requests
from google.auth import impersonated_credentials
from google.cloud import storage

from app.core.config import get_settings

# GCS object key layout for audio uploads: one prefix per user keeps listing
# and lifecycle rules simple, and a UUID avoids collisions / guessable keys.
_AUDIO_PREFIX = "audio"

# Content types we let clients presign an upload for. Restricting this keeps a
# presign URL from being repurposed to store arbitrary payloads.
_ALLOWED_AUDIO_CONTENT_TYPES = frozenset(
    {
        "audio/mp4",  # .m4a — Expo/iOS default
        "audio/aac",
        "audio/mpeg",  # .mp3
        "audio/ogg",
        "audio/wav",
        "audio/webm",
    }
)


class StorageNotConfiguredError(RuntimeError):
    """Raised when no GCS bucket is configured (the router maps this to 503)."""


class UnsupportedContentTypeError(ValueError):
    """Raised for a content type outside the audio allowlist (router -> 400)."""


@dataclass(frozen=True)
class PresignedUpload:
    """Everything the client needs to PUT one object and reference it later."""

    upload_url: str
    object_key: str
    content_type: str
    expires_at: dt.datetime


def _object_key(user_id: str, content_type: str) -> str:
    extension = {
        "audio/mp4": "m4a",
        "audio/aac": "aac",
        "audio/mpeg": "mp3",
        "audio/ogg": "ogg",
        "audio/wav": "wav",
        "audio/webm": "webm",
    }[content_type]
    return f"{_AUDIO_PREFIX}/{user_id}/{uuid.uuid4()}.{extension}"


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
