"""Pipeline stepping-stone logging: one ``STEP=<name>`` marker per hop, keyed by
``recordId``.

The audio path is an async chain across process boundaries (``POST /events`` ->
GCS finalize -> Pub/Sub -> ``/internal/uploaded`` -> Cloud Task ->
``/internal/transcribe`` -> Nebius -> write-back). Since we validate on deployed
prod (decision 17, no dev shim), each hop emits a durable marker so a stalled
event is diagnosable after the fact. Cloud Run streams stdout to Cloud Logging,
so filtering the ``limon.pipeline`` logger (or the ``STEP=`` token) on a given
``recordId`` shows exactly how far that event travelled - and a *missing* line
tells you which stone it never reached.

Never logs audio bytes or transcript text: only ids and counts.
"""

import logging
import sys

logger = logging.getLogger("limon.pipeline")


def configure_logging() -> None:
    """Attach a stdout handler to the pipeline logger. Idempotent.

    Self-contained (``propagate=False`` + its own handler) so it neither depends
    on nor duplicates uvicorn's logging config: a bare ``logger.info`` would
    otherwise be dropped, because nothing configures a root handler at INFO.
    """
    if logger.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def step(name: str, **fields: object) -> None:
    """Emit one stepping-stone marker as ``STEP=<name> key=value ...``."""
    suffix = " ".join(f"{key}={value}" for key, value in fields.items())
    logger.info("STEP=%s %s", name, suffix)
