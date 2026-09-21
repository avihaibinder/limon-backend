"""The transcriber job-API client, over an httpx MockTransport (no network).

Several of these assert the *exact* request rather than the parsed result. That
is deliberate and the reason is in the module under test: the box ignores unknown
query parameters instead of rejecting them, so `include_text=true` returns 200
with every transcript missing and nothing to notice. A test that only checked the
parsed output would pass against that bug.
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
    JobConflictError,
    TranscriberError,
    TranscriberNotConfiguredError,
    UnauthorizedError,
)

URL = "https://box.test"
TOKEN = "secret-token"
AUDIO = b"\x00\x01\x02fake-m4a-bytes"
JOB = "11111111-2222-3333-4444-555555555555"

DONE_JOB = {
    "job_id": JOB,
    "status": "done",
    "created_at": "2026-09-21T09:09:21+00:00",
    "audio_duration_s": 19.43,
    "transcription_time_s": 21.29,
    "rtf": 1.097,
    "text": "שלום עולם",
    "segments": [{"start": 0.0, "end": 1.2, "text": "שלום עולם"}],
}

FAILED_JOB = {
    "job_id": JOB,
    "status": "failed",
    "created_at": "2026-09-21T09:09:21+00:00",
    "audio_duration_s": 30.0,
    "error": {
        "code": "transcription_failed",
        "message": "internal transcription error",
        "retryable": True,
    },
}


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        transcriber,
        "get_settings",
        lambda: Settings(_env_file=None, transcriber_base_url=URL, transcriber_token=TOKEN),
    )


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _json(payload, status=200, headers=None):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload, headers=headers or {})

    return handler


# --- configuration ---------------------------------------------------------


async def test_unconfigured_raises_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transcriber, "get_settings", lambda: Settings(_env_file=None))
    with pytest.raises(TranscriberNotConfiguredError):
        await transcriber.drain()


async def test_token_is_sent_as_a_bearer_header(configured: None) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json={"jobs": []})

    await transcriber.drain(client=_client(handler))
    assert seen["authorization"] == f"Bearer {TOKEN}"


async def test_token_is_never_a_query_parameter(configured: None) -> None:
    """A token in a URL lands in every log and proxy along the way."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"jobs": []})

    await transcriber.drain(client=_client(handler))
    assert TOKEN not in seen[0]


# --- submit ----------------------------------------------------------------


async def test_submit_sends_multipart_file_and_job_id(configured: None) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = request.content
        return httpx.Response(202, json={"job_id": JOB, "status": "queued", "queue_position": 1})

    result = await transcriber.submit(JOB, AUDIO, client=_client(handler))

    assert seen["path"] == "/jobs"
    assert JOB.encode() in seen["body"]
    assert AUDIO in seen["body"]
    assert result.already_existed is False
    assert result.queue_position == 1


async def test_submit_200_is_an_idempotent_hit_not_a_new_job(configured: None) -> None:
    """Same id, same bytes: the box recognises it and queues nothing new."""
    result = await transcriber.submit(
        JOB, AUDIO, client=_client(_json({"job_id": JOB, "status": "queued"}, status=200))
    )
    assert result.already_existed is True


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (400, AudioRejectedError),
        (401, UnauthorizedError),
        (409, JobConflictError),
        (413, AudioTooLargeError),
        (500, TranscriberError),
    ],
)
async def test_submit_maps_status_codes_to_typed_errors(
    configured: None, status: int, expected: type[Exception]
) -> None:
    with pytest.raises(expected):
        await transcriber.submit(JOB, AUDIO, client=_client(_json({}, status=status)))


async def test_submit_503_is_queue_full_and_carries_retry_after(configured: None) -> None:
    """503 means the box's queue is full of audio, not that the box is down."""
    handler = _json({}, status=503, headers={"Retry-After": "120"})
    with pytest.raises(EndpointBusyError) as excinfo:
        await transcriber.submit(JOB, AUDIO, client=_client(handler))
    assert excinfo.value.retry_after == 120.0


