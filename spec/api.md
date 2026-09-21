# HTTP API

App-facing routes are mounted under `/api/v1` and require a Supabase JWT (`auth.md`). Worker
routes live at the root under `/internal` and are not user-gated. `/health` is public and
returns `{"status": "ok"}`.

**The API is write-only for app data.** There are no list routes; reads come from Supabase
(`realtime-reads.md`). `GET /events/{id}` remains as a single-row fallback.

Bodies and responses are camelCase; timestamps are ISO-8601 out, and the one input in another
format is `clientCreatedAt` (epoch milliseconds).

## Scoping: 404, never 403

Every route is scoped to the caller, and a row belonging to someone else is answered **`404`,
not `403`** — a `403` would confirm the id exists. This holds for read, update, and delete
alike. The client never sends a user id; if it does, it is ignored.

## Events

| Method | Path | |
|---|---|---|
| `POST` | `/events` | create — `201` |
| `GET` | `/events/{id}` | fallback single read |
| `PATCH` | `/events/{id}` | partial update; only provided fields change |
| `DELETE` | `/events/{id}` | `204` |

### `POST /events`

One call creates the event and, for audio, everything the upload needs:

```json
{ "type": "audio", "title": "…", "description": null, "tagIds": ["…"],
  "clientCreatedAt": 1737000000000, "clientEventId": "<uuid>", "durationSec": 15 }
```

Response `201`: `{ "event": {…}, "recordId": "<uuid>|null", "signedUrl": "https://…|null" }`.

- **`type` is `text` or `audio`.** There is no third type. The "press the lemon" quick capture
  is a `text` event with a null title and null description — an empty text note *is* the lemon.
  Collapsing the old `lemon` type removed every three-way branch in the codebase; the only
  distinction that carries weight is "has a recording" versus "does not".
- **Audio** additionally creates the `pending` recording row and returns a signed PUT URL for
  `v0/{userId}/{recordId}.m4a`. Text gets neither, and both `recordId` and `signedUrl` are null.
- **`description` is accepted on create** even though the original contract's create body
  omitted it. This route is shared with text notes, whose body *is* the description; dropping it
  would force a second call to write a note. Audio leaves it null and the transcript fills it in.
- **`clientCreatedAt` is required** — there is no server default, because it is user content
  ("when it happened"), not a row timestamp.
- **`durationSec`** is optional, audio only, whole seconds `>= 0`; `0` is valid, negative or
  non-integer is `422`. Absent or null means unknown length. Stored on the recording and
  mirrored onto the event (`data-model.md`).
- Unknown body fields are **ignored**, not rejected — the model is not `extra="forbid"`. This is
  deliberate and load-bearing for rollout: the client can start sending a new field before the
  backend understands it, so the two repos never need a synchronized deploy.

**Idempotency.** `clientEventId` makes create retry-safe: a repeat returns the *same* event with
a **freshly minted** `signedUrl`, never a duplicate row. The dedupe is **durable, not
time-boxed** — it is a unique column on the row, so the same key returns the same event for as
long as that event exists. One key can safely cover a whole recording take with no expiry
window to reason about. The fresh URL matters because the
original may have expired — re-creating with the same key is exactly how a client recovers from
an expired upload URL. Nothing else is rewritten on the retry path, including `durationSec`.

**Ordering on the audio path.** The URL is signed *before* anything is persisted, so a signing
failure leaves no event stranded without a way to upload to it. The recording row is flushed
before the event insert, since no ORM relationship orders the two foreign keys.

If audio is requested with no bucket configured, the route answers `503` — the caller did
nothing wrong.

### `PATCH /events/{id}`

Only provided fields change (`title`, `description`, `occurredAt`, `tagIds`). Success is **`200`
with the full updated event**, not `204` — the client reconciles its optimistic card from that
body, so returning no body would read as a failure and revert an edit the server applied.

Explicit `title: null` / `description: null` clear those fields. **`tagIds` must always be an
array when sent; `tagIds: null` is not supported** because the column is non-null.

Editing the title or description of an untagged event re-triggers auto-tagging (`tagging.md`).

### Deletes are idempotent

`DELETE` on events and tags returns `204`, and `404` for a missing or foreign id. The client
treats `404` as success — the row is absent either way — which is safe precisely because a
foreign id is indistinguishable from a missing one.

### Validation failures are `422`

