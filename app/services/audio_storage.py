"""Audio blob read access for the transcription worker.

Kept separate from the upload-presign service (the open GCS PR / domain 08) so it
does not collide with that file before it merges; the two may be reconciled into
one storage module later. This module only *reads* audio: given a storage key,
return the bytes.

Two backends, chosen by config:
- ``local_audio_dir`` set -> read ``{dir}/{storage_key}`` from the filesystem
  (dev / tests / offline end-to-end against a raised endpoint).
- otherwise -> download from GCS (``gcs_bucket``), with ``google-cloud-storage``
  imported lazily so this module loads without that dependency installed.
"""

import asyncio
from pathlib import Path

from app.core.config import get_settings


class AudioStorageNotConfiguredError(RuntimeError):
    """Neither a local audio dir nor a GCS bucket is configured."""


class AudioNotFoundError(RuntimeError):
    """No object exists at the given storage key."""


async def download(storage_key: str) -> bytes:
    """Return the audio bytes for ``storage_key`` (runs the blocking read off-loop)."""
    return await asyncio.to_thread(_download_sync, storage_key)


def _download_sync(storage_key: str) -> bytes:
    settings = get_settings()
    local_dir = settings.local_audio_dir
    if local_dir:
        path = Path(local_dir) / storage_key
        if not path.is_file():
            raise AudioNotFoundError(f"No audio at {path}")
        return path.read_bytes()

    # `gcs_bucket` is added by the storage PR (domain 08); tolerate its absence.
    bucket = getattr(settings, "gcs_bucket", None)
    if not bucket:
        raise AudioStorageNotConfiguredError(
            "No audio storage configured (set LIMON_LOCAL_AUDIO_DIR for dev, "
            "or LIMON_GCS_BUCKET for GCS)."
        )
    return _download_gcs(bucket, storage_key)


def _download_gcs(bucket: str, storage_key: str) -> bytes:
    # Lazy import: google-cloud-storage is only needed for the GCS path and lands
    # with the storage work (domain 08). ADC only, no key files.
    from google.cloud import storage

    blob = storage.Client().bucket(bucket).blob(storage_key)
    if not blob.exists():
        raise AudioNotFoundError(f"No audio at gs://{bucket}/{storage_key}")
    return blob.download_as_bytes()
