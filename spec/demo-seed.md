# Demo history

`POST /users/me/demo-data` backfills the caller's account with a demo timeline so the app can be
shown populated. It is triggered by a button, never automatically.

**16 tags and 46 text events**, dated 09–25 July 2026. The dataset lives in
`app/services/demo_seed.py`, sourced from `spec-local/mock_data/DEMO_SEED.mock-data.md`.

## The rules

**Existing events are the only blocker.** An account holding any event gets `409`; existing tags
are reused by name and do not block. So a user who already created tags can still seed.

**It is not one-shot.** Delete every event and the button works again. An earlier design blocked
on `users.demo_seeded_at`; that check is gone. The column is still stamped on every successful
seed, but nothing reads it back — it is a record of *when* demo data was last added, not a gate
(`data-model.md`).

Tags are reused by name, and a tag the user already owns **keeps its own color** rather than
being repainted to the seed's palette.

Responses here are **snake_case**, like the rest of `/users`, not the camelCase of `/events`.

## Deliberate deviations in the data

These look like bugs and are not:

- **Nine rows were recordings in the source, and are seeded as `text` events.** A seeded
  recording would have no audio behind it and nothing to play. They keep their
  `הקלטה (M:SS)` title verbatim and carry the transcript as the description, with no
  `recording_id`.
- **Those nine also carry `duration_sec`**, parsed out of the title. This contradicts the audio
  contract's "text events never send it" (`api.md`) — the seed writes the ORM directly and the
  duration is wanted for display. **It is safe only while the client decides "is this audio?"
  from `type` rather than from `durationSec != null`.** If that ever changes, this breaks.
- **Rows with no title and no description are lemon presses** — the user tapped the lemon for an
  instant event and only added tags. Empty is the content.
- **Two rows carry no tags at all.** Intentional, not missing data.

## Timestamps do not move

The source times are real and are kept **as written**, interpreted as Israel local time. They
are not rebased onto "now", which an earlier seed did.

The consequence is that **the demo ages**: it is a fixed window in July 2026, drifting further
into the past. That was the trade — real, coherent timestamps that tell a consistent story,
rather than a timeline that is always "this week" but internally arbitrary.

The whole range sits inside Israeli DST, so a constant UTC+3 offset is correct and no timezone
database is needed. Extending the dataset outside that window would break that assumption.

## Client contract

`spec-local/FE_DEMO_SEED.md` is the frontend-facing document; the frontend repo holds its own
copy. One line of it is stale: it says to show the button when the fetched events list has
`total: 0`. There is no list route any more (`realtime-reads.md`) — the condition is simply that
the timeline is empty, which the client now determines from its Supabase snapshot.
