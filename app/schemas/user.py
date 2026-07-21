from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserUpdate(BaseModel):
    """Partial update payload; provider identity fields are immutable."""

    email: EmailStr | None = None
    display_name: str | None = Field(default=None, max_length=200)


class UserRead(BaseModel):
    """User as returned by the API. Users are provisioned automatically from
    verified Supabase tokens — there is no create payload.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(
        description="The Supabase user id (`sub` claim); our own primary key too.",
        examples=["8f1c2b34-0000-4000-8000-000000000000"],
    )
    provider: str = Field(examples=["google"])
    email: EmailStr | None = None
    display_name: str | None = Field(default=None, max_length=200)
    created_at: datetime
    updated_at: datetime
