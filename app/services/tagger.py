"""GroqCloud client for selecting tags from one user's existing tag list.

Groq receives the Hebrew event text and only the owning user's tags. Qwen uses
high, hidden reasoning and strict Structured Outputs, so visible model output is
limited to ``{"tag_ids": [...]}``. The response is validated again before it
can reach the database; a model-supplied ID outside the offered set is rejected.

Never log entry text, tag names, response bodies, or the API key from here.
"""

from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.config import get_settings

_MAX_COMPLETION_TOKENS = 800

_INSTRUCTIONS = """Select the relevant tags for the Hebrew journal entry.
Use only tag IDs from the supplied list. Never create or infer another tag ID.
If none match, return an empty list. Evaluate the choices carefully before
answering. Return only the JSON object required by the supplied schema."""


class TaggingResult(BaseModel):
    """The complete visible model result: selected existing tag IDs only."""

    model_config = ConfigDict(extra="forbid")
    tag_ids: list[str]


class TaggerError(RuntimeError):
    """Base class for tagger client failures."""


class TaggerNotConfiguredError(TaggerError):
    """API key is unset; treat as unavailable, not a caller error."""


class EndpointUnavailableError(TaggerError):
    """Connection, DNS, or timeout failure. Soft and retryable."""


class EndpointBusyError(TaggerError):
    """503. Soft and retryable; carries ``retry_after`` when available."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class RateLimitedError(TaggerError):
    """429. Soft and retryable; carries ``retry_after`` when available."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class TaggerResponseError(TaggerError):
    """Authentication, model, schema, or malformed-response failure."""


@dataclass(frozen=True)
class _Prepared:
    endpoint: str
    headers: dict[str, str]
    body: dict[str, Any]
    existing_ids: frozenset[str]


def _prepare(settings, api_key: str, text: str, existing_tags: list[dict[str, str]]) -> _Prepared:
    existing_ids = frozenset(tag["id"] for tag in existing_tags)
    tag_list = "\n".join(f"- {tag['id']}: {tag['name']}" for tag in existing_tags)
    schema = {
        "type": "object",
        "properties": {
            "tag_ids": {
                "type": "array",
                "items": {"type": "string", "enum": sorted(existing_ids)},
            }
        },
        "required": ["tag_ids"],
        "additionalProperties": False,
    }
    body = {
        "model": settings.tagger_model,
        "messages": [
            {
                "role": "user",
                "content": (
                    f"{_INSTRUCTIONS}\n\nAvailable tags:\n{tag_list}"
                    f"\n\nHebrew event text:\n{text}"
                ),
            }
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "tag_selection",
                "schema": schema,
                "strict": True,
            },
        },
        "reasoning_effort": "high",
        "reasoning_format": "hidden",
        "temperature": 1.0,
        "max_completion_tokens": _MAX_COMPLETION_TOKENS,
    }
    return _Prepared(
        endpoint=settings.tagger_base_url.rstrip("/") + "/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        body=body,
        existing_ids=existing_ids,
    )


async def suggest_tags(
    text: str,
    existing_tags: list[dict[str, str]],
    *,
    client: httpx.AsyncClient | None = None,
) -> TaggingResult:
    """Return only tag IDs selected from ``existing_tags`` for ``text``."""
    if not existing_tags:
        return TaggingResult(tag_ids=[])

    settings = get_settings()
    api_key = settings.tagger_api_key
    if not api_key:
        raise TaggerNotConfiguredError(
            "Tagger endpoint is not configured (set LIMON_TAGGER_API_KEY)."
        )

    prepared = _prepare(settings, api_key, text, existing_tags)
    if client is not None:
        return await _send(client, prepared)
    async with httpx.AsyncClient(timeout=settings.tagger_timeout_s) as owned:
        return await _send(owned, prepared)


async def _send(client: httpx.AsyncClient, prepared: _Prepared) -> TaggingResult:
    try:
        response = await client.post(
            prepared.endpoint, json=prepared.body, headers=prepared.headers
        )
    except httpx.TransportError as exc:
        raise EndpointUnavailableError(
            f"Tagger endpoint unreachable: {type(exc).__name__}"
        ) from exc

    if response.status_code != 200:
        _raise_for_status(response)
    try:
        payload = response.json()
    except ValueError as exc:
        raise TaggerResponseError("Tagger returned a non-JSON response") from exc
    return _parse(payload, existing_ids=prepared.existing_ids)


def _raise_for_status(response: httpx.Response) -> None:
    code = response.status_code
    if code == 503:
        raise EndpointBusyError("Tagger busy (503)", retry_after=_parse_retry_after(response))
    if code == 429:
        raise RateLimitedError(
            "Tagger rate-limited (429)", retry_after=_parse_retry_after(response)
        )
    if code in (401, 403):
        raise TaggerResponseError(f"Tagger authentication failed ({code})")
    raise TaggerResponseError(f"Tagger returned unexpected status {code}")


def _parse(payload: dict[str, Any], *, existing_ids: frozenset[str]) -> TaggingResult:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise TaggerResponseError("Tagger response missing choices[0].message.content") from exc

    try:
        result = TaggingResult.model_validate_json(content)
    except (ValidationError, ValueError, TypeError) as exc:
        raise TaggerResponseError("Tagger response failed schema validation") from exc

    invented = set(result.tag_ids) - existing_ids
    if invented:
        raise TaggerResponseError("Tagger returned a tag ID outside the supplied list")

    result.tag_ids = list(dict.fromkeys(result.tag_ids))
    return result


def _parse_retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None
