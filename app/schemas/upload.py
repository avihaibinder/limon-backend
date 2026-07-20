import datetime as dt

from pydantic import BaseModel, ConfigDict, Field


class AudioUploadPresignRequest(BaseModel):
    """Ask for a short-lived URL to upload one audio file directly to GCS."""

    content_type: str = Field(
        description="MIME type of the audio the client will PUT (e.g. audio/mp4 for .m4a).",
        examples=["audio/mp4"],
    )


class AudioUploadPresignResponse(BaseModel):
    """A presigned PUT target. The client uploads the bytes itself, then
    references object_key when creating the voice note."""

    model_config = ConfigDict(from_attributes=True)

    upload_url: str = Field(
        description="Signed URL to PUT the file to, with this exact content-type."
    )
    object_key: str = Field(
        description="GCS object key the file will live at; store this server-side."
    )
    content_type: str
    expires_at: dt.datetime = Field(
        description="After this instant the upload_url is rejected by GCS."
    )
