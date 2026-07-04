from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserBase(BaseModel):
    """Fields shared by create and read representations."""

    provider: str = Field(min_length=1, max_length=50, examples=["google"])
    provider_subject: str = Field(
        min_length=1,
        max_length=255,
        description="The `sub` claim from the provider's token.",
        examples=["10769150350006150715113082367"],
    )
    email: EmailStr | None = None
    display_name: str | None = Field(default=None, max_length=200)


class UserCreate(UserBase):
    """Payload for creating a user. Once OAuth lands, users are provisioned
    automatically from verified tokens instead of via this payload.
    """


class UserUpdate(BaseModel):
    """Partial update payload; provider identity fields are immutable."""

    email: EmailStr | None = None
    display_name: str | None = Field(default=None, max_length=200)


class UserRead(UserBase):
    """User as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime
    updated_at: datetime


class UserList(BaseModel):
    """Paginated collection of users."""

    items: list[UserRead]
    total: int
    limit: int
    offset: int