FastAPI's default, not `400`: over-length or empty-after-trim names, malformed bodies, a negative
`durationSec`. Only `401`, `404`, and `409` carry specific meaning; everything else non-2xx is
one generic failure.

## Tags

| Method | Path | |
|---|---|---|
| `POST` | `/tags` | upsert by name — `201` new, `200` existing |
| `PATCH` | `/tags/{id}` | rename or recolor; `409` on name clash |
| `DELETE` | `/tags/{id}` | `204`, and detaches the tag from the owner's events |

**Create is an upsert, not a strict create.** A name the caller already holds returns the
existing tag with `200` rather than `409`. This is what lets the client dedupe by name with no
idempotency key and no pre-flight check: creating "sleep" twice is simply not an error. The
**existing color is never overwritten** — create is a retry-safe dedupe, not an edit. Use
`PATCH` to change a color.

Names are trimmed before validation, so a whitespace-only name fails `min_length` with `422`
rather than being stored. Matching is **exact after trim: case-sensitive, no Unicode
normalization**, per user — `Sleep` and `sleep` are two different tags. Max length is 100.
`(user_id, name)` is unique, and a lost race against a concurrent create of the same name
resolves to the winner's row instead of surfacing the constraint error.

`color` is opaque (up to 32 chars) and never interpreted server-side. On `PATCH`, `color: null`
clears it while an omitted key leaves it untouched.

**Delete detaches.** Deleting a tag removes its id from every one of the owner's events in the
same transaction. Without this, ids would dangle in `tag_ids` forever and sync to every future
device (`data-model.md`). Each touched event gets a fresh `updated_at`, so production echoes it
over Realtime as a normal UPDATE — the client sees the tag vanish from its cards without a
refetch. The tag DELETE and the event UPDATEs come from one transaction and may arrive in
either order.

## Users

| Method | Path | |
|---|---|---|
| `GET` | `/users/me` | the caller's profile |
| `PATCH` | `/users/me` | update `email` / `displayName` |
| `POST` | `/users/me/demo-data` | backfill demo history — `201`, `409` if the account has events |
| `DELETE` | `/users/me` | delete the account — `204`, `502` if Supabase fails |

Self-service only: there is no `POST /users` and no route takes a user id. Accounts are created
implicitly on the first authenticated request (`auth.md`), and provider identity is immutable.

See `demo-seed.md` and `auth.md` for the two non-obvious routes here.

## Internal worker routes

Mounted at the root, outside `/api/v1`, without the user gate. Called by Cloud Tasks and Pub/Sub
— never by the app.

| Method | Path | Called by |
|---|---|---|
| `POST` | `/internal/uploaded` | Pub/Sub push, on GCS object-finalize |
| `POST` | `/internal/transcribe` | Cloud Tasks — `{"recordId": …}` |
| `POST` | `/internal/tag` | Cloud Tasks — `{"eventId": …}` |
| `POST` | `/internal/transcripts-ready` | The transcriber box, when results are waiting |
| `POST` | `/internal/transcripts-sweep` | Cloud Scheduler, daily |

`/internal/transcribe` and `/internal/tag` answer `200` for terminal outcomes and **`503` with
`Retry-After`** when the work should be retried, which is how the queue's backoff is driven
(`transcription.md`).

`/internal/transcripts-ready` is the transcriber's callback. It always answers `200` once the
collection cycle has run: the cycle is idempotent and anything it could not store stays on the box,
so there is nothing a retry would fix that the next drain will not. The body is a nudge carrying no
transcript and is ignored except for logging — the endpoint drains *everything* ready, not the job
the body names. Unknown body fields are tolerated on purpose, because a `422` reads as a `4xx` to
the box and a `4xx` is deliberately not retried.

**These two are the exception to the line below**: they authenticate with the transcriber's shared
secret (`X-Callback-Token`) and **fail closed** — an unset secret rejects rather than opens. An
unset secret answers `503` and a wrong one `401`, which is not cosmetic: the box retries the first
and not the second (`transcription.md`).

`/internal/uploaded` distinguishes three cases deliberately: it **ACKs with `204`** for a
well-formed message it chose not to act on (wrong event type, or a key that is not one of ours),
so Pub/Sub stops redelivering something that will never be acted on; but it lets an **enqueue
failure propagate as `5xx`** so a real finalize notification is retried. The three `204`s are
otherwise indistinguishable, which is why each emits its own `STEP=` marker.

These routes are currently **unauthenticated in production** — see `security.md`.
