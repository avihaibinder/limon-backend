#!/usr/bin/env python
"""Drive the whole transcription cycle against the live box, minus the callback.

**The integration gate.** Unit tests prove each piece against a mock; this proves
the wire contract against the real VPS, using the real service functions and a
real (throwaway) database. What it does not cover is the wakeup itself, which
needs an inbound HTTPS endpoint the box can reach -- that is the deployed test.

    uv run python scripts/transcriber/roundtrip.py \
        ~/dev-projects/hebrew-transcriber/audio/clip1_normal.ogg

Reads LIMON_TRANSCRIBER_BASE_URL and LIMON_TRANSCRIBER_TOKEN from .env.

**It filters every drain by the ids it submitted.** The box has one queue and no
per-caller scoping, so an unfiltered drain returns other callers' results and the
ack that follows would delete them. Production drains unfiltered because LimON is
the only caller there; a shared box is exactly when that stops being true.

Expect one alarming-looking log line, `STEP=local_tagging_failed
reason=OperationalError`, and ignore it. With Cloud Tasks unconfigured, tagging
falls back to running in-process against the app's *real* session factory, which
points at a database this script never creates. It is an artefact of the
throwaway database below, not a defect -- and the transcript committing anyway is
the best-effort isolation doing its job.
"""

import argparse
import asyncio
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy.ext.asyncio import async_sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.logging import configure_logging  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import create_engine  # noqa: E402
from app.models.event import Event  # noqa: E402
from app.models.recording import Recording  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services import audio_storage, transcriber, transcription  # noqa: E402


def log(*parts: object) -> None:
    print(f"[{time.strftime('%H:%M:%S')}]", *parts, flush=True)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("clip", type=Path)
    parser.add_argument(
        "--timeout", type=float, default=600.0, help="seconds to wait for the transcript"
    )
    parser.add_argument(
        "--interval", type=float, default=10.0, help="seconds between collection attempts"
    )
    args = parser.parse_args()

    configure_logging()
    settings = get_settings()
    if not settings.transcriber_base_url or not settings.transcriber_token:
        log("ERROR: set LIMON_TRANSCRIBER_BASE_URL and LIMON_TRANSCRIBER_TOKEN in .env")
        return 2
    if not args.clip.is_file():
        log(f"ERROR: no such clip: {args.clip}")
        return 2

    audio = args.clip.read_bytes()
    log(f"box   -> {settings.transcriber_base_url}")
    log(f"clip  -> {args.clip.name} ({len(audio)} bytes)")

    # A throwaway in-memory database, so this never touches a real one.
    engine = create_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    # Feed the worker's GCS read from the local file instead of the bucket.
    async def _download(storage_key: str) -> bytes:
        return audio

    audio_storage.download = _download

    async with factory() as session:
        user = User(id="roundtrip", provider="google", email="roundtrip@example.com")
        session.add(user)
        await session.flush()
        recording = Recording(
            user_id=user.id,
            storage_key=f"v0/{user.id}/clip{args.clip.suffix}",
            content_type="application/octet-stream",
        )
        session.add(recording)
        await session.flush()
        event = Event(
            user_id=user.id,
            type="audio",
            title=None,
            occurred_at=datetime.now(UTC),
            recording_id=recording.id,
        )
        session.add(event)
        await session.commit()
        record_id = recording.id

        log(f"seeded recording {record_id}")
        log("")
        log("--- submit (POST /jobs) ---")
        outcome = await transcription.run_transcription(session, record_id)
        log(f"outcome: {outcome.status}")
        if outcome.status != "submitted":
            log("FAILED: expected 'submitted'")
            await engine.dispose()
            return 1

        log("")
        log("--- collect (GET /jobs -> persist -> POST /jobs/ack) ---")
        log("NOT a callback: polling stands in for the wakeup this box cannot reach.")
        deadline = time.time() + args.timeout
        while time.time() < deadline:
            # ids= keeps us off other callers' results. See the module docstring.
            result = await transcription.collect_transcripts(session, ids=[record_id])
            if result.written or result.failed:
                break
            log(f"  nothing ready yet; retrying in {args.interval:.0f}s")
            await asyncio.sleep(args.interval)
        else:
            log(f"TIMED OUT after {args.timeout:.0f}s")
            await engine.dispose()
            return 1

        await session.refresh(event)
        await session.refresh(recording)

        log("")
        log("=" * 72)
        log(f"recording.state = {recording.state}")
        log(f"recording.error = {recording.error}")
        log(f"transcript      = {event.description!r}")
        log("=" * 72)

        ok = recording.state == "done" and bool(event.description)
        log(f"VERDICT: {'PASS' if ok else 'FAIL'}")

        # Leave the box as we found it: nothing of ours queued or unacked.
        leftover = await transcriber.get_job(record_id)
        log(f"box still holds this job: {leftover is not None} (False is correct after ack)")

    await engine.dispose()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
