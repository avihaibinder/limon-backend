"""Internal worker routes. Mounted at the root (no /api/v1) and without the
user-JWT gate: these are called by Cloud Tasks / Pub/Sub, not the app.

The chain (decision 6): GCS object-finalize -> Pub/Sub push -> POST /internal/uploaded
-> Cloud Task -> POST /internal/transcribe. There is no dev shim: the pipeline is
validated on deployed prod (decision 17), with per-hop STEP= log markers
(app/core/logging.py) providing the stepping-stone visibility.
"""

import base64
import binascii
import json
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import step
from app.dependencies import SessionDep
from app.models.recording import Recording
from app.schemas.pubsub import PubSubEnvelope, PubSubMessage
from app.schemas.tagging import TagTask
from app.schemas.transcription import ResultsReadyCallback, TranscribeTask
from app.services import storage, tagging, task_queue, transcription
from app.services.storage import record_id_from_audio_key

router = APIRouter(prefix="/internal", tags=["internal"])


async def require_internal_auth(
    x_internal_token: Annotated[str | None, Header(alias="X-Internal-Token")] = None,
) -> None:
    """Interim guard for the internal endpoints.

    Production auth is OIDC (Cloud Tasks for /transcribe, the Pub/Sub push
    subscription for /uploaded): a Google-signed JWT verified against Google's
    certs with an expected audience; that is wired at deploy. Until then: if
    LIMON_INTERNAL_TASK_TOKEN is set we require a matching header; if unset, the
    endpoints are open (local dev only).
    """
    expected = get_settings().internal_task_token
    if expected is None:
        return
    if x_internal_token != expected:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


async def require_callback_auth(request: Request) -> None:
    """Authenticate the transcriber box on its callback. **Fails closed.**

    Unlike ``require_internal_auth``, an unset secret here rejects rather than
    opens: the box's contract requires this endpoint to authenticate, and its
    caller can always supply the header, so there is no dev convenience to buy.

    The two rejections are deliberately different codes, because the box treats
    them differently. ``503`` is a 5xx, which it retries five times over about a
    minute -- right for "not configured yet", which a deploy will fix. ``401`` is
    a 4xx, which it does **not** retry -- right for a wrong secret, since
    hammering a misconfigured endpoint helps nobody. Both are safe: a callback
    that never lands deletes nothing, and the next one that does land collects
    everything that piled up behind it.
    """
    settings = get_settings()
    expected = settings.transcriber_callback_secret
    if not expected:
        step("callback_rejected", reason="secret_not_configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Callback secret is not configured",
        )
    presented = request.headers.get(settings.transcriber_callback_header) or ""
    if not secrets.compare_digest(presented, expected):
        step("callback_rejected", reason="bad_secret")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


@router.post("/transcripts-ready")
async def transcripts_ready(
    session: SessionDep,
    payload: ResultsReadyCallback,
    _auth: Annotated[None, Depends(require_callback_auth)],
) -> JSONResponse:
    """The transcriber box telling us results are waiting.

    The body names a job; we deliberately ignore it and drain **everything**. An
    earlier callback may have been lost and this one is the first wakeup since,
    so the job named here is not necessarily the only one ready -- or even one we
    still care about.

    The drain runs inside this request rather than after it. Cloud Run throttles
    CPU outside a request, so work deferred past the response is not guaranteed
    to run, and the wakeup would be wasted.

    **This is also where recovery happens**, which is why it calls ``sweep``
    rather than ``collect_transcripts``: this wakeup is the only clock the
    service has, and there is deliberately no scheduler behind it
    (``transcription.sweep``).

    Always ``200``. The cycle is internally idempotent and anything it could not
    store stays on the box, so there is nothing a retry of this call would fix
    that the next drain will not.
    """
    step(
        "callback_received",
        jobId=payload.job_id,
        waiting=payload.results_waiting,
        queueEmpty=payload.queue_empty,
    )
    summary = await transcription.sweep(session)
    return JSONResponse({"status": "ok", **summary}, status_code=200)


@router.post("/transcripts-sweep")
async def transcripts_sweep(
    session: SessionDep,
    _auth: Annotated[None, Depends(require_callback_auth)],
) -> JSONResponse:
    """The same cycle the callback runs, exposed as a manual handle.

    **Nothing calls this on a schedule.** Recovery normally rides on the callback
    above; this is here for the times you want it now -- after fixing a rotated
    tunnel URL, say, rather than waiting for the next recording to trigger it.
    Shares the callback's authentication because it does the same work and has
    the same blast radius.
    """
    summary = await transcription.sweep(session)
    return JSONResponse({"status": "ok", **summary}, status_code=200)


