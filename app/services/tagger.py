"""Client for the Nebius Token Factory auto-tagging endpoint.

The backend calls Nebius's OpenAI-compatible chat completions API with a
Hebrew system prompt and a strict ``json_schema`` response format, asking the
model to pick tags for one entry from the caller's own existing tags only --
it is never allowed to invent a new tag (enforced again server-side in
``_parse``, not just requested in the prompt).

Native Qwen3 "thinking" is turned off (``chat_template_kwargs.enable_thinking:
false``): combining it with strict ``json_schema`` output is unreliable
across OpenAI-compatible providers. The schema's own ``reasoning`` field is
where the model is asked to show its step-by-step work instead.

Never log entry text or the model's reasoning from here.
"""

import re
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.config import get_settings

Sentiment = Literal["positive", "negative", "neutral"]

# Hebrew block \u0590-\u05FF (includes niqqud and geresh/gershayim), basic
# Latin, digits, whitespace, and common sentence punctuation. Used to scrub
# stray Chinese/Cyrillic/Arabic characters the model sometimes mixes into
# free text even with thinking disabled -- see _sanitize_reasoning.
_DISALLOWED_REASONING_CHARS = re.compile(
    "[^\u0590-\u05ffa-zA-Z0-9\\s.,;:!?()\\[\\]{}'\"\\-\u2013\u2014/%&@#*+=_~`^|<>]+"
)

# Room for the `reasoning` field to finish a full sentence plus the rest of
# the structured response. The endpoint's own default was too low: live
# testing showed `reasoning` truncated mid-sentence with no cap set at all.
_MAX_TOKENS = 1000

_SYSTEM_PROMPT = """את/ה עוזר/ת שמתייג/ת רשומות יומן אישיות עבור אפליקציית LimON.

בהינתן טקסט של רשומה ורשימת התגיות הקיימות של המשתמש/ת (מזהה ושם לכל תגית),
המשימה שלך:

1. לנתח את תוכן הרשומה.
2. לקבוע את הסנטימנט הכללי שלה: positive, negative או neutral.
3. לזהות אזכור מפורש ומובהק של מיקום (עיר, מדינה, מקום ספציפי) אם קיים בטקסט;
   אם אין אזכור כזה, להחזיר null. אין לנחש מיקום שלא הוזכר במפורש.
4. לבחור אילו מהתגיות הקיימות -- ורק מהן -- רלוונטיות לרשומה, לפי המזהה (id)
   שלהן. אסור בהחלט להמציא תגית חדשה או להחזיר מזהה שלא מופיע ברשימה
   שסופקה. אם אף תגית לא רלוונטית, יש להחזיר רשימה ריקה.

חשוב: קודם נמק/י צעד-אחר-צעד בשדה reasoning (מה יש בטקסט, למה כל תגית
נבחרה או לא, למה נקבע הסנטימנט), ורק לאחר מכן קבע/י את שאר השדות בהתאם
למסקנה. כל התשובה, כולל הנימוק, צריכה להיות בעברית בלבד.

חשוב מאוד: יש להשתמש אך ורק באותיות עבריות, לטיניות (אנגלית), ספרות וסימני
פיסוק בסיסיים. אסור בהחלט להשתמש בתווים בסינית, בקיריליות (רוסית) או בערבית,
בשום מקום בתשובה -- כולל בתוך שדה reasoning. אם עולה בך דחף להשתמש בתו שאינו
עברי או לטיני, נסח/י מחדש את המשפט בעברית תקנית במקום.

יש להחזיר אך ורק JSON תואם לסכימה שסופקה, ללא טקסט נוסף מחוץ ל-JSON."""


class TaggingResult(BaseModel):
    """Parsed, validated model output.

    No field has a default: this model doubles as the strict ``json_schema``
    sent to the endpoint (via ``model_json_schema()``), and OpenAI-compatible
    strict mode requires every property in ``required`` -- a nullable field
    (``suggested_location``) is still required, just typed to allow ``null``.
    A default here would make pydantic drop the field from ``required``.

    ``tag_ids`` is filtered down to the ids offered in ``existing_tags`` --
    the model is instructed never to invent a tag, but this is the
    enforcement of that, not just a request.
    """

    model_config = ConfigDict(extra="forbid")

    sentiment: Sentiment
    suggested_location: str | None
    tag_ids: list[str]
    reasoning: str


class TaggerError(RuntimeError):
    """Base class for tagger client failures."""


class TaggerNotConfiguredError(TaggerError):
    """API key is unset; treat as unavailable, not a caller error."""


