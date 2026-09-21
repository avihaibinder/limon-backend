from pydantic import BaseModel, ConfigDict, Field


class TranscribeTask(BaseModel):
    """Body of the Cloud Tasks call to POST /internal/transcribe."""

    model_config = ConfigDict(populate_by_name=True)

    record_id: str = Field(alias="recordId")


class ResultsReadyCallback(BaseModel):
    """Body of the transcriber box's callback to POST /internal/transcripts-ready.

    **A nudge, never a delivery.** It carries no transcript and never will, so
    nothing here is persisted -- the fields exist so the log line is useful and
    so a body of an unexpected shape is visible rather than silently ignored.

    Deliberately permissive: the box may add fields, and rejecting an unknown one
    would turn a harmless addition into a 422, which the box reads as a 4xx and
    does not retry. A rejected callback is silent on both sides.
    """

    model_config = ConfigDict(extra="ignore")

    event: str | None = None
    job_id: str | None = None
    results_waiting: int | None = None
    done_unacked: int | None = None
    failed_unacked: int | None = None
    queued: int | None = None
    queue_empty: bool | None = None
