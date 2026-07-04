from pydantic import BaseModel, ConfigDict, Field


class TagBase(BaseModel):
    """Fields shared by create and read representations."""

    user_id: str = Field(description="Owner of the tag.")
    name: str = Field(min_length=1, max_length=100, examples=["sleep"])


class TagCreate(TagBase):
    """Payload for creating a tag."""


class TagUpdate(BaseModel):
    """Partial update payload; a tag cannot move between users."""

    name: str | None = Field(default=None, min_length=1, max_length=100)


class TagRead(TagBase):
    """Tag as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: str


class TagList(BaseModel):
    """Paginated collection of tags."""

    items: list[TagRead]
    total: int
    limit: int
    offset: int
