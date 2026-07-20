"""Internal worker routes. Mounted at the root (no /api/v1) and without the
user-JWT gate: these are called by Cloud Tasks, not the app.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.dependencies import SessionDep
from app.schemas.transcription import TranscribeTask
from app.services import transcription

router = APIRouter(prefix="/internal", tags=["internal"])


async def require_internal_auth(
    x_internal_token: Annotated[str | None, Header(alias="X-Internal-Token")] = None,
) -> None:
    """Interim guard for the worker endpoint.

    Production auth is Cloud Tasks OIDC (a Google-signed JWT verified against
    Google's certs with an expected audience); that is wired in domain 04 / deploy.
    Until then: if LIMON_INTERNAL_TASK_TOKEN is set we require a matching header;
    if unset, the endpoint is open (local dev only).
    """
    expected = get_settings().internal_task_token
    if expected is None:
        return
    if x_internal_token != expected:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


@router.post("/transcribe")
async def transcribe(
    session: SessionDep,
    payload: TranscribeTask,
    _auth: Annotated[None, Depends(require_internal_auth)],
) -> JSONResponse:
    outcome = await transcription.run_transcription(session, payload.record_id)
    if outcome.status == "retry":
        headers = {}
        if outcome.retry_after is not None:
            headers["Retry-After"] = str(int(outcome.retry_after))
        return JSONResponse({"status": "retry"}, status_code=503, headers=headers)
    return JSONResponse({"status": outcome.status}, status_code=200)
