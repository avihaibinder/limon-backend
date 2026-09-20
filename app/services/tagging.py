"""Auto-tagging worker logic: load an event, ask the tagger for tags, write the
result.

Driven by ``POST /internal/tag`` (Cloud Tasks), enqueued whenever a text or
audio event ends up with text and no user-selected tags (see
``services/events.py`` and ``services/transcription.py``). Idempotent: an
event that already carries tags -- the user picked some manually in the
meantime, or a duplicate task redelivers -- is a no-op. The check-then-act
here is a plain read, not an atomic claim like ``Recording.state``: accepted,
since the worst case is one redundant tagger call, not a correctness bug.

Outcomes map to HTTP status at the router: ``noop``/``done``/``failed`` -> 2xx
(the queue must not retry), ``retry`` -> 503 (retryable within the capped
budget). Never logs entry text or the model's reasoning.
"""

import asyncio
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import step
from app.db.session import async_session_factory
from app.models.event import Event
from app.services import tagger, task_queue
from app.services import tags as tags_service
from app.services.tagger import (
    EndpointBusyError,
    EndpointUnavailableError,
    RateLimitedError,
    TaggerNotConfiguredError,
    TaggerResponseError,
)
from app.services.task_queue import TaskQueueNotConfiguredError

_local_tagging_tasks: set[asyncio.Task[None]] = set()


@dataclass(frozen=True)
class Outcome:
    status: str  # noop | done | failed | retry
    retry_after: float | None = None


async def dispatch_tagging(event_id: str) -> None:
    """Queue tagging in production, or run it asynchronously in local development."""
    try:
        await task_queue.enqueue_tagging(event_id)
    except TaskQueueNotConfiguredError:
        task = asyncio.create_task(_run_local_tagging(event_id))
        _local_tagging_tasks.add(task)
        task.add_done_callback(_local_tagging_tasks.discard)
        step("tagging_started_locally", eventId=event_id)


async def _run_local_tagging(event_id: str) -> None:
    try:
        async with async_session_factory() as session:
            outcome = await run_tagging(session, event_id)
        step("local_tagging_finished", eventId=event_id, status=outcome.status)
    except Exception as exc:  # Background work must never escape into the request loop.
        step("local_tagging_failed", eventId=event_id, reason=type(exc).__name__)


async def run_tagging(session: AsyncSession, event_id: str) -> Outcome:
    event = await session.get(Event, event_id)
    if event is None:
        step("noop", eventId=event_id, reason="no_event")
        return Outcome("noop")
    if event.tag_ids:
        step("noop", eventId=event_id, reason="already_tagged")
        return Outcome("noop")

    text = "\n".join(part for part in (event.title, event.description) if part)
    if not text.strip():
        step("noop", eventId=event_id, reason="no_text")
        return Outcome("noop")

    # No pagination cap here (unlike routers/tags.py's Query(le=200)): tagging
    # needs the user's full tag list, not a page of it.
    tags, _total = await tags_service.list_tags(
        session, user_id=event.user_id, limit=10_000, offset=0
    )
    existing_tags = [{"id": tag.id, "name": tag.name} for tag in tags]

    try:
        result = await tagger.suggest_tags(text, existing_tags)
    except (EndpointBusyError, RateLimitedError) as exc:
        # Soft failure with a hinted backoff: busy or rate-limited, retry.
        step("retry", eventId=event_id, reason=type(exc).__name__)
        return Outcome("retry", retry_after=exc.retry_after)
    except (EndpointUnavailableError, TaggerNotConfiguredError) as exc:
        # Endpoint down / not configured: soft, retry within budget.
        step("retry", eventId=event_id, reason=type(exc).__name__)
        return Outcome("retry")
    except TaggerResponseError as exc:
        # Unparseable/invalid model response: hard, retrying will not fix it.
        step("failed", eventId=event_id, reason=type(exc).__name__)
        return Outcome("failed")

    allowed_ids = {tag["id"] for tag in existing_tags}
    if any(tag_id not in allowed_ids for tag_id in result.tag_ids):
        step("failed", eventId=event_id, reason="invalid_tag_id")
        return Outcome("failed")

    event.tag_ids = list(dict.fromkeys(result.tag_ids))
    await session.commit()
    step("tagged", eventId=event_id, tagCount=len(result.tag_ids))
    return Outcome("done")
