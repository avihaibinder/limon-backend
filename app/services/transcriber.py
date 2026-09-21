"""Client for the Hebrew transcriber's async job API (the Oracle VPS box).

The box is always on but slower than real time, so it does not answer a
transcription in the request that submits it. The cycle is **submit, be woken by
a callback, drain everything ready, persist, acknowledge** -- and the ack is the
only thing that deletes a result, which is why persisting first is not a
preference. See ``spec/transcription.md``; the box's own contract lives in
``hebrew-transcriber/spec/job-api.md``.

This module is only the wire: it knows routes, status codes and response shapes.
The ordering rules and everything touching the database live in
``services/transcription.py``.

Three things here are load bearing and look like details:

- The drain asks for ``include=text``. **An unknown query parameter is ignored,
  not rejected**, so ``include_text=true`` returns 200 with no transcripts and no
  error -- a silent N+1 at best and silent data loss at worst.
- The drain passes ``limit``. The box's default is 50 against a cap of 200, and a
  truncated page says nothing about having truncated.
- A failed job carries a nested ``error`` object. There is no flat ``error_code``
  on the wire; that is a column name in the box's database.

Never log audio bytes or transcript text from here.
"""

from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.config import get_settings

# The box's MAX_LIST_LIMIT. Values above it are clamped rather than rejected, but
# asking for exactly it keeps the intent legible.
MAX_LIST_LIMIT = 200


@dataclass(frozen=True)
class TranscriptJob:
    """One job as the box reports it.

    ``text`` and ``segments`` are populated only for ``done``; ``error_code`` and
    ``error_message`` only for ``failed``.
    """

    job_id: str
    status: str  # queued | running | done | failed
    text: str | None = None
    segments: list[dict[str, Any]] = field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    error_retryable: bool = False
    audio_duration_s: float | None = None
    transcription_time_s: float | None = None
    rtf: float | None = None

    @property
    def is_done(self) -> bool:
        return self.status == "done"

    @property
    def is_failed(self) -> bool:
        return self.status == "failed"


@dataclass(frozen=True)
class DrainPage:
    """One page of the drain, plus the envelope counts that detect truncation.

    ``done_unacked``/``failed_unacked`` are global to the box and are only a
    valid completeness check for an **unfiltered** drain; with an ``ids`` filter
    they still count everything waiting, not everything matching.
    """

    jobs: list[TranscriptJob]
    done_unacked: int = 0
    failed_unacked: int = 0

    @property
    def waiting(self) -> int:
        return self.done_unacked + self.failed_unacked


@dataclass(frozen=True)
class SubmitResult:
    """The 202 (or idempotent 200) from ``POST /jobs``."""

    job_id: str
    status: str
    already_existed: bool
    audio_duration_s: float | None = None
    queue_position: int | None = None
    poll_after_s: float | None = None


class TranscriberError(RuntimeError):
    """Base class for transcription client failures."""


class TranscriberNotConfiguredError(TranscriberError):
    """Base URL/token are unset; treat as unavailable, not a caller error."""


class EndpointUnavailableError(TranscriberError):
    """Connection refused / timeout / DNS. Soft (retry).

    **This is the expected failure when the Quick Tunnel URL has rotated**, which
    happens whenever the box's tunnel restarts. It is an operating condition, not
    an incident: the backstop sweep re-drives whatever piled up once the URL is
    corrected.
    """


class EndpointBusyError(TranscriberError):
    """503: the box's queue already holds MAX_QUEUE_AUDIO_S of audio. Soft.

    Not "the box is down" -- nothing already accepted is disturbed. Carries
    ``retry_after`` when the box supplied one.
    """

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class AudioRejectedError(TranscriberError):
    """400: undecodable, non-audio, empty, or a job_id outside 1..200 chars. Hard."""


class AudioTooLargeError(TranscriberError):
    """413: over MAX_UPLOAD_MB *or* over MAX_AUDIO_DURATION_S. Hard either way.

    The box does not distinguish the two and a caller cannot tell them apart
    without string-matching the message. We do not try: both are permanent, and
    both remedies (re-encode, split) are work this backend does not do. We
    pre-flight both limits instead, so reaching here means either the client sent
    no duration or our configured limits have drifted from the box's.
    """


class JobConflictError(TranscriberError):
    """409: this job_id exists on the box with *different* audio.

    Not a retry. The same id with the same bytes is a free, idempotent 200.
    """


class UnauthorizedError(TranscriberError):
    """401: missing or wrong AUTH_TOKEN. A configuration error, not a transient."""


def _config() -> tuple[str, str]:
    settings = get_settings()
    url = settings.transcriber_base_url
    token = settings.transcriber_token
    if not url or not token:
        raise TranscriberNotConfiguredError(
            "Transcriber is not configured "
            "(set LIMON_TRANSCRIBER_BASE_URL and LIMON_TRANSCRIBER_TOKEN)."
        )
    return url.rstrip("/"), token


def _headers(token: str) -> dict[str, str]:
    # Header only, never a query parameter: a token in a URL lands in logs.
    return {"Authorization": f"Bearer {token}"}


async def _request(
    client: httpx.AsyncClient | None,
    method: str,
    path: str,
    **kwargs: Any,
) -> httpx.Response:
    """One authenticated call, with transport errors normalized to soft failures."""
    url, token = _config()
    headers = {**_headers(token), **kwargs.pop("headers", {})}

    async def _send(http: httpx.AsyncClient) -> httpx.Response:
        try:
            return await http.request(method, url + path, headers=headers, **kwargs)
        except httpx.TransportError as exc:
            raise EndpointUnavailableError(
                f"Transcriber unreachable: {type(exc).__name__}"
            ) from exc

    if client is not None:
        return await _send(client)
    async with httpx.AsyncClient(timeout=get_settings().transcriber_timeout_s) as owned:
        return await _send(owned)


