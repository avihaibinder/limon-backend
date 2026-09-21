"""Transcription domain logic against the box's async job API.

Three entry points, one per trigger:

- ``run_transcription``  -- Cloud Tasks, via ``POST /internal/transcribe``.
  Claims a recording and **submits** it. The transcript does not arrive here.
- ``collect_transcripts`` -- the box's callback, via
  ``POST /internal/transcripts-ready``. Drains, persists, then acknowledges.
- ``sweep``              -- Cloud Scheduler, via ``POST /internal/transcripts-sweep``.
  The backstop for a callback that never landed and for work that never got out.

The ordering rule that governs all of this: **persist, then acknowledge.** The
ack is the only thing that deletes a result on the box, so a crash between the
two loses a transcript permanently, while a crash the other way round costs one
redundant drain. Everything else is forgiving -- a lost callback, a failed drain
and a dead instance all leave the results sitting on the box.

Never log audio bytes or transcript text.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import step
from app.models.event import Event
from app.models.recording import Recording
from app.services import audio_storage, tagging, task_queue, transcriber
from app.services.task_queue import TaskQueueError
from app.services.transcriber import (
    AudioRejectedError,
    AudioTooLargeError,
    EndpointBusyError,
    EndpointUnavailableError,
    JobConflictError,
    TranscriberError,
    TranscriberNotConfiguredError,
    TranscriptJob,
    UnauthorizedError,
)

# A guard on the drain loop rather than a tuning knob. Without it, a page whose
# every persist fails leaves the box's counts high and the loop spinning forever.
MAX_DRAIN_PAGES = 10


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Outcome:
    """Result of one submission attempt.

    ``submitted`` and ``done`` both mean "the queue must not retry"; they are
    distinct only so the logs can tell "handed to the box" from "already had a
    transcript".
    """

    status: str  # noop | submitted | done | failed | retry
    retry_after: float | None = None


@dataclass
class CollectionResult:
    written: int = 0
    failed: int = 0
    skipped: int = 0
    unknown: int = 0
    acked: list[str] = field(default_factory=list)
    pages: int = 0


# --- submission ------------------------------------------------------------


async def run_transcription(session: AsyncSession, record_id: str) -> Outcome:
    """Claim a recording and hand its audio to the box.

    Idempotent under at-least-once delivery. The atomic claim is what makes a
    duplicate Cloud Task a no-op, and it is also why a ``409`` from the box is
    nearly unreachable: a second task for the same recording cannot get past it
    to submit different bytes.

    ``transcribing`` here means **"submitted, waiting for a callback"**, which
    can last as long as the box's queue is deep. The Cloud Tasks retry budget
    therefore covers submission only; the transcription itself is under no
    deadline at all.
    """
    recording = await session.get(Recording, record_id)
    if recording is None:
        step("noop", recordId=record_id, reason="no_recording")
        return Outcome("noop")
    if recording.state == "done":
        step("noop", recordId=record_id, reason="already_done")
        return Outcome("noop")

    claim = await session.execute(
        update(Recording)
        .where(Recording.id == record_id, Recording.state.in_(("pending", "failed")))
        .values(state="transcribing", updated_at=_utcnow())
    )
    await session.commit()
    if claim.rowcount == 0:
        step("noop", recordId=record_id, reason="claim_lost")
        return Outcome("noop")

    step("claimed", recordId=record_id)

    event = await session.scalar(select(Event).where(Event.recording_id == record_id))
    if event is None:
        step("failed", recordId=record_id, reason="no_event")
        await _mark_failed(session, record_id, "No event linked to recording")
        return Outcome("failed")

    settings = get_settings()

    # Pre-flight the duration before spending an upload. `duration_sec` is
    # client-supplied and nullable, so this catches the common case and not every
    # case; a recording with no stated duration still reaches the box's own 413.
    if (
        recording.duration_sec is not None
        and recording.duration_sec > settings.transcriber_max_audio_duration_s
    ):
        step("failed", recordId=record_id, reason="too_long", durationSec=recording.duration_sec)
        await _mark_failed(session, record_id, "Recording is longer than the transcriber allows")
        return Outcome("failed")

    try:
        audio = await audio_storage.download(recording.storage_key)
    except audio_storage.AudioNotFoundError:
        step("failed", recordId=record_id, reason="audio_not_found")
        await _mark_failed(session, record_id, "Audio object not found")
        return Outcome("failed")
    except audio_storage.AudioStorageNotConfiguredError:
        step("retry", recordId=record_id, reason="storage_not_configured")
        await _revert_pending(session, record_id)
        return Outcome("retry")

    if len(audio) > settings.transcriber_max_upload_bytes:
        # Should be unreachable: the signed-URL cap is the same number, enforced
        # by GCS at the signature. Reaching it means the two have drifted apart.
        step("failed", recordId=record_id, reason="too_large", bytes=len(audio))
        await _mark_failed(session, record_id, "Audio is larger than the transcriber allows")
        return Outcome("failed")

    try:
        await _submit_with_conflict_recovery(record_id, audio, recording.content_type)
    except (AudioRejectedError, AudioTooLargeError, JobConflictError) as exc:
        step("failed", recordId=record_id, reason=type(exc).__name__)
        await _mark_failed(session, record_id, type(exc).__name__)
        return Outcome("failed")
    except EndpointBusyError as exc:
        step("retry", recordId=record_id, reason="queue_full")
        await _revert_pending(session, record_id)
        return Outcome("retry", retry_after=exc.retry_after)
    except UnauthorizedError:
        # Configuration, not weather. Retry so the sweep can pick it up once the
        # token is fixed, but say plainly in the logs that retrying will not help.
        step("retry", recordId=record_id, reason="bad_token_check_configuration")
        await _revert_pending(session, record_id)
        return Outcome("retry")
    except (EndpointUnavailableError, TranscriberNotConfiguredError, TranscriberError) as exc:
        step("retry", recordId=record_id, reason=type(exc).__name__)
        await _revert_pending(session, record_id)
        return Outcome("retry")

    # The row stays `transcribing`; the transcript arrives over the callback.
    step("submitted", recordId=record_id, bytes=len(audio))
    return Outcome("submitted")


async def _submit_with_conflict_recovery(record_id: str, audio: bytes, content_type: str) -> None:
    """Submit, and on a 409 discard the box's copy and submit once more.

    A 409 means the box holds this id with *different* audio, which the atomic
    claim above makes very hard to reach. It is handled anyway because the cost
    is three lines and the cost of being wrong is a recording that never
    transcribes and never explains why. The new audio wins in every sub-case: it
    is what is in GCS now.
    """
    try:
        await transcriber.submit(
            record_id, audio, filename=f"{record_id}.m4a", content_type=content_type
        )
        return
    except JobConflictError:
        step("job_conflict", recordId=record_id, action="discard_and_resubmit")

    await transcriber.discard(record_id)
    await transcriber.submit(
        record_id, audio, filename=f"{record_id}.m4a", content_type=content_type
    )


# --- collection ------------------------------------------------------------


async def collect_transcripts(
    session: AsyncSession, *, ids: list[str] | None = None
) -> CollectionResult:
    """Drain everything ready, persist it, then acknowledge it.

    Called by the box's callback and by the sweep, and deliberately identical in
    both: the callback is a nudge, so what it triggers is the same cycle a timer
    would have run. It never fetches the job the callback named -- an earlier
    callback may have been lost and this one is the first wakeup since, so there
    may be results here that no callback ever announced.

    Safe to run concurrently with itself. Two Cloud Run instances can be woken by
    two callbacks and drain the same jobs; the conditional update below decides
    which one writes, and a duplicate ack is a no-op on the box.
    """
    result = CollectionResult()
    settings = get_settings()

    async with httpx.AsyncClient(timeout=settings.transcriber_timeout_s) as client:
        for _ in range(MAX_DRAIN_PAGES):
            page = await transcriber.drain(ids=ids, client=client)
            result.pages += 1
            if not page.jobs:
                break

            ackable: list[str] = []
            for job in page.jobs:
                if await _persist(session, job, result):
                    ackable.append(job.job_id)

            if ackable:
                # Only now, and only for jobs whose outcome is durably stored.
                await transcriber.ack(ackable, client=client)
                result.acked.extend(ackable)

            if not ackable:
                # Nothing could be stored, so nothing was deleted and the next
                # page would be identical. Stop rather than spin.
                step("collect_stalled", waiting=page.waiting, page=len(page.jobs))
                break

            # The envelope's counts are the authoritative measure of what is
            # left, which beats inferring completeness from a short page. They
            # are global, so they only mean this for an unfiltered drain.
            if ids is not None or len(page.jobs) >= page.waiting:
                break

    step(
        "collected",
        written=result.written,
        failed=result.failed,
        skipped=result.skipped,
        unknown=result.unknown,
        acked=len(result.acked),
    )
    return result


async def _persist(session: AsyncSession, job: TranscriptJob, result: CollectionResult) -> bool:
    """Store one job's outcome. Returns whether it may now be acknowledged.

    ``False`` means the write failed and the job must stay on the box -- it will
    come back on the next drain. Returning ``True`` for a job we decided not to
    store (no such recording) is deliberate: otherwise it sits on the box until
    the 30-day TTL, and a TTL doing real work is a bug in this caller.
    """
    try:
        recording = await session.get(Recording, job.job_id)
        if recording is None:
            # The account or the event was deleted while the box worked. There is
            # nothing to store and nobody to tell.
            step("collect_unknown", recordId=job.job_id)
            result.unknown += 1
            return True

        if job.is_failed:
            return await _persist_failure(session, job, result)
        if job.is_done:
            return await _persist_transcript(session, job, result)

        # Not terminal: we asked for done,failed only, so this is the box
        # disagreeing with its own filter. Leave it alone rather than ack it.
        step("collect_unexpected_status", recordId=job.job_id, status=job.status)
        return False
    except Exception as exc:  # noqa: BLE001 - one bad row must not strand the page
        await session.rollback()
        step("collect_error", recordId=job.job_id, reason=type(exc).__name__)
        return False


async def _persist_transcript(
    session: AsyncSession, job: TranscriptJob, result: CollectionResult
) -> bool:
    event = await session.scalar(select(Event).where(Event.recording_id == job.job_id))
    if event is None:
        step("collect_no_event", recordId=job.job_id)
        await _mark_failed(session, job.job_id, "No event linked to recording")
        result.failed += 1
        return True

    # Conditional update, not a read-then-write: this is what makes two
    # concurrent drains safe. Whoever moves the row out of `done` owns the
    # transcript write; the loser skips it and still acknowledges.
    claim = await session.execute(
        update(Recording)
        .where(Recording.id == job.job_id, Recording.state != "done")
        .values(state="done", error=None, updated_at=_utcnow())
    )
    if claim.rowcount == 0:
        await session.rollback()
        step("collect_skipped", recordId=job.job_id, reason="already_done")
        result.skipped += 1
        return True

    # `description` going non-null is the client's completion signal over
    # Realtime (spec/realtime-reads.md).
    event.description = job.text or ""
    await session.commit()
    step("transcribed", recordId=job.job_id, chars=len(job.text or ""))
    result.written += 1

    if not event.tag_ids:
        # Best-effort: a tagging enqueue failure must not make us re-collect a
        # transcript that is already safely stored.
        try:
            await tagging.dispatch_tagging(event.id)
        except TaskQueueError as exc:
            step("tagging_enqueue_failed", recordId=job.job_id, reason=type(exc).__name__)

    return True


async def _persist_failure(
    session: AsyncSession, job: TranscriptJob, result: CollectionResult
) -> bool:
    if job.error_code == "audio_missing":
        # The box's row outlived its file, which should not happen. The contract
        # says resubmit under a new id; acknowledging frees this one, so reverting
        # to `pending` lets the sweep resubmit under the same id.
        step("collect_audio_missing", recordId=job.job_id)
        await _revert_pending(session, job.job_id)
        result.failed += 1
        return True

    # Everything else is permanent by the time we see it: the box has already
    # spent both of its attempts. `error_retryable` describes the failure's
    # nature, not an instruction to resubmit.
    reason = job.error_code or "transcription_failed"
    step("failed", recordId=job.job_id, reason=reason)
    await _mark_failed(session, job.job_id, reason)
    result.failed += 1
    return True


# --- the backstop ----------------------------------------------------------


async def sweep(session: AsyncSession) -> dict[str, int]:
    """The slow safety net, run daily. Three jobs, in order.

    The callback is the fast path and never a guarantee: if every delivery
    attempt fails, the box gives up, and the results simply wait. Something has
    to eventually come and get them, and this is it.
    """
    settings = get_settings()
    collected = await collect_transcripts(session)

    now = _utcnow()
    requeued_stuck = 0
    requeued_pending = 0

    # 2. Submitted, but the box no longer has it. A 404 is ambiguous -- unknown,
    #    acknowledged or expired -- and all three mean the same thing here: no
    #    transcript is coming, so put it back in the queue.
    stuck_cutoff = now - timedelta(hours=settings.transcriber_stale_submitted_hours)
    stuck = await session.scalars(
        select(Recording).where(
            Recording.state == "transcribing", Recording.updated_at < stuck_cutoff
        )
    )
    for recording in stuck.all():
        try:
            job = await transcriber.get_job(recording.id)
        except TranscriberError as exc:
            step("sweep_check_failed", recordId=recording.id, reason=type(exc).__name__)
            continue
        if job is not None:
            # Still queued behind other work. The box is slower than real time by
            # design; a deep queue is not a stall.
            continue
        step("sweep_requeue", recordId=recording.id, reason="box_has_no_job")
        await _revert_pending(session, recording.id)
        if await _enqueue(recording.id):
            requeued_stuck += 1

    # 3. Never got out at all. This is the hole the old design left open: a
    #    submission that outlived its Cloud Tasks budget reverted to `pending`
    #    and became indistinguishable from one that had not started.
    pending_cutoff = now - timedelta(minutes=settings.transcriber_stale_pending_minutes)
    pending = await session.scalars(
        select(Recording).where(Recording.state == "pending", Recording.updated_at < pending_cutoff)
    )
    for recording in pending.all():
        step("sweep_requeue", recordId=recording.id, reason="stale_pending")
        if await _enqueue(recording.id):
            requeued_pending += 1

    summary = {
        "written": collected.written,
        "failed": collected.failed,
        "skipped": collected.skipped,
        "unknown": collected.unknown,
        "acked": len(collected.acked),
        "requeued_stuck": requeued_stuck,
        "requeued_pending": requeued_pending,
    }
    step("swept", **summary)
    return summary


async def _enqueue(record_id: str) -> bool:
    try:
        await task_queue.enqueue_transcription(record_id)
        return True
    except TaskQueueError as exc:
        step("sweep_enqueue_failed", recordId=record_id, reason=type(exc).__name__)
        return False


# --- row helpers -----------------------------------------------------------


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
