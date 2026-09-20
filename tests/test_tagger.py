from types import SimpleNamespace

import httpx
import pytest

from app.services import tagger


@pytest.fixture(autouse=True)
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        tagger,
        "get_settings",
        lambda: SimpleNamespace(
            tagger_api_key="test-key",
            tagger_model="qwen/qwen3.8-27b",
            tagger_base_url="https://api.groq.com/openai/v1",
            tagger_timeout_s=1.0,
        ),
    )


def _response(tag_ids: list[str]) -> dict:
    return {
        "choices": [
            {"message": {"content": tagger.TaggingResult(tag_ids=tag_ids).model_dump_json()}}
        ]
    }


async def test_selects_matching_tag_for_hebrew_text_and_uses_groq_contract() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = __import__("json").loads(request.content)
        assert "יצאתי לריצה בפארק" in body["messages"][0]["content"]
        assert "ספורט" in body["messages"][0]["content"]
        assert body["reasoning_effort"] == "high"
        assert body["reasoning_format"] == "hidden"
        assert body["max_completion_tokens"] == 800
        schema = body["response_format"]["json_schema"]
        assert schema["strict"] is True
        assert schema["schema"]["properties"]["tag_ids"]["items"]["enum"] == ["sport"]
        assert set(schema["schema"]["properties"]) == {"tag_ids"}
        return httpx.Response(200, json=_response(["sport"]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await tagger.suggest_tags(
            "יצאתי לריצה בפארק", [{"id": "sport", "name": "ספורט"}], client=client
        )

    assert result.tag_ids == ["sport"]


async def test_empty_matching_result() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_response([]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await tagger.suggest_tags(
            "יום שקט", [{"id": "work", "name": "עבודה"}], client=client
        )

    assert result.tag_ids == []


async def test_rejects_invented_tag_even_if_provider_returns_it() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_response(["other-users-tag"]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(tagger.TaggerResponseError, match="outside the supplied list"):
            await tagger.suggest_tags("טקסט", [{"id": "mine", "name": "שלי"}], client=client)


@pytest.mark.parametrize("status", [401, 400, 500])
async def test_authentication_and_model_errors_are_contained(status: int) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": "not echoed"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(tagger.TaggerResponseError):
            await tagger.suggest_tags("טקסט", [{"id": "mine", "name": "שלי"}], client=client)


async def test_rate_limit_preserves_retry_after() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "12"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(tagger.RateLimitedError) as caught:
            await tagger.suggest_tags("טקסט", [{"id": "mine", "name": "שלי"}], client=client)

    assert caught.value.retry_after == 12


async def test_timeout_and_malformed_response_are_contained() -> None:
    async def timeout(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout")

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        with pytest.raises(tagger.EndpointUnavailableError):
            await tagger.suggest_tags("טקסט", [{"id": "mine", "name": "שלי"}], client=client)

    async def malformed(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    async with httpx.AsyncClient(transport=httpx.MockTransport(malformed)) as client:
        with pytest.raises(tagger.TaggerResponseError, match="non-JSON"):
            await tagger.suggest_tags("טקסט", [{"id": "mine", "name": "שלי"}], client=client)
