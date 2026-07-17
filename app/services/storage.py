"""Blob storage boundary backed by Google Cloud Storage.

The application depends on ``BlobStorage`` rather than the Google SDK. This
keeps domain services testable and leaves room for a different implementation
without spreading provider-specific calls through the codebase.
"""

from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

from google.cloud import storage
from google.cloud.storage.retry import DEFAULT_RETRY

from app.core.config import get_settings

_DEFAULT_TIMEOUT_SECONDS = 30
_RETRY = DEFAULT_RETRY.with_deadline(_DEFAULT_TIMEOUT_SECONDS)


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
        raise RuntimeError("Blob storage is not configured (set LIMON_GCS_BUCKET).")
    return GCSBlobStorage(bucket_name)
