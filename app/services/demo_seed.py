"""Demo history backfilled into an account with no events, on request.

Triggered by ``POST /users/me/demo-data`` (an FE button), never automatically.
The router guards the call: 409 if the account already has events; this module
only seeds. Events are the *only* blocker -- an account that already has tags
can still seed, and an account that seeds, deletes every event and presses the
button again gets a fresh copy.

Source: spec-local/mock_data/DEMO_SEED.mock-data.md.

Shape of the dataset (46 rows, 09/07/2026 - 25/07/2026):
- Every seeded event is ``text``. Nine rows are recordings in the source, but a
  seeded recording would have no audio behind it, so they are seeded as text
  events that keep the ``הקלטה (M:SS)`` title verbatim and carry the transcript
  as the description.
- Those nine also carry ``duration_sec``, parsed out of their title. This is a
  deliberate deviation from FE_CONTRACT.audio-duration.md ("text events never
  send it"): the seed writes the ORM directly, and the duration is wanted for
  display. They have no ``recording_id`` -- there is no player in the FE.
- Rows with no title and no description are "lemon press" events: the user
  tapped the lemon for an instant event and only added tags.
- Two rows carry no tags at all; that is intentional, not missing data.

Timestamps are absolute, NOT rebased onto "now" (unlike the earlier seed): the
source times are real and are kept as written, interpreted as Israel local time.
The whole range sits inside Israeli DST, so the offset is a constant UTC+3.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import Event
from app.models.tag import Tag
from app.models.user import User
from app.services import tags as tags_service

# Israel Daylight Time. Every row below falls between 09/07/2026 and 25/07/2026,
# well inside DST, so a fixed offset is correct here and no tzdata is needed.
_IDT = timedelta(hours=3)

# Tag name -> color. Soft pastels; the five names carried over from the previous
# seed keep their original hex, and `שינה` reclaims the cyan freed by dropping
# `חלום רע`. A tag the user already created keeps its own color (see below).
_TAGS: dict[str, str] = {
    "משפחה": "#fcddca",
    "עבודה": "#ede3fc",
    "ריב": "#fcd7dd",
    "רעש": "#e5e4cf",
    "תחושה רעה": "#dcebdd",
    "שינה": "#d6fcfc",
    "עומס": "#ffe0b3",
    "נסיעה": "#cfe0fa",
    "צפיפות": "#ecd4f5",
    "הצלחה": "#c9f0cd",
    "בית": "#faf0c8",
    "סופר": "#cdf3e4",
    "חברה": "#f9d5f0",
    "הימנעות": "#d5d8e8",
    "גופני": "#e8d3c0",
    "המתנה": "#e6e6e6",
}

# One row per seed event, in source order:
#   (date, time, title, tag names, description, duration_sec)
# `title`/`description` are None where the source column was blank. `duration_sec`
# is set only on the nine recording rows, parsed from their title.
_SEED_EVENTS: list[tuple[str, str, str | None, list[str], str | None, int | None]] = [
    (
        "2026-07-09",
        "08:10",
        "התעוררתי אחרי לילה לא שקט",
        ["שינה", "תחושה רעה"],
        "התעוררתי כמה פעמים והרגשתי עייפות וכבדות בבוקר.",
        None,
    ),
    ("2026-07-09", "14:25", "פגישה שהתארכה", ["עבודה", "עומס"], None, None),
    (
        "2026-07-09",
        "21:40",
        "הקלטה (0:09)",
        ["משפחה", "ריב"],
        "השיחה בבית התפתחה לוויכוח והרגשתי שאני נסגרת ולא מצליחה להסביר מה מפריע לי.",
        9,
    ),
    (
        "2026-07-10",
        "09:05",
        "האוטובוס היה צפוף",
        ["נסיעה", "צפיפות"],
        "ירדתי תחנה אחת לפני כי הרגשתי שאין לי מספיק אוויר.",
        None,
    ),
    ("2026-07-10", "16:50", None, ["תחושה רעה"], None, None),
    (
        "2026-07-10",
        "22:15",
        "הצלחתי לצאת להליכה",
        ["הצלחה"],
        "לא היה לי כוח לצאת, אבל אחרי כמה דקות בחוץ הרגשתי קצת יותר רגועה.",
        None,
    ),
    ("2026-07-11", "10:30", "רעש מהשיפוץ של השכנים", ["רעש", "בית"], None, None),
    (
        "2026-07-11",
        "18:20",
        "הקלטה (0:11)",
        ["משפחה"],
        "בארוחה הרגשתי שכולם מדברים אליי בבת אחת. יצאתי לכמה דקות למרפסת כדי להירגע.",
        11,
    ),
    ("2026-07-12", "07:45", None, ["שינה"], None, None),
    (
        "2026-07-12",
        "13:10",
        "אזעקה של רכב בחניון",
        ["רעש", "תחושה רעה"],
        "הצליל תפס אותי לא מוכנה ונשארתי דרוכה גם אחרי שהוא הפסיק.",
        None,
    ),
    ("2026-07-12", "20:05", "ארוחת ערב אצל ההורים", ["משפחה"], None, None),
    (
        "2026-07-13",
        "08:55",
        "קושי להתרכז בישיבה",
        ["עבודה", "עומס"],
        "התקשיתי לעקוב אחרי השיחה וביקשתי שיחזרו על הדברים.",
        None,
    ),
    ("2026-07-13", "15:35", None, ["תחושה רעה"], None, None),
    (
        "2026-07-13",
        "23:10",
        "הקלטה (0:08)",
        ["שינה"],
        "אני עייפה אבל לא מצליחה להירדם. כל רעש קטן מקפיץ אותי.",
        8,
    ),
    ("2026-07-14", "11:20", "קניות במכולת", ["סופר", "רעש"], None, None),
    (
        "2026-07-14",
        "17:45",
        "שיחה טובה עם המנהלת",
        ["עבודה", "הצלחה"],
        "אמרתי שאני צריכה יותר זמן למשימה והיא קיבלה את זה בלי ויכוח.",
        None,
    ),
    ("2026-07-15", "06:50", "התעוררתי מחלום", ["שינה", "תחושה רעה"], None, None),
    (
        "2026-07-15",
        "12:40",
        "הקלטה (0:14)",
        ["עבודה", "עומס"],
        "קיבלתי כמה משימות בבת אחת והרגשתי שהכול נסגר עליי. יצאתי לשירותים ונשמתי כמה דקות.",
        14,
    ),
    (
        "2026-07-15",
        "19:30",
        "ביטלתי מפגש עם חברים",
        ["חברה", "הימנעות"],
        "כתבתי שאני לא מרגישה טוב, למרות שפשוט לא הצלחתי להביא את עצמי לצאת.",
        None,
    ),
    ("2026-07-16", "09:15", "פקק בדרך לעבודה", ["נסיעה", "רעש"], None, None),
    ("2026-07-16", "14:05", None, ["עבודה", "תחושה רעה"], None, None),
    (
        "2026-07-16",
        "22:00",
        "הצלחתי להירגע לבד",
        ["הצלחה"],
        "כיביתי את הטלפון, שתיתי מים וישבתי כמה דקות בשקט.",
        None,
    ),
    ("2026-07-17", "10:50", "ילדים צעקו בחדר המדרגות", ["רעש", "בית"], None, None),
    (
        "2026-07-17",
        "16:25",
        "הקלטה (0:10)",
        ["משפחה", "ריב"],
        "התעצבנתי בשיחה עם אחותי והרמתי את הקול. אחר כך הרגשתי אשמה.",
        10,
    ),
    ("2026-07-18", "08:30", "קמתי עם כאב ראש", ["תחושה רעה", "גופני"], None, None),
    (
        "2026-07-18",
        "13:55",
        "סופר עמוס",
        ["סופר", "צפיפות"],
        "השארתי את העגלה ויצאתי לפני שסיימתי את הקניות.",
        None,
    ),
    ("2026-07-18", "20:45", "ישבתי עם חברה בבית קפה", ["חברה", "הצלחה"], None, None),
    ("2026-07-19", "09:40", None, ["שינה"], None, None),
    ("2026-07-19", "14:30", "הטלפון צלצל בלי הפסקה", ["עבודה", "עומס"], None, None),
    (
        "2026-07-19",
        "23:20",
        "הקלטה (0:06)",
        ["רעש"],
        "יש מוזיקה חזקה מהרחוב ואני לא מצליחה להירגע.",
        6,
    ),
    ("2026-07-20", "07:25", "רעש של משאית ברחוב", ["רעש", "תחושה רעה"], None, None),
    (
        "2026-07-20",
        "12:15",
        "הצגתי בישיבה",
        ["עבודה", "הצלחה"],
        "הייתי לחוצה לפני, אבל הצלחתי לסיים את ההצגה כמו שתכננתי.",
        None,
    ),
    ("2026-07-20", "18:35", None, ["משפחה"], None, None),
    (
        "2026-07-21",
        "08:00",
        "הקלטה (0:13)",
        ["נסיעה", "תחושה רעה"],
        "מישהו צפר מאחוריי והרגשתי שהגוף שלי ננעל. עצרתי בצד עד שהנשימה נרגעה.",
        13,
    ),
    ("2026-07-21", "15:10", "ביקור אצל הרופא", ["המתנה", "גופני"], None, None),
    (
        "2026-07-21",
        "21:50",
        "ויכוח עם בן הזוג",
        ["משפחה", "ריב"],
        "ניסיתי להסביר שאני צריכה שקט, אבל זה יצא כעס.",
        None,
    ),
    ("2026-07-22", "09:35", "קניות בסופר", ["סופר", "צפיפות"], None, None),
    ("2026-07-22", "16:40", "הצלחתי לענות להודעה שדחיתי", ["הצלחה"], None, None),
    ("2026-07-22", "22:30", None, ["שינה", "תחושה רעה"], None, None),
    # No tags in the source; intentional.
    ("2026-07-23", "18:01", "קניות בסופר", [], None, None),
    (
        "2026-07-23",
        "21:13",
        "הקלטה (0:12)",
        ["משפחה"],
        "היה לי ריב עם אמא בטלפון. אני עדיין מוצפת ולא מבינה למה הגבתי ככה.",
        12,
    ),
    ("2026-07-23", "23:26", None, ["תחושה רעה"], None, None),
    ("2026-07-24", "10:23", "קניות", ["רעש", "סופר"], None, None),
    # No tags in the source; intentional.
    (
        "2026-07-25",
        "04:36",
        "הקלטה (0:07)",
        [],
        "התעוררתי מרעש בחוץ והלב התחיל לדפוק. קשה לי לחזור לישון.",
        7,
    ),
    ("2026-07-25", "15:17", None, ["משפחה", "ריב"], None, None),
    ("2026-07-25", "19:42", "רעש של אופנוע מהרחוב", ["רעש", "תחושה רעה"], None, None),
]


def _occurred_at(date: str, time: str) -> datetime:
    """Parse a source ``date``/``time`` pair (Israel local) into a UTC instant."""
    local = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
    return (local - _IDT).replace(tzinfo=UTC)


async def seed_demo_data(session: AsyncSession, *, user: User) -> User:
    """Create the demo tags and events for ``user`` and stamp ``demo_seeded_at``.

    The caller has already verified the account has no events. Tags are matched
    by name: one the user already created is reused rather than duplicated, so
    the (user_id, name) unique constraint holds and the user's own color is not
    overwritten. Commits once, so the events and the mark land atomically.

    ``demo_seeded_at`` records when demo data was last added; it is not a gate
    (re-seeding an emptied account is allowed), so it may be overwritten.
    """
    tags: dict[str, Tag] = {}
    for name, color in _TAGS.items():
        tag = await tags_service.get_tag_by_name(session, user.id, name)
        if tag is None:
            tag = Tag(user_id=user.id, name=name, color=color)
            session.add(tag)
        tags[name] = tag
    # Flush so newly created tags get their generated ids before events reference them.
    await session.flush()

    for date, time, title, tag_names, description, duration_sec in _SEED_EVENTS:
        session.add(
            Event(
                user_id=user.id,
                type="text",
                title=title,
                description=description,
                duration_sec=duration_sec,
                occurred_at=_occurred_at(date, time),
                tag_ids=[tags[name].id for name in tag_names],
            )
        )
    user.demo_seeded_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(user)
    return user