@router.post("/transcribe")
async def transcribe(
    session: SessionDep,
    payload: TranscribeTask,
    _auth: Annotated[None, Depends(require_internal_auth)],
) -> JSONResponse:
    outcome = await transcription.run_transcription(session, payload.record_id)
    if outcome.status == "retry":
        headers = {}
        if outcome.retry_after is not None:
            headers["Retry-After"] = str(int(outcome.retry_after))
        return JSONResponse({"status": "retry"}, status_code=503, headers=headers)
    return JSONResponse({"status": outcome.status}, status_code=200)


@router.post("/tag")
async def tag(
    session: SessionDep,
    payload: TagTask,
    _auth: Annotated[None, Depends(require_internal_auth)],
) -> JSONResponse:
    outcome = await tagging.run_tagging(session, payload.event_id)
    if outcome.status == "retry":
        headers = {}
        if outcome.retry_after is not None:
            headers["Retry-After"] = str(int(outcome.retry_after))
        return JSONResponse({"status": "retry"}, status_code=503, headers=headers)
    return JSONResponse({"status": outcome.status}, status_code=200)


@router.post("/uploaded")
async def uploaded(
    session: SessionDep,
    envelope: PubSubEnvelope,
    _auth: Annotated[None, Depends(require_internal_auth)],
) -> Response:
    """GCS object-finalize notification (Pub/Sub push): enqueue a transcription task.

    GCS publishes OBJECT_FINALIZE to a Pub/Sub topic and a push subscription
    delivers it here. We recover ``recordId`` from the object name
    (``v0/{userId}/{recordId}.m4a``) and enqueue one Cloud Task -> POST
    /internal/transcribe. Tiny and idempotent: duplicate finalize notifications are
    safe because the worker's atomic claim dedupes.

    We ACK (204) once the message is well-formed but we chose not to act on it
    (wrong event type, or a key that is not one of our audio objects) so Pub/Sub
    does not redeliver a message we will never act on. An enqueue *failure*, by
    contrast, is allowed to propagate (5xx) so Pub/Sub redelivers and the enqueue
    is retried.
    """
    message = envelope.message
    event_type = message.attributes.get("eventType")
    object_name = _object_name(message)

    # Each early return is ACKed (204) so Pub/Sub stops redelivering a message we
    # will never act on. The STEP= markers make the otherwise-identical 204s
    # distinguishable in the logs: today the blind spot is that a dropped message
    # looks the same as an enqueued one.
    if event_type != "OBJECT_FINALIZE":
        step("finalize_ignored", reason="event_type", eventType=event_type)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    if object_name is None:
        step("finalize_ignored", reason="no_object_name")
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    record_id = record_id_from_audio_key(object_name)
    if record_id is None:
        step("finalize_ignored", reason="non_audio_key", objectId=object_name)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    recording = await session.scalar(
        select(Recording).where(
            Recording.id == record_id,
            Recording.storage_key == object_name,
        )
    )
    if recording is None:
        # A signed upload can finish after account deletion. Remove only the
        # exact object named by the trusted finalize notification and do not
        # recreate work for a row that no longer exists.
        await storage.delete_orphan_audio_object(object_name)
        step("finalize_orphan_deleted", recordId=record_id, objectId=object_name)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # Stepping stone 3: a finalize for one of our audio objects reached us.
    step("finalize_received", recordId=record_id, objectId=object_name)
    # An enqueue *failure* is allowed to propagate (5xx) so Pub/Sub redelivers; the
    # "task_enqueued" marker is therefore only emitted on success (stone 4).
    await task_queue.enqueue_transcription(record_id)
    step("task_enqueued", recordId=record_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _object_name(message: PubSubMessage) -> str | None:
    """The GCS object name from a finalize notification.

    GCS carries it in the ``objectId`` attribute and again in the base64 ``data``
    payload (the object-resource JSON, as ``name``). Prefer the attribute; fall
    back to the payload; return ``None`` if neither yields a usable name.
    """
    name = message.attributes.get("objectId")
    if name:
        return name
    if message.data:
        try:
            payload = json.loads(base64.b64decode(message.data))
        except (ValueError, binascii.Error):
            return None
        name = payload.get("name") if isinstance(payload, dict) else None
        if isinstance(name, str) and name:
            return name
    return None
