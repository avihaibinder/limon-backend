"""Transcription domain logic: submit, collect, sweep, and the internal routes.

The transcriber client and the GCS read are stubbed via monkeypatch, so no
network and no real storage. Rows are seeded through the in-memory
`session_factory`.

The test that matters most here is `test_a_failed_persist_is_not_acknowledged`:
the ack is the only thing that deletes a result on the box, so acking something
we failed to store loses it permanently.
"""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.models.event import Event
from app.models.recording import Recording
from app.models.user import User
from app.services import audio_storage, task_queue, transcriber, transcription
from app.services.transcriber import (
    DrainPage,
    EndpointBusyError,
    EndpointUnavailableError,
    JobConflictError,
    SubmitResult,
    TranscriptJob,
)

SECRET = "callback-secret"


async def _seed(
    session: AsyncSession, *, state: str = "pending", duration: int | None = None
) -> tuple[str, str]:
    """Create a user + recording + linked audio event; return (recordId, eventId)."""
    user = User(id="s", provider="google", email="a@example.com")
    session.add(user)
    await session.flush()
    recording = Recording(
        user_id=user.id,
        storage_key=f"v0/{user.id}/r.m4a",
        content_type="audio/mp4",
        state=state,
        duration_sec=duration,
    )
    session.add(recording)
    await session.flush()
    event = Event(
        user_id=user.id,
        type="audio",
        title=None,
        occurred_at=datetime.now(UTC),
        recording_id=recording.id,
    )
    session.add(event)
    await session.commit()
    return recording.id, event.id


@pytest.fixture(autouse=True)
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point both modules at a configured, isolated Settings."""
    settings = Settings(
        _env_file=None,
        transcriber_base_url="https://box.test",
        transcriber_token="t",
        transcriber_callback_secret=SECRET,
    )
    for module in (transcription, transcriber):
        monkeypatch.setattr(module, "get_settings", lambda: settings, raising=False)
    import app.routers.internal as internal_router

    monkeypatch.setattr(internal_router, "get_settings", lambda: settings)
    monkeypatch.setattr(audio_storage, "download", _download(b"audio-bytes"))
    monkeypatch.setattr(task_queue, "enqueue_tagging", _noop_async)
    monkeypatch.setattr(task_queue, "enqueue_transcription", _noop_async)


async def _noop_async(*args: object, **kwargs: object) -> None:
    return None


def _download(data: bytes):
    async def _inner(storage_key: str) -> bytes:
        return data

    return _inner


def _stub_submit(monkeypatch: pytest.MonkeyPatch, *, exc: Exception | None = None):
    calls: list[str] = []

    async def _submit(job_id: str, audio: bytes, **kwargs: object) -> SubmitResult:
        calls.append(job_id)
        if exc is not None and len(calls) == 1:
            raise exc
        return SubmitResult(job_id=job_id, status="queued", already_existed=False)

    monkeypatch.setattr(transcriber, "submit", _submit)
    return calls


def _stub_cycle(monkeypatch: pytest.MonkeyPatch, pages: list[DrainPage]):
    """Stub drain/ack. Returns the list that records every acked id."""
    acked: list[list[str]] = []
    remaining = list(pages)

    async def _drain(**kwargs: object) -> DrainPage:
        return remaining.pop(0) if remaining else DrainPage(jobs=[])

    async def _ack(job_ids: list[str], **kwargs: object) -> dict:
        acked.append(list(job_ids))
        return {"deleted": job_ids, "not_found": []}

    monkeypatch.setattr(transcriber, "drain", _drain)
    monkeypatch.setattr(transcriber, "ack", _ack)
    return acked


def _done(job_id: str, text: str = "שלום") -> TranscriptJob:
    return TranscriptJob(job_id=job_id, status="done", text=text)


def _failed(job_id: str, code: str) -> TranscriptJob:
    return TranscriptJob(job_id=job_id, status="failed", error_code=code)


# --- submission ------------------------------------------------------------


async def test_submit_claims_the_row_and_leaves_it_transcribing(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The transcript arrives later, so `transcribing` is where the row rests."""
    async with session_factory() as session:
        record_id, _ = await _seed(session)
        calls = _stub_submit(monkeypatch)

        outcome = await transcription.run_transcription(session, record_id)

        assert outcome.status == "submitted"
        assert calls == [record_id]
        assert (await session.get(Recording, record_id)).state == "transcribing"


