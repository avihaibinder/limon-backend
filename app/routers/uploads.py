from fastapi import APIRouter, HTTPException, status

from app.core.auth import CurrentUserDep
from app.schemas.upload import AudioUploadPresignRequest, AudioUploadPresignResponse
from app.services import storage as storage_service

router = APIRouter(prefix="/uploads", tags=["uploads"])


@router.post(
    "/audio/presign",
    response_model=AudioUploadPresignResponse,
    status_code=status.HTTP_201_CREATED,
)
async def presign_audio_upload(
    current_user: CurrentUserDep, payload: AudioUploadPresignRequest
) -> AudioUploadPresignResponse:
    """Issue a short-lived signed URL for the client to upload one audio file
    straight to GCS. The upload bytes never pass through this backend."""
    try:
        presigned = storage_service.presign_audio_upload(
            user_id=current_user.id, content_type=payload.content_type
        )
    except storage_service.UnsupportedContentTypeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported audio content type {payload.content_type!r}",
        ) from exc
    except storage_service.StorageNotConfiguredError as exc:
        # The deployment lacks a bucket; the caller did nothing wrong.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Uploads are not configured (set LIMON_GCS_BUCKET).",
        ) from exc
    return AudioUploadPresignResponse.model_validate(presigned)
