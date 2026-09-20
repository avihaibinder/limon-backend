import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.event import Event
from app.models.tag import Tag
from app.models.user import User
from app.services import tagger, tagging
from app.services.task_queue import TaskQueueNotConfiguredError


async def _seed_two_users(session_factory: async_sessionmaker[AsyncSession]) -> str:
    async with session_factory() as session:
        session.add_all(
            [
                User(id="owner", provider="google"),
                User(id="other", provider="google"),
            ]
        )
        await session.flush()
        session.add_all(
            [
                Tag(id="owner-sport", user_id="owner", name="ספורט"),
                Tag(id="other-private", user_id="other", name="פרטי"),
            ]
        )
        event = Event(
            id="owner-event",
            user_id="owner",
            type="text",
            description="יצאתי לריצה בפארק",
            tag_ids=[],
            occurred_at=datetime.now(UTC),
        )
        session.add(event)
        await session.commit()
        return event.id


async def test_worker_passes_only_owners_tags_and_saves_valid_selection(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    event_id = await _seed_two_users(session_factory)
    received: list[dict[str, str]] = []

    async def suggest(text: str, existing_tags: list[dict[str, str]]) -> tagger.TaggingResult:
        assert text == "יצאתי לריצה בפארק"
        received.extend(existing_tags)
        return tagger.TaggingResult(tag_ids=["owner-sport"])

    monkeypatch.setattr(tagger, "suggest_tags", suggest)
    async with session_factory() as session:
        outcome = await tagging.run_tagging(session, event_id)

    assert outcome.status == "done"
    assert received == [{"id": "owner-sport", "name": "ספורט"}]
    async with session_factory() as session:
        event = await session.get(Event, event_id)
        assert event is not None and event.tag_ids == ["owner-sport"]


async def test_worker_does_not_save_on_groq_error_or_rate_limit(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    event_id = await _seed_two_users(session_factory)

    async def rate_limited(*_args, **_kwargs):
        raise tagger.RateLimitedError("limited", retry_after=9)

    monkeypatch.setattr(tagger, "suggest_tags", rate_limited)
    async with session_factory() as session:
        outcome = await tagging.run_tagging(session, event_id)
    assert outcome == tagging.Outcome("retry", retry_after=9)

    async with session_factory() as session:
        event = await session.get(Event, event_id)
        assert event is not None and event.tag_ids == []


async def test_local_fallback_runs_worker_when_queue_is_not_configured(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    event_id = await _seed_two_users(session_factory)

    async def no_queue(_event_id: str) -> None:
        raise TaskQueueNotConfiguredError("local")

    async def suggest(_text: str, existing_tags: list[dict[str, str]]) -> tagger.TaggingResult:
        assert [tag["id"] for tag in existing_tags] == ["owner-sport"]
        return tagger.TaggingResult(tag_ids=["owner-sport"])

    monkeypatch.setattr(tagging.task_queue, "enqueue_tagging", no_queue)
    monkeypatch.setattr(tagging, "async_session_factory", session_factory)
    monkeypatch.setattr(tagger, "suggest_tags", suggest)

    await tagging.dispatch_tagging(event_id)
    await asyncio.gather(*tagging._local_tagging_tasks)

    async with session_factory() as session:
        event = await session.get(Event, event_id)
        assert event is not None and event.tag_ids == ["owner-sport"]


async def test_configured_queue_does_not_start_local_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queued: list[str] = []

    async def enqueue(event_id: str) -> None:
        queued.append(event_id)

    async def must_not_run(_event_id: str) -> None:
        raise AssertionError("local worker started with a configured queue")

    monkeypatch.setattr(tagging.task_queue, "enqueue_tagging", enqueue)
    monkeypatch.setattr(tagging, "_run_local_tagging", must_not_run)

    await tagging.dispatch_tagging("event-1")

    assert queued == ["event-1"]


async def test_worker_rejects_invented_or_other_users_tag_from_client_boundary(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    event_id = await _seed_two_users(session_factory)

    async def unsafe_client(*_args, **_kwargs) -> tagger.TaggingResult:
        return tagger.TaggingResult(tag_ids=["other-private", "invented"])

    monkeypatch.setattr(tagger, "suggest_tags", unsafe_client)
    async with session_factory() as session:
        outcome = await tagging.run_tagging(session, event_id)

    assert outcome.status == "failed"
    async with session_factory() as session:
        event = await session.get(Event, event_id)
        assert event is not None and event.tag_ids == []
