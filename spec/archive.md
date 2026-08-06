# Archive

Designs that were agreed and then replaced. Kept only to explain why the current shape is what
it is, and to stop a future reader re-proposing something that was already tried.

Nothing here describes live code.

## The original architecture plan

The first backend design document (`docs/SPEC.md`, since removed). Its framing survives — two-speed data model, fast synchronous
CRUD plus a slow asynchronous path, Cloud Run scaling to zero — but most of its concrete choices
were replaced:

- **MinIO / S3 for blobs.** A `StorageClient` interface with an `S3StorageClient` implementation
  pointed at MinIO in compose, chosen so the concrete client could be swapped later. Replaced by
  Google Cloud Storage directly. The `LIMON_S3_*` environment variables it describes were never
  read by any code.
- **A `jobs` table** (`id, user_id, job_type, status, result_ref, error`) as one reusable async
  pattern for transcription, PDF export, and insights, with `GET /jobs/{id}` polling. Never
  built. Transcription instead uses the recording's own state machine plus Cloud Tasks, and the
  client learns of completion over Realtime rather than by polling. If PDF export and insights
  arrive, the question of a shared job abstraction reopens — but it should be argued from what
  transcription actually needed, not from this design.
- **Pub/Sub as the job queue**, publishing to one topic with a `job_type` discriminator.
  Pub/Sub survives only as the GCS-finalize transport; the work queue is Cloud Tasks, chosen for
  its capped retry budget.
- **`POST /events/{id}/attachment`** as the first blob usage. Never built; the signed-URL flow
  replaced it.
- **Local-first client with a sync layer** absorbing cold-start latency. This became Model B and
  was explicitly rejected — see below.
- **Alembic in Step 2.** Not done (`open-questions.md`).
- **`min-instances=1`** to remove cold starts. Not configured.

## Model B: local-first, audio-only

The client, as originally built, kept text notes in a local store and posted only audio events,
with no snapshot pull at launch. The backend was briefly designed toward this.

**Rejected in favour of Model A**, emphatically: the database is the source of truth for *every*
event, text and audio. The client posts everything and reads everything back.

The wording matters and was corrected once already: "the backend is the source of truth" is
loose shorthand. The database is *inside Supabase*, a separate managed Postgres reached over the
network. The backend does not contain it.

## Polling for transcripts (`CONTRACT.md` v1)

The first contract had the client poll a status field until the transcript appeared, over a
`POST /recordings` route that accepted the audio bytes directly and returned `202`.

Replaced by: one JSON create call returning a signed URL, the bytes going straight to storage,
and the transcript arriving over Realtime. There is no polling, no multipart upload to the
backend, and no separate confirm call — the object landing in storage *is* the trigger.

The v1 shape also had its own vocabulary: a `records` table holding the text, with `recordings`
as a child. That collapsed into `events` plus `recordings` (`data-model.md`).

## The `lemon` event type

`type` was once `lemon | text | audio`, where `lemon` was the one-tap quick capture. Removed:
a lemon press is a `text` event with a null title and null description.

The gain was concrete — no code needs a three-way branch, and the only distinction that carries
weight is whether an event has a recording.

## `provider_subject` on users

`users` once carried its own generated id plus `provider` and `provider_subject`, with a
`(provider, provider_subject)` unique constraint, and lookups went through that pair. Since
`provider_subject` only ever mirrored the Supabase `sub`, both it and the constraint were
dropped and `users.id` became the `sub` itself (`auth.md`).

This is why the original design document referred to `get_user_by_provider_subject`.

## The dev shim

`POST /internal/dev/transcribe/{recordId}`, guarded by debug mode, ran the transcription worker
directly — skipping Pub/Sub and Cloud Tasks — so the pipeline could be exercised offline.

Removed. Validation happens **only on deployed production**, because that is the only place the
event-driven chain actually exists; a shim that skips four hops proves nothing about them. The
`STEP=` markers were added in the same change as the replacement for the visibility the shim was
providing.

`LIMON_LOCAL_AUDIO_DIR` survives and is unrelated: it is how the *storage read* is stubbed
offline, not a way to trigger the worker.

## The standalone presign route

`POST /uploads/audio/presign` existed briefly as its own route, returning a signed URL for a key
it minted itself. Deleted in favour of signing inside `POST /events`, which is what makes the
audio flow a single call. The signer survives as `storage.presign_put(object_key, content_type)`,
taking the key as an *input* so the events service controls it.

## `tags` as a deny-all table

Round 5 settled that the client would read its tag list through `GET /tags`, keeping `tags`
deny-all with no publication — a smaller RLS surface, on the reasoning that a leaked anon key
should not be able to enumerate tags.

Superseded twice. First when concurrent phone-and-web use required live tag sync, which needs an
owner-only `SELECT` policy and a publication entry. Then, once that policy existed, `tags` was
configured identically to `events` and the API's read routes had no reason to exist — so
`GET /tags`, `GET /tags/{id}`, and `GET /events` were deleted (`realtime-reads.md`).

## Paged list endpoints

`GET /events` supported `limit`, `offset`, and a `tag` filter; `GET /tags` was paged and
alphabetical. Both are gone.

The tag filter is worth one note: it was implemented with SQLite's `json_each`, which does not
exist on Postgres. It was a portability bug waiting to surface in production, and the route was
deleted before it ever did.

## The one-shot demo seed

Seeding was originally blocked by `users.demo_seeded_at`, making it a once-per-account action,
and the dataset was 10 events and 6 tags with timestamps rebased onto "now".

Now: 46 events and 16 tags at fixed calendar dates, blocked only by the account holding events,
and repeatable (`demo-seed.md`).

## Frankfurt

Everything once ran in `europe-west3` to match the Supabase region. Production is `us-east1`
only; the cross-region latency to the EU database is known and accepted. The deploy scripts still
default to the old region, which is a live foot-gun rather than history (`ops.md`).
