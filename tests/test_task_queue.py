import json
from types import SimpleNamespace

import pytest
from google.cloud import tasks_v2

from app.core.config import Settings
from app.services import task_queue


class FakeTasksClient:
    def __init__(self, tasks: list[SimpleNamespace]) -> None:
        self.tasks = tasks
        self.deleted: list[str] = []

    def queue_path(self, project: str, location: str, queue: str) -> str:
        return f"projects/{project}/locations/{location}/queues/{queue}"

    def list_tasks(self, *, request: dict):
        assert request["response_view"] == tasks_v2.Task.View.FULL
        return self.tasks

    def delete_task(self, *, request: dict) -> None:
        self.deleted.append(request["name"])


def _task(name: str, body: dict) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        http_request=SimpleNamespace(body=json.dumps(body).encode()),
    )


async def test_cancel_account_tasks_deletes_only_owned_payload_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeTasksClient(
        [
            _task("owned-event", {"eventId": "event-1"}),
            _task("owned-recording", {"recordId": "recording-1"}),
            _task("other-event", {"eventId": "event-2"}),
            _task("other-recording", {"recordId": "recording-2"}),
            _task("unrelated", {"userId": "user-1"}),
        ]
    )
    monkeypatch.setattr(
        task_queue,
        "get_settings",
        lambda: Settings(
            tasks_project="project",
            tasks_location="location",
            tasks_queue="queue",
        ),
    )
    monkeypatch.setattr(tasks_v2, "CloudTasksClient", lambda: client)

    await task_queue.cancel_account_tasks(event_ids={"event-1"}, recording_ids={"recording-1"})

    assert client.deleted == ["owned-event", "owned-recording"]


async def test_cancel_account_tasks_is_noop_when_queue_is_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(task_queue, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(
        tasks_v2,
        "CloudTasksClient",
        lambda: (_ for _ in ()).throw(AssertionError("client must not be created")),
    )

    await task_queue.cancel_account_tasks(event_ids={"event-1"}, recording_ids=set())