class EndpointUnavailableError(TaggerError):
    """Connection refused / timeout / DNS: endpoint down. Soft (retry)."""


class EndpointBusyError(TaggerError):
    """503. Soft (retry); carries ``retry_after`` if provided."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class RateLimitedError(TaggerError):
    """429. Soft (retry); carries ``retry_after`` if provided."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class TaggerResponseError(TaggerError):
    """2xx but the body isn't a usable tagging response (bad JSON / schema
    mismatch), or an unexpected non-retryable status. Hard: retrying the same
    input against the same broken response will not fix it."""


@dataclass(frozen=True)
class _Prepared:
    endpoint: str
    headers: dict[str, str]
    body: dict[str, Any]
    existing_ids: frozenset[str]


def _prepare(settings, api_key: str, text: str, existing_tags: list[dict[str, str]]) -> _Prepared:
    endpoint = settings.tagger_base_url.rstrip("/") + "/chat/completions"
    tag_list = "\n".join(f"- {tag['id']}: {tag['name']}" for tag in existing_tags) or "(אין תגיות)"
    body = {
        "model": settings.tagger_model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"תגיות קיימות:\n{tag_list}\n\nטקסט הרשומה:\n{text}",
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "tag_suggestion",
                "schema": TaggingResult.model_json_schema(),
                "strict": True,
            },
        },
        # Disable native Qwen3 reasoning; the schema's `reasoning` field carries
        # the chain-of-thought instead. See module docstring.
        "chat_template_kwargs": {"enable_thinking": False},
        "max_tokens": _MAX_TOKENS,
    }
    return _Prepared(
        endpoint=endpoint,
        headers={"Authorization": f"Bearer {api_key}"},
        body=body,
        existing_ids=frozenset(tag["id"] for tag in existing_tags),
    )


async def suggest_tags(
    text: str,
    existing_tags: list[dict[str, str]],
    *,
    client: httpx.AsyncClient | None = None,
) -> TaggingResult:
    """Ask the tagger to suggest sentiment/location/tags for ``text``.

    ``existing_tags`` is ``[{"id": ..., "name": ...}, ...]`` for the owning
    user; the result's ``tag_ids`` is always a subset of these ids. Pass
    ``client`` to reuse/inject an ``httpx.AsyncClient`` (used by tests);
    otherwise a short-lived client is created with the configured timeout.
    Raises a ``TaggerError`` subclass on any non-200 or unparseable outcome.
    """
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

    if response.status_code == 200:
        return _parse(response.json(), existing_ids=prepared.existing_ids)
    _raise_for_status(response)


def _raise_for_status(response: httpx.Response) -> None:
    code = response.status_code
    # Deliberately do not echo the response body: it may leak endpoint internals.
    if code == 503:
        raise EndpointBusyError("Tagger busy (503)", retry_after=_parse_retry_after(response))
    if code == 429:
        raise RateLimitedError(
            "Tagger rate-limited (429)", retry_after=_parse_retry_after(response)
        )
    raise TaggerResponseError(f"Tagger returned unexpected status {code}")


def _parse(payload: dict[str, Any], *, existing_ids: frozenset[str]) -> TaggingResult:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise TaggerResponseError("Tagger response missing choices[0].message.content") from exc

    try:
        result = TaggingResult.model_validate_json(content)
    except (ValidationError, ValueError) as exc:
        raise TaggerResponseError(f"Tagger response failed schema validation: {exc}") from exc

    # Enforce "never invent a tag" server-side; the prompt asks for this, this
    # line guarantees it regardless of what the model actually returned.
    result.tag_ids = [tag_id for tag_id in result.tag_ids if tag_id in existing_ids]
    result.reasoning = _sanitize_reasoning(result.reasoning)
    return result


def _sanitize_reasoning(text: str) -> str:
    """Safety net: strip Chinese/Cyrillic/Arabic (and any other non-Hebrew/
    Latin) characters out of ``reasoning``.

    The system prompt already instructs Hebrew-only output, but the model
    sometimes mixes in a stray character or two anyway (observed even with
    thinking disabled) -- this is a mitigation, not a guarantee. Only
    ``reasoning`` is touched; ``tag_ids``/``sentiment``/``suggested_location``
    are structured values already constrained by the schema, not free text.

    Disallowed runs collapse to a single space (rather than being deleted
    outright) so removing a stray character doesn't jam the two surrounding
    Hebrew words together into a new, different word.
    """
    cleaned = _DISALLOWED_REASONING_CHARS.sub(" ", text)
    return re.sub(r"\s+", " ", cleaned).strip()


def _parse_retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        # HTTP-date form: let the caller fall back to its own backoff.
        return None