async def test_job_id_is_the_recording_id(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A UUID4 we already hold: unguessable, and needs no correlation column."""
    async with session_factory() as session:
        record_id, _ = await _seed(session)
        calls = _stub_submit(monkeypatch)
        await transcription.run_transcription(session, record_id)
        assert calls[0] == record_id


async def test_a_second_task_for_the_same_recording_is_a_noop(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The atomic claim is what makes duplicate Cloud Tasks safe."""
    async with session_factory() as session:
        record_id, _ = await _seed(session)
        calls = _stub_submit(monkeypatch)

        await transcription.run_transcription(session, record_id)
        outcome = await transcription.run_transcription(session, record_id)

        assert outcome.status == "noop"
        assert len(calls) == 1


async def test_over_long_recording_fails_without_an_upload(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with session_factory() as session:
        record_id, _ = await _seed(session, duration=900)
        calls = _stub_submit(monkeypatch)

        outcome = await transcription.run_transcription(session, record_id)

        assert outcome.status == "failed"
        assert calls == []
        recording = await session.get(Recording, record_id)
        assert recording.state == "failed"
        assert "longer" in recording.error


async def test_missing_duration_still_submits(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`duration_sec` is client-supplied and nullable; absence is not a rejection."""
    async with session_factory() as session:
        record_id, _ = await _seed(session, duration=None)
        calls = _stub_submit(monkeypatch)

        outcome = await transcription.run_transcription(session, record_id)

        assert outcome.status == "submitted"
        assert calls == [record_id]


async def test_oversized_audio_fails_without_an_upload(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with session_factory() as session:
        record_id, _ = await _seed(session)
        monkeypatch.setattr(audio_storage, "download", _download(b"x" * (25 * 1024 * 1024 + 1)))
        calls = _stub_submit(monkeypatch)

        outcome = await transcription.run_transcription(session, record_id)

        assert outcome.status == "failed"
        assert calls == []


async def test_conflict_discards_then_resubmits_once(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """409 means the box holds this id with different audio; ours is the truth."""
    async with session_factory() as session:
        record_id, _ = await _seed(session)
        calls = _stub_submit(monkeypatch, exc=JobConflictError("conflict"))
        discarded: list[str] = []

        async def _discard(job_id: str, **kwargs: object) -> None:
            discarded.append(job_id)

        monkeypatch.setattr(transcriber, "discard", _discard)

        outcome = await transcription.run_transcription(session, record_id)

        assert outcome.status == "submitted"
        assert discarded == [record_id]
        assert len(calls) == 2


async def test_queue_full_reverts_to_pending_for_retry(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with session_factory() as session:
        record_id, _ = await _seed(session)

        async def _submit(*args: object, **kwargs: object) -> SubmitResult:
            raise EndpointBusyError("full", retry_after=90)

        monkeypatch.setattr(transcriber, "submit", _submit)

        outcome = await transcription.run_transcription(session, record_id)

        assert outcome.status == "retry"
        assert outcome.retry_after == 90
        assert (await session.get(Recording, record_id)).state == "pending"


async def test_unreachable_box_reverts_to_pending(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The expected state when the tunnel URL has rotated."""
    async with session_factory() as session:
        record_id, _ = await _seed(session)

        async def _submit(*args: object, **kwargs: object) -> SubmitResult:
            raise EndpointUnavailableError("gone")

        monkeypatch.setattr(transcriber, "submit", _submit)

        outcome = await transcription.run_transcription(session, record_id)

        assert outcome.status == "retry"
        assert (await session.get(Recording, record_id)).state == "pending"


# --- collection ------------------------------------------------------------


async def test_transcript_is_written_then_acknowledged(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with session_factory() as session:
        record_id, event_id = await _seed(session, state="transcribing")
        acked = _stub_cycle(monkeypatch, [DrainPage(jobs=[_done(record_id, "שלום עולם")])])

        result = await transcription.collect_transcripts(session)

        assert result.written == 1
        assert acked == [[record_id]]
        assert (await session.get(Event, event_id)).description == "שלום עולם"
        assert (await session.get(Recording, record_id)).state == "done"


async def test_a_failed_persist_is_not_acknowledged(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ack deletes the only copy, so a job we could not store must stay put."""
    async with session_factory() as session:
        record_id, _ = await _seed(session, state="transcribing")
        acked = _stub_cycle(monkeypatch, [DrainPage(jobs=[_done(record_id)])])

        async def _explode(*args: object, **kwargs: object) -> None:
            raise RuntimeError("database is on fire")

        monkeypatch.setattr(transcription, "_persist_transcript", _explode)

        result = await transcription.collect_transcripts(session)

        assert acked == []
        assert result.written == 0
        assert (await session.get(Recording, record_id)).state == "transcribing"


async def test_collecting_twice_writes_once_and_acks_both_times(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two callbacks, or two Cloud Run instances, must be boring."""
    async with session_factory() as session:
        record_id, event_id = await _seed(session, state="transcribing")
        page = DrainPage(jobs=[_done(record_id, "first")])
        acked = _stub_cycle(monkeypatch, [page, page])

        first = await transcription.collect_transcripts(session)
        second = await transcription.collect_transcripts(session)

        assert first.written == 1
        assert second.written == 0
        assert second.skipped == 1
        assert acked == [[record_id], [record_id]]
        assert (await session.get(Event, event_id)).description == "first"


async def test_unknown_job_is_acknowledged_not_stranded(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deleted account or event: nothing to store, but it must not sit for 30 days."""
    async with session_factory() as session:
        await _seed(session, state="transcribing")
        acked = _stub_cycle(monkeypatch, [DrainPage(jobs=[_done("no-such-recording")])])

        result = await transcription.collect_transcripts(session)

        assert result.unknown == 1
        assert acked == [["no-such-recording"]]


async def test_permanent_failure_marks_the_recording_failed(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """By the time we see `failed`, the box has spent both of its attempts."""
    async with session_factory() as session:
        record_id, _ = await _seed(session, state="transcribing")
        acked = _stub_cycle(monkeypatch, [DrainPage(jobs=[_failed(record_id, "not_audio")])])

        result = await transcription.collect_transcripts(session)

        assert result.failed == 1
        assert acked == [[record_id]]
        recording = await session.get(Recording, record_id)
        assert recording.state == "failed"
        assert recording.error == "not_audio"


async def test_audio_missing_goes_back_to_pending_for_resubmission(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acking frees the id, so the sweep can resubmit under the same one."""
    async with session_factory() as session:
        record_id, _ = await _seed(session, state="transcribing")
        acked = _stub_cycle(monkeypatch, [DrainPage(jobs=[_failed(record_id, "audio_missing")])])

        await transcription.collect_transcripts(session)

        assert acked == [[record_id]]
        assert (await session.get(Recording, record_id)).state == "pending"


async def test_drain_pages_until_the_envelope_counts_are_satisfied(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`len(jobs) < done_unacked + failed_unacked` is a number, not an inference."""
    async with session_factory() as session:
        first, _ = await _seed(session, state="transcribing")
        pages = [
            DrainPage(jobs=[_done(first)], done_unacked=2),
            DrainPage(jobs=[_done("second")], done_unacked=1),
        ]
        acked = _stub_cycle(monkeypatch, pages)

        result = await transcription.collect_transcripts(session)

        assert result.pages == 2
        assert acked == [[first], ["second"]]


async def test_a_page_that_cannot_be_stored_stops_the_loop(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing acked means nothing deleted, so the next page would be identical."""
    async with session_factory() as session:
        record_id, _ = await _seed(session, state="transcribing")
        page = DrainPage(jobs=[_done(record_id)], done_unacked=99)
        acked = _stub_cycle(monkeypatch, [page] * 20)

        async def _explode(*args: object, **kwargs: object) -> None:
            raise RuntimeError("nope")

        monkeypatch.setattr(transcription, "_persist_transcript", _explode)

        result = await transcription.collect_transcripts(session)

        assert result.pages == 1
        assert acked == []


async def test_tagging_is_enqueued_after_a_transcript_lands(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with session_factory() as session:
        record_id, event_id = await _seed(session, state="transcribing")
        _stub_cycle(monkeypatch, [DrainPage(jobs=[_done(record_id)])])
        tagged: list[str] = []

        async def _dispatch(event: str) -> None:
            tagged.append(event)

        monkeypatch.setattr(transcription.tagging, "dispatch_tagging", _dispatch)

        await transcription.collect_transcripts(session)

        assert tagged == [event_id]


async def test_tagging_failure_does_not_undo_a_stored_transcript(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with session_factory() as session:
        record_id, event_id = await _seed(session, state="transcribing")
        acked = _stub_cycle(monkeypatch, [DrainPage(jobs=[_done(record_id, "kept")])])

        async def _dispatch(event: str) -> None:
            raise task_queue.TaskQueueError("queue down")

        monkeypatch.setattr(transcription.tagging, "dispatch_tagging", _dispatch)

        result = await transcription.collect_transcripts(session)

        assert result.written == 1
        assert acked == [[record_id]]
        assert (await session.get(Event, event_id)).description == "kept"


# --- sweep -----------------------------------------------------------------


async def test_sweep_requeues_a_recording_the_box_has_lost(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """404 from the box means no transcript is coming, whatever the reason."""
    async with session_factory() as session:
        record_id, _ = await _seed(session, state="transcribing")
        await session.execute(
            Recording.__table__.update()
            .where(Recording.id == record_id)
            .values(updated_at=datetime.now(UTC) - timedelta(days=2))
        )
        await session.commit()
        _stub_cycle(monkeypatch, [])
        monkeypatch.setattr(transcriber, "get_job", _noop_async)
        enqueued: list[str] = []

        async def _enqueue(rid: str) -> None:
            enqueued.append(rid)

        monkeypatch.setattr(task_queue, "enqueue_transcription", _enqueue)

        summary = await transcription.sweep(session)

        assert summary["requeued_stuck"] == 1
        assert enqueued == [record_id]
        assert (await session.get(Recording, record_id)).state == "pending"


async def test_sweep_leaves_a_job_the_box_still_has(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deep queue is not a stall: the box is slower than real time by design."""
    async with session_factory() as session:
        record_id, _ = await _seed(session, state="transcribing")
        await session.execute(
            Recording.__table__.update()
            .where(Recording.id == record_id)
            .values(updated_at=datetime.now(UTC) - timedelta(days=2))
        )
        await session.commit()
        _stub_cycle(monkeypatch, [])

        async def _get_job(job_id: str, **kwargs: object) -> TranscriptJob:
            return TranscriptJob(job_id=job_id, status="queued")

        monkeypatch.setattr(transcriber, "get_job", _get_job)

        summary = await transcription.sweep(session)

        assert summary["requeued_stuck"] == 0
        assert (await session.get(Recording, record_id)).state == "transcribing"


async def test_sweep_requeues_a_stale_pending_recording(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Closes the old hole: a submission past its retry budget looked like a new one."""
    async with session_factory() as session:
        record_id, _ = await _seed(session, state="pending")
        await session.execute(
            Recording.__table__.update()
            .where(Recording.id == record_id)
            .values(updated_at=datetime.now(UTC) - timedelta(hours=3))
        )
        await session.commit()
        _stub_cycle(monkeypatch, [])
        enqueued: list[str] = []

        async def _enqueue(rid: str) -> None:
            enqueued.append(rid)

        monkeypatch.setattr(task_queue, "enqueue_transcription", _enqueue)

        summary = await transcription.sweep(session)

        assert summary["requeued_pending"] == 1
        assert enqueued == [record_id]


async def test_sweep_ignores_a_freshly_pending_recording(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with session_factory() as session:
        await _seed(session, state="pending")
        _stub_cycle(monkeypatch, [])
        enqueued: list[str] = []

        async def _enqueue(rid: str) -> None:
            enqueued.append(rid)

        monkeypatch.setattr(task_queue, "enqueue_transcription", _enqueue)

        summary = await transcription.sweep(session)

        assert summary["requeued_pending"] == 0
        assert enqueued == []


# --- the routes ------------------------------------------------------------


async def test_callback_requires_the_secret_header(client: AsyncClient) -> None:
    response = await client.post("/internal/transcripts-ready", json={"event": "results_ready"})
    assert response.status_code == 401


async def test_callback_rejects_a_wrong_secret(client: AsyncClient) -> None:
    response = await client.post(
        "/internal/transcripts-ready",
        json={"event": "results_ready"},
        headers={"X-Callback-Token": "wrong"},
    )
    assert response.status_code == 401


async def test_callback_accepts_the_right_secret(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_cycle(monkeypatch, [])
    response = await client.post(
        "/internal/transcripts-ready",
        json={"event": "results_ready", "job_id": "j", "results_waiting": 0},
        headers={"X-Callback-Token": SECRET},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_callback_fails_closed_when_no_secret_is_configured(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unset must reject, not open -- unlike the other /internal routes.

    503 rather than 401 so the box retries: a missing secret is a deploy away
    from being fixed, while a wrong one will not improve with hammering.
    """
    import app.routers.internal as internal_router

    monkeypatch.setattr(internal_router, "get_settings", lambda: Settings(_env_file=None))
    response = await client.post(
        "/internal/transcripts-ready",
        json={"event": "results_ready"},
        headers={"X-Callback-Token": SECRET},
    )
    assert response.status_code == 503


async def test_callback_tolerates_unknown_body_fields(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 422 would read as a 4xx to the box, which does not retry those."""
    _stub_cycle(monkeypatch, [])
    response = await client.post(
        "/internal/transcripts-ready",
        json={"event": "results_ready", "something_new": 1},
        headers={"X-Callback-Token": SECRET},
    )
    assert response.status_code == 200


async def test_sweep_route_requires_the_secret(client: AsyncClient) -> None:
    assert (await client.post("/internal/transcripts-sweep")).status_code == 401


async def test_sweep_route_runs_with_the_secret(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_cycle(monkeypatch, [])
    response = await client.post(
        "/internal/transcripts-sweep", headers={"X-Callback-Token": SECRET}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
