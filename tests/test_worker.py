"""Transcription worker: service logic and the internal endpoint.

The transcriber client and the GCS read are stubbed via monkeypatch, so no network
and no real storage. Rows are seeded through the in-memory `session_factory`.
"""

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.event import Event
from app.models.recording import Recording
from app.models.user import User
from app.services import audio_storage, transcriber, transcription
from app.services.transcriber import (
    AudioRejectedError,
    EndpointBusyError,
    EndpointUnavailableError,
    TranscriptionResult,
)


async def _seed(session: AsyncSession) -> tuple[str, str]:
    """Create a user + pending recording + linked audio event; return (recId, evId)."""
    user = User(provider="google", provider_subject="s", email="a@example.com")
    session.add(user)
    await session.flush()
    recording = Recording(
        user_id=user.id, storage_key=f"v0/{user.id}/r.m4a", content_type="audio/mp4"
    )
    session.add(recording)
    await session.flush()
    event = Event(
        type="audio", title=None, occurred_at=datetime.now(UTC), recording_id=recording.id
    )
    session.add(event)
    await session.commit()
    return recording.id, event.id


def _stub_download(data: bytes = b"audio-bytes"):
    async def _download(storage_key: str) -> bytes:
        return data

    return _download


def _stub_transcribe(*, result: TranscriptionResult | None = None, exc: Exception | None = None):
    async def _transcribe(audio: bytes, **kwargs: object) -> TranscriptionResult:
        if exc is not None:
            raise exc
        assert result is not None
        return result

    return _transcribe


async def test_success_writes_transcript_and_marks_done(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with session_factory() as session:
        rec_id, ev_id = await _seed(session)
    monkeypatch.setattr(audio_storage, "download", _stub_download())
    monkeypatch.setattr(
        transcriber,
        "transcribe",
        _stub_transcribe(result=TranscriptionResult(text="שלום עולם", segments=[])),
    )

    async with session_factory() as session:
        outcome = await transcription.run_transcription(session, rec_id)

    assert outcome.status == "done"
    async with session_factory() as session:
        event = await session.get(Event, ev_id)
        recording = await session.get(Recording, rec_id)
        assert event is not None and event.description == "שלום עולם"
        assert recording is not None and recording.state == "done"


async def test_noop_and_no_download_when_already_done(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with session_factory() as session:
        rec_id, ev_id = await _seed(session)
        recording = await session.get(Recording, rec_id)
        event = await session.get(Event, ev_id)
        recording.state = "done"
        event.description = "already there"
        await session.commit()

    calls = {"n": 0}

    async def _download(storage_key: str) -> bytes:
        calls["n"] += 1
        return b"x"

    monkeypatch.setattr(audio_storage, "download", _download)

    async with session_factory() as session:
        outcome = await transcription.run_transcription(session, rec_id)

    assert outcome.status == "noop"
    assert calls["n"] == 0
    async with session_factory() as session:
        event = await session.get(Event, ev_id)
        assert event is not None and event.description == "already there"


async def test_soft_failure_reverts_to_pending(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with session_factory() as session:
        rec_id, ev_id = await _seed(session)
    monkeypatch.setattr(audio_storage, "download", _stub_download())
    monkeypatch.setattr(
        transcriber, "transcribe", _stub_transcribe(exc=EndpointUnavailableError("down"))
    )

    async with session_factory() as session:
        outcome = await transcription.run_transcription(session, rec_id)

    assert outcome.status == "retry"
    async with session_factory() as session:
        recording = await session.get(Recording, rec_id)
        event = await session.get(Event, ev_id)
        assert recording is not None and recording.state == "pending"
        assert event is not None and event.description is None


async def test_busy_carries_retry_after(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with session_factory() as session:
        rec_id, _ = await _seed(session)
    monkeypatch.setattr(audio_storage, "download", _stub_download())
    monkeypatch.setattr(
        transcriber, "transcribe", _stub_transcribe(exc=EndpointBusyError("busy", retry_after=30))
    )

    async with session_factory() as session:
        outcome = await transcription.run_transcription(session, rec_id)

    assert outcome.status == "retry"
    assert outcome.retry_after == 30


async def test_hard_failure_marks_failed(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with session_factory() as session:
        rec_id, ev_id = await _seed(session)
    monkeypatch.setattr(audio_storage, "download", _stub_download())
    monkeypatch.setattr(
        transcriber, "transcribe", _stub_transcribe(exc=AudioRejectedError("cannot decode"))
    )

    async with session_factory() as session:
        outcome = await transcription.run_transcription(session, rec_id)

    assert outcome.status == "failed"
    async with session_factory() as session:
        recording = await session.get(Recording, rec_id)
        event = await session.get(Event, ev_id)
        assert recording is not None and recording.state == "failed"
        assert recording.error == "AudioRejectedError"
        assert event is not None and event.description is None


async def test_missing_record_is_noop(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        outcome = await transcription.run_transcription(session, "does-not-exist")
    assert outcome.status == "noop"


async def test_internal_endpoint_transcribes(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with session_factory() as session:
        rec_id, ev_id = await _seed(session)
    monkeypatch.setattr(audio_storage, "download", _stub_download())
    monkeypatch.setattr(
        transcriber,
        "transcribe",
        _stub_transcribe(result=TranscriptionResult(text="hi", segments=[])),
    )

    response = await client.post("/internal/transcribe", json={"recordId": rec_id})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "done"


async def test_internal_endpoint_returns_503_on_soft_failure(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with session_factory() as session:
        rec_id, _ = await _seed(session)
    monkeypatch.setattr(audio_storage, "download", _stub_download())
    monkeypatch.setattr(
        transcriber, "transcribe", _stub_transcribe(exc=EndpointBusyError("busy", retry_after=15))
    )

    response = await client.post("/internal/transcribe", json={"recordId": rec_id})
    assert response.status_code == 503
    assert response.headers.get("Retry-After") == "15"
