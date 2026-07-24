"""Enqueue background work (transcription, auto-tagging) as Cloud Tasks.

The GCS-finalize handler (``POST /internal/uploaded``) turns one uploaded audio
object into one Cloud Task targeting ``POST /internal/transcribe`` (domain 03).
Auto-tagging (``POST /internal/tag``) is enqueued the same way, from the events
and transcription services, whenever an event ends up with text and no
user-selected tags. Cloud Tasks, rather than a direct Pub/Sub push to the
worker, is what gives us the capped retry budget and the backlog re-enqueue
story; see ``spec-local/plan/04-trigger.md`` (decision 6).

``google-cloud-tasks`` is imported lazily so this module (and the app) load without
it in dev/tests, where the enqueue seam is mocked and the dev shim replaces the
trigger entirely. Queue coordinates are settings, not code.
"""

import asyncio
import json

from app.core.config import get_settings

# Paths on the worker service a task POSTs to; joined onto tasks_worker_url.
_TRANSCRIBE_PATH = "/internal/transcribe"
_TAG_PATH = "/internal/tag"


class TaskQueueError(RuntimeError):
    """Enqueueing a transcription task failed (client / transport error).

    The finalize handler lets this propagate so Pub/Sub does not ack and redelivers
    the (durable) finalize notification, retrying the enqueue.
    """


class TaskQueueNotConfiguredError(TaskQueueError):
    """Cloud Tasks coordinates are missing (project / location / queue / worker URL)."""


async def enqueue_transcription(record_id: str) -> None:
    """Enqueue one Cloud Task that will ``POST {"recordId": record_id}`` to the worker.

    Runs the blocking Cloud Tasks client call off the event loop. Raises
    ``TaskQueueNotConfiguredError`` when the queue is not configured and
    ``TaskQueueError`` on a client/transport failure. Safe to call more than once
    for the same ``record_id``: the worker's atomic claim makes a duplicate task a
    no-op.
    """
    await asyncio.to_thread(_create_task, _TRANSCRIBE_PATH, {"recordId": record_id})


async def enqueue_tagging(event_id: str) -> None:
    """Enqueue one Cloud Task that will ``POST {"eventId": event_id}`` to the worker.

    Runs the blocking Cloud Tasks client call off the event loop. Raises
    ``TaskQueueNotConfiguredError`` when the queue is not configured and
    ``TaskQueueError`` on a client/transport failure. Safe to call more than once
    for the same ``event_id``: ``/internal/tag`` no-ops once the event already
    carries tags.
    """
    await asyncio.to_thread(_create_task, _TAG_PATH, {"eventId": event_id})


def _create_task(path: str, body: dict) -> None:
    settings = get_settings()
    project = settings.tasks_project
    location = settings.tasks_location
    queue = settings.tasks_queue
    worker_url = settings.tasks_worker_url
    if not (project and location and queue and worker_url):
        raise TaskQueueNotConfiguredError(
            "Cloud Tasks is not configured (set LIMON_TASKS_PROJECT, "
            "LIMON_TASKS_LOCATION, LIMON_TASKS_QUEUE, LIMON_TASKS_WORKER_URL)."
        )

    # Lazy import: google-cloud-tasks is only needed on the production enqueue path;
    # dev/tests mock this seam and never reach here.
    from google.cloud import tasks_v2

    target_url = worker_url.rstrip("/") + path
    http_request: dict = {
        "http_method": tasks_v2.HttpMethod.POST,
        "url": target_url,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body).encode(),
    }
    oidc_sa = settings.tasks_oidc_service_account
    if oidc_sa:
        # OIDC token so the worker can verify the caller (replaces the interim
        # shared-secret header in production).
        http_request["oidc_token"] = {"service_account_email": oidc_sa, "audience": target_url}

    client = tasks_v2.CloudTasksClient()
    parent = client.queue_path(project, location, queue)
    try:
        client.create_task(parent=parent, task={"http_request": http_request})
    except Exception as exc:  # noqa: BLE001 - normalize any google API/transport error
        raise TaskQueueError(f"Failed to enqueue task {path}: {type(exc).__name__}") from exc
