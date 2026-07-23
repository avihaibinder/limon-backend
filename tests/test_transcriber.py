"""Nebius transcription client, exercised with an httpx MockTransport (no network).

Settings are overridden the same way as test_auth.py: patch the module's
``get_settings`` to return a configured ``Settings``.
"""

import httpx
import pytest

from app.core.config import Settings
from app.services import transcriber
from app.services.transcriber import (
    AudioRejectedError,
    AudioTooLargeError,
    EndpointBusyError,
    EndpointUnavailableError,
    TranscriberError,
    TranscriberNotConfiguredError,
)

URL = "https://endpoint.test"
TOKEN = "secret-token"
AUDIO = b"\x00\x01\x02fake-m4a-bytes"

SUCCESS_BODY = {
    "text": "שלום עולם",
    "segments": [{"start": 0.0, "end": 1.2, "text": "שלום עולם"}],
    "audio_duration_s": 1.2,
    "transcription_time_s": 0.1,
    "rtf": 0.083,
    "params": {"device": "cuda", "compute_type": "float16"},
}


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        transcriber,
        "get_settings",
        lambda: Settings(transcriber_endpoint_url=URL, transcriber_endpoint_token=TOKEN),
    )


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_success_returns_transcript(configured: None) -> None:
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        seen["content_type"] = request.headers.get("Content-Type")
        return httpx.Response(200, json=SUCCESS_BODY)

    async with _client(handler) as client:
        result = await transcriber.transcribe(
            AUDIO, filename="clip.m4a", content_type="audio/mp4", client=client
        )

    assert result.text == "שלום עולם"
    assert result.segments[0]["end"] == 1.2
    assert result.rtf == 0.083
    assert seen["url"] == "https://endpoint.test/transcribe"
    assert seen["auth"] == "Bearer secret-token"
    assert seen["content_type"].startswith("multipart/form-data")


async def test_busy_raises_endpoint_busy_with_retry_after(configured: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, headers={"Retry-After": "12"}, text="busy")

    async with _client(handler) as client:
        with pytest.raises(EndpointBusyError) as exc:
            await transcriber.transcribe(AUDIO, client=client)

    assert exc.value.retry_after == 12.0


async def test_connection_error_raises_unavailable(configured: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async with _client(handler) as client:
        with pytest.raises(EndpointUnavailableError):
            await transcriber.transcribe(AUDIO, client=client)


async def test_timeout_raises_unavailable(configured: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    async with _client(handler) as client:
        with pytest.raises(EndpointUnavailableError):
            await transcriber.transcribe(AUDIO, client=client)


async def test_bad_audio_raises_rejected(configured: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="cannot decode")

    async with _client(handler) as client:
        with pytest.raises(AudioRejectedError):
            await transcriber.transcribe(AUDIO, client=client)


async def test_too_large_raises_too_large(configured: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(413, text="too big")

    async with _client(handler) as client:
        with pytest.raises(AudioTooLargeError):
            await transcriber.transcribe(AUDIO, client=client)


async def test_unexpected_status_raises_generic(configured: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    async with _client(handler) as client:
        with pytest.raises(TranscriberError):
            await transcriber.transcribe(AUDIO, client=client)


async def test_missing_text_field_raises(configured: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"segments": []})

    async with _client(handler) as client:
        with pytest.raises(TranscriberError):
            await transcriber.transcribe(AUDIO, client=client)


async def test_unconfigured_raises_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transcriber, "get_settings", lambda: Settings())
    with pytest.raises(TranscriberNotConfiguredError):
        await transcriber.transcribe(AUDIO)
