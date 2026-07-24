"""Transcription worker logic: claim a recording, transcribe it, write the result.

Driven by ``POST /internal/transcribe`` (Cloud Tasks). Idempotent under
at-least-once delivery: a retry after success is a no-op. Never logs audio bytes
or transcript text.

Outcomes map to HTTP status at the router: ``noop``/``done``/``failed`` -> 2xx
(the queue must not retry), ``retry`` -> 503 (retryable within the capped budget).
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import step
from app.models.event import Event
from app.models.recording import Recording
from app.services import audio_storage, task_queue, transcriber
from app.services.task_queue import TaskQueueError
from app.services.transcriber import (
    AudioRejectedError,
    AudioTooLargeError,
    EndpointBusyError,
    EndpointUnavailableError,
    TranscriberError,
    TranscriberNotConfiguredError,
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Outcome:
    status: str  # noop | done | failed | retry
    retry_after: float | None = None


async def run_transcription(session: AsyncSession, record_id: str) -> Outcome:
    recording = await session.get(Recording, record_id)
    if recording is None:
        step("noop", recordId=record_id, reason="no_recording")
        return Outcome("noop")
    if recording.state == "done":
        step("noop", recordId=record_id, reason="already_done")
        return Outcome("noop")

    # Atomic claim: pending|failed -> transcribing. If nothing was updated, another
    # worker already claimed it (or it just finished), so no-op.
    claim = await session.execute(
        update(Recording)
        .where(Recording.id == record_id, Recording.state.in_(("pending", "failed")))
        .values(state="transcribing", updated_at=_utcnow())
    )
    await session.commit()
    if claim.rowcount == 0:
        step("noop", recordId=record_id, reason="claim_lost")
        return Outcome("noop")

    # Stepping stone 5: this worker owns the recording and will transcribe it.
    step("claimed", recordId=record_id)

    event = await session.scalar(select(Event).where(Event.recording_id == record_id))
    if event is None:
        # Data-integrity problem, not transient: fail permanently (no retry).
        step("failed", recordId=record_id, reason="no_event")
        await _mark_failed(session, record_id, "No event linked to recording")
        return Outcome("failed")

    try:
        audio = await audio_storage.download(recording.storage_key)
    except audio_storage.AudioNotFoundError:
        step("failed", recordId=record_id, reason="audio_not_found")
        await _mark_failed(session, record_id, "Audio object not found")
        return Outcome("failed")
    except audio_storage.AudioStorageNotConfiguredError:
        # Cannot read right now; retry rather than lose the record.
        step("retry", recordId=record_id, reason="storage_not_configured")
        await _revert_pending(session, record_id)
        return Outcome("retry")

    try:
        result = await transcriber.transcribe(
            audio,
            filename=f"{record_id}.m4a",
            content_type=recording.content_type,
        )
    except (AudioRejectedError, AudioTooLargeError) as exc:
        # Hard failure: the audio itself is the problem. Do not retry.
        step("failed", recordId=record_id, reason=type(exc).__name__)
        await _mark_failed(session, record_id, type(exc).__name__)
        return Outcome("failed")
    except EndpointBusyError as exc:
        step("retry", recordId=record_id, reason="endpoint_busy")
        await _revert_pending(session, record_id)
        return Outcome("retry", retry_after=exc.retry_after)
    except (EndpointUnavailableError, TranscriberNotConfiguredError, TranscriberError) as exc:
        # Endpoint down / not configured / unexpected: soft, retry within budget.
        step("retry", recordId=record_id, reason=type(exc).__name__)
        await _revert_pending(session, record_id)
        return Outcome("retry")

    # Success: write the transcript onto the event and mark the recording done.
    # description going non-null + updated_at bumping is the FE's Realtime signal.
    event.description = result.text
    recording.state = "done"
    recording.error = None
    await session.commit()
    # Stepping stone 6: transcript written; the FE gets it over Realtime.
    step("transcribed", recordId=record_id, chars=len(result.text))

    if not event.tag_ids:
        # Best-effort: an enqueue failure here must not turn a successful
        # transcription into a "retry" outcome (that would re-run transcription
        # over an unrelated tagging problem).
        try:
            await task_queue.enqueue_tagging(event.id)
        except TaskQueueError as exc:
            step("tagging_enqueue_failed", recordId=record_id, reason=type(exc).__name__)

    return Outcome("done")


async def _mark_failed(session: AsyncSession, record_id: str, error: str) -> None:
    await session.execute(
        update(Recording)
        .where(Recording.id == record_id)
        .values(state="failed", error=error[:2000], updated_at=_utcnow())
    )
    await session.commit()


async def _revert_pending(session: AsyncSession, record_id: str) -> None:
    await session.execute(
        update(Recording)
        .where(Recording.id == record_id)
        .values(state="pending", updated_at=_utcnow())
    )
    await session.commit()
