from pydantic import BaseModel, ConfigDict, Field, field_validator


def _strip_name(value: object) -> object:
    """Trim surrounding whitespace before length validation, so a
    whitespace-only name fails min_length (422) rather than slipping through."""
    return value.strip() if isinstance(value, str) else value


class TagCreate(BaseModel):
    """Payload for creating a tag; the owner is the authenticated user."""

    name: str = Field(min_length=1, max_length=100, examples=["sleep"])
    color: str | None = Field(
        default=None,
        max_length=32,
        description="Opaque display color (e.g. a hex string); never interpreted server-side.",
        examples=["#f9d9a0"],
    )

    _trim_name = field_validator("name", mode="before")(_strip_name)


class TagUpdate(BaseModel):
    """Partial update payload; a tag cannot move between users.

    ``color: null`` clears the color; an omitted key leaves the field untouched
    (services apply with ``exclude_unset``).
    """

    name: str | None = Field(default=None, min_length=1, max_length=100)
    color: str | None = Field(default=None, max_length=32)

    _trim_name = field_validator("name", mode="before")(_strip_name)


class TagRead(BaseModel):
    """Tag as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str = Field(description="Owner of the tag.")
    name: str
    color: str | None


class TagList(BaseModel):
    """Paginated collection of tags."""

    items: list[TagRead]
    total: int
    limit: int
    offset: int
