from dataclasses import dataclass
from typing import Any

import pytest

from app.core.config import Settings
from app.services import storage as storage_module
from app.services.storage import GCSBlobStorage


@dataclass
class FakeBlob:
    name: str
    data: bytes | None = None
    generation: str | None = None
    size: str | None = None
    content_type: str | None = None
    deleted: bool = False
    upload_options: dict[str, Any] | None = None
    delete_options: dict[str, Any] | None = None

    def upload_from_string(self, data: bytes, **kwargs: Any) -> None:
        self.data = data
        self.generation = "7"
        self.size = str(len(data))
        self.content_type = kwargs["content_type"]
        self.upload_options = kwargs

    def download_as_bytes(self, **kwargs: Any) -> bytes:
        if self.data is None:
            raise FileNotFoundError(self.name)
        return self.data

    def delete(self, **kwargs: Any) -> None:
        self.deleted = True
        self.delete_options = kwargs


class FakeBucket:
    def __init__(self, name: str) -> None:
        self.name = name
        self.blobs: dict[str, FakeBlob] = {}

    def blob(self, name: str) -> FakeBlob:
        return self.blobs.setdefault(name, FakeBlob(name))

    def list_blobs(self, *, prefix: str):
        return [blob for name, blob in self.blobs.items() if name.startswith(prefix)]


class FakeClient:
    def __init__(self) -> None:
        self.buckets: dict[str, FakeBucket] = {}

    def bucket(self, name: str) -> FakeBucket:
        return self.buckets.setdefault(name, FakeBucket(name))


def test_upload_download_and_generation_safe_delete() -> None:
    client = FakeClient()
    storage = GCSBlobStorage("limon-test", client=client)  # type: ignore[arg-type]

    result = storage.upload("voice/user/event.m4a", b"audio", content_type="audio/mp4")

    assert result.bucket == "limon-test"
    assert result.name == "voice/user/event.m4a"
    assert result.generation == 7
    assert result.size == 5
    assert storage.download(result.name) == b"audio"

    blob = client.buckets["limon-test"].blobs[result.name]
    assert blob.upload_options is not None
    assert blob.upload_options["if_generation_match"] == 0
    assert blob.upload_options["checksum"] == "auto"

    storage.delete(result.name, generation=result.generation)
    assert blob.deleted is True
    assert blob.delete_options is not None
    assert blob.delete_options["if_generation_match"] == 7


@pytest.mark.parametrize("name", ["", "   "])
def test_blank_blob_name_is_rejected(name: str) -> None:
    storage = GCSBlobStorage("limon-test", client=FakeClient())  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="blob name"):
        storage.download(name)


def test_blank_bucket_name_is_rejected() -> None:
    with pytest.raises(ValueError, match="bucket_name"):
        GCSBlobStorage(" ", client=FakeClient())  # type: ignore[arg-type]


def test_storage_factory_requires_bucket_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(storage_module, "get_settings", lambda: Settings(gcs_bucket=None))
    storage_module.get_blob_storage.cache_clear()

    try:
        with pytest.raises(RuntimeError, match="LIMON_GCS_BUCKET"):
            storage_module.get_blob_storage()
    finally:
        storage_module.get_blob_storage.cache_clear()


async def test_delete_user_audio_is_prefix_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient()
    bucket = client.bucket("limon-test")
    own = bucket.blob("v0/user-1/rec-1.m4a")
    own.generation = "3"
    other = bucket.blob("v0/user-2/rec-2.m4a")
    other.generation = "4"
    monkeypatch.setattr(storage_module, "_client", lambda: client)
    monkeypatch.setattr(
        storage_module, "get_settings", lambda: Settings(gcs_bucket="limon-test")
    )

    await storage_module.delete_user_audio("user-1", {own.name})

    assert own.deleted is True
    assert other.deleted is False


async def test_delete_user_audio_rejects_cross_owner_key() -> None:
    with pytest.raises(storage_module.AudioCleanupError, match="does not match"):
        await storage_module.delete_user_audio(
            "user-1", {"v0/user-2/rec-2.m4a"}
        )