async def test_retry_after_in_http_date_form_is_ignored_not_fatal(configured: None) -> None:
    handler = _json({}, status=503, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})
    with pytest.raises(EndpointBusyError) as excinfo:
        await transcriber.submit(JOB, AUDIO, client=_client(handler))
    assert excinfo.value.retry_after is None


async def test_transport_error_becomes_endpoint_unavailable(configured: None) -> None:
    """The expected failure when the Quick Tunnel URL has rotated."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    with pytest.raises(EndpointUnavailableError):
        await transcriber.submit(JOB, AUDIO, client=_client(handler))


# --- drain -----------------------------------------------------------------


async def test_drain_asks_for_include_text_not_include_text_true(configured: None) -> None:
    """The trap: an unknown parameter is ignored, so the wrong name is silent."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json={"jobs": []})

    await transcriber.drain(client=_client(handler))

    assert seen["include"] == "text"
    assert "include_text" not in seen


async def test_drain_passes_limit_because_the_default_is_50(configured: None) -> None:
    """A drain that omits `limit` silently tops out at 50 and looks complete."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json={"jobs": []})

    await transcriber.drain(client=_client(handler))

    assert seen["limit"] == str(transcriber.MAX_LIST_LIMIT)
    assert seen["status"] == "done,failed"


async def test_drain_parses_transcript_and_envelope_counts(configured: None) -> None:
    handler = _json({"jobs": [DONE_JOB], "done_unacked": 3, "failed_unacked": 1})
    page = await transcriber.drain(client=_client(handler))

    assert page.jobs[0].text == "שלום עולם"
    assert page.jobs[0].is_done
    assert page.waiting == 4


async def test_drain_reads_nested_error_not_flat_error_code(configured: None) -> None:
    """There is no `error_code` on the wire; reading one yields None silently."""
    page = await transcriber.drain(client=_client(_json({"jobs": [FAILED_JOB]})))
    job = page.jobs[0]

    assert job.is_failed
    assert job.error_code == "transcription_failed"
    assert job.error_retryable is True


async def test_drain_with_ids_filters_the_request(configured: None) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json={"jobs": []})

    await transcriber.drain(ids=["a", "b"], client=_client(handler))
    assert seen["ids"] == "a,b"


# --- ack -------------------------------------------------------------------


async def test_ack_posts_the_ids(configured: None) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["json"] = request.content
        return httpx.Response(200, json={"deleted": [JOB], "not_found": []})

    result = await transcriber.ack([JOB], client=_client(handler))

    assert seen["path"] == "/jobs/ack"
    assert JOB.encode() in seen["json"]
    assert result["deleted"] == [JOB]


async def test_ack_of_nothing_makes_no_request(configured: None) -> None:
    """Guards the drain loop: an empty page must not POST an empty ack."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("ack should not have been called")

    assert await transcriber.ack([], client=_client(handler)) == {
        "deleted": [],
        "not_found": [],
    }


async def test_ack_over_the_cap_is_rejected_before_the_wire(configured: None) -> None:
    with pytest.raises(ValueError):
        await transcriber.ack(["x"] * (transcriber.MAX_LIST_LIMIT + 1))


# --- get / discard ---------------------------------------------------------


async def test_get_job_404_is_none_not_an_error(configured: None) -> None:
    """404 is ambiguous (unknown / acked / expired) and all three mean 'gone'."""
    assert await transcriber.get_job(JOB, client=_client(_json({}, status=404))) is None


async def test_get_job_returns_the_parsed_job(configured: None) -> None:
    job = await transcriber.get_job(JOB, client=_client(_json(DONE_JOB)))
    assert job is not None
    assert job.rtf == 1.097


async def test_discard_treats_404_as_success(configured: None) -> None:
    await transcriber.discard(JOB, client=_client(_json({}, status=404)))


async def test_discard_uses_delete(configured: None) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        return httpx.Response(204)

    await transcriber.discard(JOB, client=_client(handler))
    assert seen["method"] == "DELETE"
