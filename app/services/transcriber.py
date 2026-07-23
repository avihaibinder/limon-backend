"""Client for the Nebius Hebrew-transcription endpoint.

The backend integrates with the endpoint over HTTPS: one multipart POST of the
audio bytes, one JSON transcript back. The endpoint forces Hebrew and runs VAD
server-side, so this client sends only the file. See
``spec-local/BACKEND_INTEGRATION.md`` for the endpoint's API and error behavior.

The endpoint is single-flight and down most of the time (raised manually for
tests/demos), so failures are classified into **soft** (retryable: busy or
unreachable) and **hard** (permanent: audio rejected) via the exception types
below, letting the worker (domain 03) decide retry vs. mark-failed.

Never log audio bytes or transcript text from here.
"""

from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import get_settings


@dataclass(frozen=True)
class TranscriptionResult:
    """Parsed success response. Only ``text`` and ``segments`` are needed by the
    worker; the timing fields are kept for throughput/cost logging (numbers only).
    """

    text: str
    segments: list[dict[str, Any]]
    audio_duration_s: float | None = None
    transcription_time_s: float | None = None
    rtf: float | None = None
    params: dict[str, Any] | None = None


class TranscriberError(RuntimeError):
    """Base class for transcription client failures."""


class TranscriberNotConfiguredError(TranscriberError):
    """Endpoint URL/token are unset; treat as unavailable, not a caller error."""


class EndpointUnavailableError(TranscriberError):
    """Connection refused / timeout / DNS: endpoint down or cold. Soft (retry)."""


class EndpointBusyError(TranscriberError):
    """503 single-flight busy. Soft (retry); carries ``retry_after`` if provided."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class AudioRejectedError(TranscriberError):
    """400 undecodable / non-audio / empty input. Hard (do not retry)."""


class AudioTooLargeError(TranscriberError):
    """413 over the endpoint's upload cap. Hard (should not happen given the
    25 MB signed-URL cap, but handled)."""


async def transcribe(
    audio: bytes,
    *,
    filename: str = "audio.m4a",
    content_type: str = "audio/mp4",
    client: httpx.AsyncClient | None = None,
) -> TranscriptionResult:
    """Transcribe ``audio`` via the configured endpoint.

    Pass ``client`` to reuse/inject an ``httpx.AsyncClient`` (used by tests);
    otherwise a short-lived client is created with the configured timeout.
    Raises a ``TranscriberError`` subclass on any non-200 outcome.
    """
    settings = get_settings()
    url = settings.transcriber_endpoint_url
    token = settings.transcriber_endpoint_token
    if not url or not token:
        raise TranscriberNotConfiguredError(
            "Transcriber endpoint is not configured "
            "(set LIMON_TRANSCRIBER_ENDPOINT_URL and LIMON_TRANSCRIBER_ENDPOINT_TOKEN)."
        )

    if client is not None:
        return await _send(client, url, token, audio, filename, content_type)
    async with httpx.AsyncClient(timeout=settings.transcriber_timeout_s) as owned:
        return await _send(owned, url, token, audio, filename, content_type)


async def _send(
    client: httpx.AsyncClient,
    url: str,
    token: str,
    audio: bytes,
    filename: str,
    content_type: str,
) -> TranscriptionResult:
    endpoint = url.rstrip("/") + "/transcribe"
    try:
        response = await client.post(
            endpoint,
            files={"file": (filename, audio, content_type)},
            headers={"Authorization": f"Bearer {token}"},
        )
    except httpx.TransportError as exc:
        # Connection refused, timeout, read/network error: the endpoint is down
        # or cold. Soft failure so the worker leaves the row pending.
        raise EndpointUnavailableError(
            f"Transcriber endpoint unreachable: {type(exc).__name__}"
        ) from exc

    if response.status_code == 200:
        return _parse(response.json())
    _raise_for_status(response)


def _raise_for_status(response: httpx.Response) -> None:
    code = response.status_code
    # Deliberately do not echo the response body: it may leak endpoint internals.
    if code == 503:
        raise EndpointBusyError("Transcriber busy (503)", retry_after=_parse_retry_after(response))
    if code == 400:
        raise AudioRejectedError("Transcriber rejected the audio (400)")
    if code == 413:
        raise AudioTooLargeError("Audio too large for the transcriber (413)")
    raise TranscriberError(f"Transcriber returned unexpected status {code}")


def _parse(payload: dict[str, Any]) -> TranscriptionResult:
    if "text" not in payload:
        raise TranscriberError("Transcriber response missing 'text'")
    return TranscriptionResult(
        text=payload["text"],
        segments=payload.get("segments") or [],
        audio_duration_s=payload.get("audio_duration_s"),
        transcription_time_s=payload.get("transcription_time_s"),
        rtf=payload.get("rtf"),
        params=payload.get("params"),
    )


def _parse_retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        # HTTP-date form: let the caller fall back to its own backoff.
        return None
