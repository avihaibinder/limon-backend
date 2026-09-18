"""Transcription trigger: the GCS-finalize handler and the object-key parse.

The Cloud Tasks enqueue is mocked at the seam (``task_queue.enqueue_transcription``),
so no GCP is touched. There is no dev shim (removed per decision 17); the worker
path is covered directly in test_worker.py.
"""

import base64
import json

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.recording import Recording
from app.models.user import User
from app.services import storage, task_queue
from app.services.storage import audio_object_key, record_id_from_audio_key

REC_ID = "8f1c2b34-0000-4000-8000-000000000000"
OBJECT_NAME = f"v0/user-1/{REC_ID}.m4a"


# ---------------------------------------------------------------------------
# Object-key layout: minted by the events service, parsed by the handler.
# ---------------------------------------------------------------------------


def test_audio_key_round_trips() -> None:
    key = audio_object_key("user-1", REC_ID)
    assert key == OBJECT_NAME
    assert record_id_from_audio_key(key) == REC_ID


@pytest.mark.parametrize(
    "key",
    [
        "v1/user-1/r.m4a",  # wrong prefix
        "v0/user-1/nested/r.m4a",  # nested path (4 parts)
        "v0/user-1/r.txt",  # wrong suffix
        "v0/user-1/.m4a",  # empty stem
        "r.m4a",  # not namespaced
    ],
)
def test_non_audio_keys_are_rejected(key: str) -> None:
    assert record_id_from_audio_key(key) is None


# ---------------------------------------------------------------------------
# POST /internal/uploaded — the Pub/Sub push finalize handler.
# ---------------------------------------------------------------------------


def _finalize_envelope(
    *,
    object_id: str | None = OBJECT_NAME,
    event_type: str = "OBJECT_FINALIZE",
    data: str | None = None,
) -> dict:
    attributes = {"eventType": event_type}
    if object_id is not None:
        attributes["objectId"] = object_id
    message: dict = {"attributes": attributes, "messageId": "m1"}
    if data is not None:
        message["data"] = data
    return {"message": message, "subscription": "projects/p/subscriptions/s"}


def _record_enqueue(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    async def _fake(record_id: str) -> None:
        calls.append(record_id)

    monkeypatch.setattr(task_queue, "enqueue_transcription", _fake)
    return calls


async def _seed_recording(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add(User(id="user-1", provider="google"))
        await session.flush()
        session.add(
            Recording(
                id=REC_ID,
                user_id="user-1",
                storage_key=OBJECT_NAME,
                content_type="audio/mp4",
            )
        )
        await session.commit()


async def test_finalize_enqueues_transcription(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_recording(session_factory)
    calls = _record_enqueue(monkeypatch)

    response = await client.post("/internal/uploaded", json=_finalize_envelope())

    assert response.status_code == 204
    assert calls == [REC_ID]


async def test_finalize_reads_object_name_from_data_payload(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No objectId attribute: the name lives only in the base64 object-resource JSON.
    calls = _record_enqueue(monkeypatch)
    await _seed_recording(session_factory)
    data = base64.b64encode(json.dumps({"name": OBJECT_NAME}).encode()).decode()

    response = await client.post(
        "/internal/uploaded", json=_finalize_envelope(object_id=None, data=data)
    )

    assert response.status_code == 204
    assert calls == [REC_ID]


async def test_non_finalize_event_is_ignored(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _record_enqueue(monkeypatch)

    response = await client.post(
        "/internal/uploaded", json=_finalize_envelope(event_type="OBJECT_DELETE")
    )

    assert response.status_code == 204
    assert calls == []


async def test_non_audio_key_is_ignored(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _record_enqueue(monkeypatch)

    response = await client.post(
        "/internal/uploaded", json=_finalize_envelope(object_id="v0/user-1/notes.txt")
    )

    assert response.status_code == 204
    assert calls == []


async def test_enqueue_failure_is_not_acked(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A failed enqueue must NOT ack, so Pub/Sub redelivers and retries. The ASGI
    # transport re-raises the app exception rather than turning it into a 5xx body.
    async def _boom(record_id: str) -> None:
        raise task_queue.TaskQueueError("cloud tasks down")

    monkeypatch.setattr(task_queue, "enqueue_transcription", _boom)
    await _seed_recording(session_factory)

    with pytest.raises(task_queue.TaskQueueError):
        await client.post("/internal/uploaded", json=_finalize_envelope())


async def test_finalize_deletes_late_orphan_instead_of_enqueuing(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _record_enqueue(monkeypatch)
    deleted: list[str] = []

    async def _delete(object_key: str) -> None:
        deleted.append(object_key)

    monkeypatch.setattr(storage, "delete_orphan_audio_object", _delete)

    response = await client.post("/internal/uploaded", json=_finalize_envelope())

    assert response.status_code == 204
    assert calls == []
    assert deleted == [OBJECT_NAME]
