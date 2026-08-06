# Data model

Four tables: `users`, `events`, `tags`, `recordings`. Conventions (UUID4 `String(36)` ids,
tz-aware UTC timestamps) are in `architecture.md`; identity is in `auth.md`.

The client reads these columns **directly** from Supabase, so column names here are part of the
external contract, not an internal detail. See `realtime-reads.md` before renaming anything.

## `users`

`id` is the Supabase JWT `sub`, not a generated value (`auth.md`). `provider` records which
OAuth provider Supabase authenticated, for display only. `email` and `display_name` are seeded
at creation from token claims and thereafter owned by the user.

`demo_seeded_at` records when demo data was last added. It is **informational only** — nothing
reads it back, and it is re-stamped on every seed. What actually blocks re-seeding is the
account holding any event (`demo-seed.md`).

## `events`

The timeline item, and the only table the client reads for content.

| column | notes |
|---|---|
| `user_id` | FK → `users.id`, `ON DELETE CASCADE`, **not null**, indexed |
| `type` | `text` or `audio`. Only `audio` carries a recording and gets a transcript |
| `title` | nullable for **every** type; the server never requires one |
| `description` | the body: the user's text, or the transcript for audio |
| `occurred_at` | when it happened — *user content*, from the client's `clientCreatedAt` |
| `tag_ids` | JSON array of `tags.id` strings |
| `recording_id` | FK → `recordings.id`, nullable, **unique** (audio only) |
| `duration_sec` | recording length in whole seconds, mirrored from the recording |
| `suggested_location`, `tag_reasoning` | auto-tagging output; see `tagging.md` |
| `client_event_id` | idempotency key for create, nullable, unique |
| `created_at` / `updated_at` | row lifecycle, distinct from `occurred_at` |

**There is no `state` column.** "Transcript ready" is signalled by `description` becoming
non-null. The recording's state machine is internal and the client never sees it
(`transcription.md`).

**`occurred_at` versus `created_at`** is a real distinction, not redundancy: the first is when
the event happened in the user's life, the second is when the row was written. They differ
whenever a capture is retried, and the demo seed sets them years apart.

### `tag_ids` is a JSON array, not a join table

Tags are referenced by id, not by name. The array carries **no foreign key** — Postgres cannot
put one inside a JSON array — so referential integrity is the application's job, and readers
must tolerate ids of deleted tags by skipping unknown ones.

That tolerance is a backstop rather than the primary mechanism: deleting a tag detaches its id
from all the owner's events in the same transaction (`api.md`). The dangling case survives for
rows written by an older client or a partial failure.

The alternative — a normalized `event_tags(event_id, tag_id)` join table — buys real integrity
at the cost of a table and a join on every read. It stays the migration path if integrity ever
proves worth it. The array was chosen deliberately.

### `duration_sec` is denormalized on purpose

Audio length lives on `recordings.duration_sec` (where it belongs) **and** flat on
`events.duration_sec`. The client reads events straight from Supabase and never reads the
recordings table, so without the mirror the value would need a join the read path cannot
express. Written once at create; the idempotent retry never rewrites it.

## `tags`

`(user_id, name)` is unique — a user cannot hold two tags of the same name, which is what makes
create-by-name safely idempotent (`api.md`). `color` is an opaque display string (up to 32
chars, e.g. a pastel hex) that is never interpreted server-side; `null` means the client picks
its own default.

## `recordings`

One row per recorded audio file: `storage_key` (the GCS object), `content_type`, `byte_size`,
`duration_sec`, and the transcription state machine `state` (`pending → transcribing → done` /
`failed`) plus a short `error`. `state` is indexed.

**The client never reads this table.** It exists for worker coordination — specifically the
atomic claim that makes at-least-once task delivery safe (`transcription.md`). `error` holds a
short reason, never transcript text or endpoint internals.

## Schema changes: there are no migrations

`Base.metadata.create_all` runs on startup. It creates **missing tables** and nothing else — it
never adds a column to a table that already exists. On a fresh database (local SQLite, a
recreated Supabase project) everything appears correctly, which is exactly what makes this
dangerous: the gap only shows up in production.

**Consequence: deploying code with a new model column requires hand-applying the `ALTER` to the
live database first.** This has already caused one production outage — `POST /events` returned
`500` service-wide with `UndefinedColumnError: column events.duration_sec does not exist`,
because four columns added by two features had never been applied. Postgres reports only the
*first* missing column, so they had to be found one at a time.

Two operational notes that follow: production DDL is run by hand (`ops.md`), and Alembic is the
known fix, deferred rather than rejected (`roadmap.md`).

`create_all` also does not manage RLS policies or replication publications; `setup.sql` owns
those (`realtime-reads.md`).

For a **new** database, `scripts/supabase/create_tables.sql` builds the schema without needing a
running app pointed at it — the artifact you want when moving to another Postgres. It is
generated from these models by `scripts/supabase/gen_create_tables.py`, so regenerate it rather
than editing it when a model changes.
