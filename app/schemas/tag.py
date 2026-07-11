from pydantic import BaseModel, ConfigDict, Field


class TagCreate(BaseModel):
    """Payload for creating a tag; the owner is the authenticated user."""

    name: str = Field(min_length=1, max_length=100, examples=["sleep"])


class TagUpdate(BaseModel):
    """Partial update payload; a tag cannot move between users."""

    name: str | None = Field(default=None, min_length=1, max_length=100)


class TagRead(BaseModel):
    """Tag as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str = Field(description="Owner of the tag.")
    name: str


class TagList(BaseModel):
    """Paginated collection of tags."""

    items: list[TagRead]
    total: int
    limit: int
    offset: int
