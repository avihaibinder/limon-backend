"""Throwaway manual check for app.services.tagger against the real Nebius endpoint.

Not a pytest test and not wired into the app -- run by hand to sanity-check the
request shape (structured output, the disabled-thinking-mode assumption) against
the live API before trusting it in the Cloud Tasks pipeline. Uses whatever
LIMON_TAGGER_* is in .env, same as the app would; makes one real, billed call.

Usage: uv run python scripts/test_tagging_manual.py
"""

import asyncio

from app.services import tagger

_SAMPLE_TEXT = (
    "היום התעוררתי מוקדם והלכתי לרוץ בפארק הירקון בתל אביב. "
    "אחר כך פגשתי את אמא שלי לארוחת בוקר, ודיברנו הרבה על העבודה החדשה שלי. "
    "הרגשתי די לחוץ לקראת הפגישה של אחר הצהריים, אבל בסופו של דבר היא הלכה טוב."
)

_SAMPLE_TAGS = [
    {"id": "tag-1", "name": "ספורט"},
    {"id": "tag-2", "name": "משפחה"},
    {"id": "tag-3", "name": "עבודה"},
    {"id": "tag-4", "name": "בריאות נפשית"},
    {"id": "tag-5", "name": "בישול"},
]


async def main() -> None:
    print("Sending one live request to the tagger endpoint...")
    print(f"  base_url: {tagger.get_settings().tagger_base_url}")
    print(f"  model:    {tagger.get_settings().tagger_model}")
    print()

    try:
        result = await tagger.suggest_tags(_SAMPLE_TEXT, _SAMPLE_TAGS)
    except tagger.TaggerError as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}")
        raise

    print("Result:")
    print(f"  sentiment:          {result.sentiment}")
    print(f"  suggested_location: {result.suggested_location!r}")
    print(f"  tag_ids:            {result.tag_ids}")
    tag_names = {t["id"]: t["name"] for t in _SAMPLE_TAGS}
    print(f"  (resolved names:    {[tag_names.get(i, '???') for i in result.tag_ids]})")
    print(f"  reasoning:          {result.reasoning}")

    unknown = [i for i in result.tag_ids if i not in tag_names]
    if unknown:
        # suggest_tags() already filters these out server-side -- if this ever
        # prints, the filter itself is broken, not the model.
        print(f"  !! unexpected: unfiltered unknown tag ids leaked through: {unknown}")


if __name__ == "__main__":
    # tagger.suggest_tags is async (httpx.AsyncClient); this is a plain script,
    # not an ASGI app, so drive it with asyncio.run directly.
    asyncio.run(main())