async def submit(
    job_id: str,
    audio: bytes,
    *,
    filename: str = "audio.m4a",
    content_type: str = "audio/mp4",
    client: httpx.AsyncClient | None = None,
) -> SubmitResult:
    """Queue ``audio`` under ``job_id``. 202 on accept, 200 on an idempotent hit.

    ``job_id`` is ours to choose and must be unguessable: the box has one shared
    token for every caller, so a predictable id would let any token-holder walk
    ids and read transcripts. We pass ``recordings.id`` (a UUID4).

    The box detects the audio type itself -- filename and content type are not
    consulted -- so no conversion or demuxing happens on this side.
    """
    response = await _request(
        client,
        "POST",
        "/jobs",
        files={"file": (filename, audio, content_type)},
        data={"job_id": job_id},
    )
    if response.status_code in (200, 202):
        body = response.json()
        return SubmitResult(
            job_id=body.get("job_id", job_id),
            status=body.get("status", "queued"),
            already_existed=response.status_code == 200,
            audio_duration_s=body.get("audio_duration_s"),
            queue_position=body.get("queue_position"),
            poll_after_s=body.get("poll_after_s"),
        )
    _raise_for_status(response)
    raise AssertionError("unreachable")  # pragma: no cover


async def drain(
    *,
    ids: list[str] | None = None,
    limit: int = MAX_LIST_LIMIT,
    client: httpx.AsyncClient | None = None,
) -> DrainPage:
    """Everything terminal the box is holding, in ONE request.

    ``include=text`` is the parameter name and getting it wrong is silent: an
    unknown query parameter is ignored, so ``include_text=true`` returns 200 with
    every transcript missing and no error to notice.

    ``ids`` filters to jobs we submitted. Production drains unfiltered, because a
    callback we never received means the box may be holding results this process
    has never heard of. Filtering is for sharing the box with another caller,
    where an unfiltered ack would delete *their* results.
    """
    params: dict[str, Any] = {
        "status": "done,failed",
        "include": "text",
        "limit": limit,
    }
    if ids:
        params["ids"] = ",".join(ids)
    response = await _request(client, "GET", "/jobs", params=params)
    if response.status_code != 200:
        _raise_for_status(response)
    body = response.json()
    return DrainPage(
        jobs=[_parse_job(j) for j in body.get("jobs", [])],
        done_unacked=body.get("done_unacked", 0),
        failed_unacked=body.get("failed_unacked", 0),
    )


async def ack(job_ids: list[str], *, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Confirm receipt. **This is what deletes a result on the box.**

    Only ever call this for ids whose outcome is durably stored -- or that we
    have decided there is nothing to store for. Acknowledging before the write
    commits is the one way to lose a transcript permanently.
    """
    if not job_ids:
        return {"deleted": [], "not_found": []}
    if len(job_ids) > MAX_LIST_LIMIT:
        raise ValueError(f"at most {MAX_LIST_LIMIT} ids per ack call")
    response = await _request(client, "POST", "/jobs/ack", json={"job_ids": job_ids})
    if response.status_code != 200:
        _raise_for_status(response)
    return response.json()


async def get_job(job_id: str, *, client: httpx.AsyncClient | None = None) -> TranscriptJob | None:
    """One job, or ``None`` if the box does not have it.

    A 404 is genuinely ambiguous -- unknown, already acknowledged, or expired
    past JOB_TTL_DAYS -- and the caller cannot tell which. For the sweep they
    amount to the same thing: the box is not going to produce this transcript, so
    resubmit. Do not use this in the drain loop; that is what ``drain`` is for.
    """
    response = await _request(client, "GET", f"/jobs/{job_id}")
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        _raise_for_status(response)
    return _parse_job(response.json())


async def discard(job_id: str, *, client: httpx.AsyncClient | None = None) -> None:
    """Cancel a queued job or drop a finished one. 404 is success here."""
    response = await _request(client, "DELETE", f"/jobs/{job_id}")
    if response.status_code in (204, 404):
        return
    _raise_for_status(response)


def _parse_job(payload: dict[str, Any]) -> TranscriptJob:
    # `error` is an object. There is no flat `error_code` on the wire -- reading
    # one yields None on every failed job and silently classifies every permanent
    # failure as unrecognised.
    error = payload.get("error") or {}
    return TranscriptJob(
        job_id=payload["job_id"],
        status=payload.get("status", "unknown"),
        text=payload.get("text"),
        segments=payload.get("segments") or [],
        error_code=error.get("code"),
        error_message=error.get("message"),
        error_retryable=bool(error.get("retryable")),
        audio_duration_s=payload.get("audio_duration_s"),
        transcription_time_s=payload.get("transcription_time_s"),
        rtf=payload.get("rtf"),
    )


def _raise_for_status(response: httpx.Response) -> None:
    code = response.status_code
    # Deliberately do not echo the response body: it may carry box internals.
    if code == 503:
        raise EndpointBusyError(
            "Transcriber queue is full (503)", retry_after=_parse_retry_after(response)
        )
    if code == 401:
        raise UnauthorizedError("Transcriber rejected our token (401)")
    if code == 409:
        raise JobConflictError("This job_id exists on the box with different audio (409)")
    if code == 400:
        raise AudioRejectedError("Transcriber rejected the submission (400)")
    if code == 413:
        raise AudioTooLargeError("Audio is over the transcriber's limits (413)")
    raise TranscriberError(f"Transcriber returned unexpected status {code}")


def _parse_retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        # HTTP-date form: let the caller fall back to its own backoff.
        return None
