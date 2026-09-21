# Open questions and known defects

Decisions not yet made, and defects with no owner. Distinct from `roadmap.md`, which is work
that is understood and merely unscheduled.

## Defects

### The transcriber's base URL is a Quick Tunnel and rotates without warning

The box is reachable only through a Cloudflare Quick Tunnel whose address is reminted whenever
`cloudflared` restarts, and there is no API to discover it — the startup banner is the only place it
exists. While it is stale every submission fails soft and work piles up as `pending` until someone
notices and updates `LIMON_TRANSCRIBER_BASE_URL`.

Nothing is lost — the audio stays in GCS and the work is recoverable indefinitely — but
transcription silently stops, and **recovery does not resume on its own the moment the URL is
fixed.** Since recovery rides on the box's callback and nothing is reaching the box, it takes the
next recording (or a manual `/internal/transcripts-sweep`) to restart the pipeline
(`transcription.md`).

A stable ingress is queued work in the transcriber's own repo (its Batch K) and is not ours to
build. **Nothing here alerts on it today** — the first symptom is transcripts not appearing.

### The box is one queue with no per-caller scoping

Anyone holding `AUTH_TOKEN` can acknowledge — and therefore permanently delete — any result on the
box, including one they never submitted. Our production drain is unfiltered, which is correct while
LimON is the only caller and destructive the moment it is not. If a second consumer ever appears,
the drain has to filter by ids we submitted and this design changes shape (`transcription.md`).

### Auto-tagging is failing in production

`TaggerResponseError` on every event since the 2026-09-21 deploy. The code merged on 2026-09-20
moved tagging to Groq, but the Cloud Run environment still points at Nebius
(`LIMON_TAGGER_BASE_URL=https://api.tokenfactory.nebius.com/v1/`, model `Qwen/Qwen3-32B`, secret
`nebius_token_factory_tagger_api_key`), and env vars override the code's defaults. There is no Groq
key in the project.

Whether the Nebius key expired or the model moved is unknown — the error is deliberately not
echoed into the logs. Transcription is unaffected: tagging is best-effort and cannot undo a stored
transcript. The fix is a Groq API key plus the three matching env vars (`ops.md`).

### Nothing alerts when the pipeline dies

Three failures so far are all silent, and each is only visible by going and looking:

- the Pub/Sub push subscription expiring (`ops.md`), which stops transcription starting at all
- the transcriber's tunnel URL rotating, which stops submissions landing
- auto-tagging failing, above

Each leaves every individual component healthy — `/health` green, no errors in the request path —
and shows up only as a `STEP=` marker that never arrives. There is no monitoring and no
alerting of any kind. A single daily check that "an `event_created` in the last 24h was followed by
a `transcribed`" would catch all three.

### `provision_trigger.sh` wires the wrong bucket

It derives the bucket name in a way that does not match the bucket the service uses, and a bucket
by the derived name exists, so it fails silently rather than erroring. Needs a `--bucket` flag or
a fixed derivation; until then the trigger chain must be provisioned by hand (`ops.md`).

## Unresolved asks from the frontend

### The RLS tripwire

Requested in round 5 and never built: a CI check asserting that **every** table in the exposed
`public` schema has RLS enabled, so a future table cannot ship world-readable through the anon
key. The ask is about the invariant, not today's state (`security.md`).

Open question: a test against a live database (needs credentials in CI) or a lint over
`setup.sql` (cheaper, weaker — it cannot see a table created outside the script).

### The GCS error-code map

Still owed. The client currently maps HTTP `403` to "expired signed URL, re-create" and `400` to
"file too large". **This has never been verified against real GCS**, and there is a known
collision risk: an expired V4 URL can surface as `400 ExpiredToken`, which would be
indistinguishable from the over-range rejection by status alone.

Resolving it needs two real PUTs against a live bucket — one over-range, one expired — recording
the exact status *and* the XML `<Code>` for each. If the codes collide, the client has to switch
from a status-only mapping to reading the XML body. `scripts/verify_gcs_upload.py` exists for
this. Non-blocking: the happy path is unaffected.

### Realtime DELETE privacy

Deleted rows broadcast their full contents to every subscriber, table-wide across users
(`security.md`). Two candidate mitigations, neither validated:

1. **`REPLICA IDENTITY USING INDEX`** on a unique `(id, user_id)` index for `tags`. The old
   image would carry just the two UUIDs — enough for the UPDATE policy check, and no names or
   colors in the DELETE broadcast. Unverified against Supabase Realtime, and the identity
   silently degrades to `NOTHING` if the index is ever dropped. It does **not** help `events`,
   whose client consumes old-record data on UPDATEs.
2. **Migrate Realtime to broadcast authorization** with per-user private channels. This is the
   real fix and it is a rework of the whole Realtime setup.

## Orphaned audio events are never reaped

A client can create an audio event and then die before uploading — the app is killed, the network
drops, the user gives up. The event exists, its recording stays `pending` forever, and no object
ever lands in GCS. The client shows it as transcribing indefinitely, since there is no failed
state on the client by design (`realtime-reads.md`).

Reaping was accepted as the backend's job and **never built**. The sketched approach: periodically
delete audio events whose recording is still `pending` with no stored object after a TTL.

It compounds the exhausted-retry defect above — both leave a recording sitting at `pending`
forever, and nothing distinguishes "never uploaded" from "uploaded but the endpoint was down"
without checking GCS for the object.

## Unanswered support question

An iOS tester (Expo Go, 2026-07-25) had no usable Supabase session: audio create failed before
any request was sent because `getSession()` returned null, and delete-account failed too. Android
on the same build worked end to end. The frontend's hypothesis was a revoked refresh token or a
deleted auth user, while its persisted "signed in" flag still said otherwise.

The backend was asked to check, for that account and a 2026-07-25 18:00–22:00 Israel-time
window: whether an `auth.users` row exists and whether it was ever deleted and recreated; whether
any live session or revoked refresh token exists; how many `public.events` rows that `user_id`
has and their latest `created_at`; and whether any `POST /events` or `DELETE /users/me` requests
from that user appear in the Cloud Run log with what status. Zero requests would confirm the
frontend never sent them and close it as frontend-only.

**None of that was ever recorded as answered**, and the tester's email was never filled in. The
frontend was building a real auth gate regardless, so this may be moot — it has not been closed
either way.

## Undocumented decision

**Alembic versus hand-applied SQL was never actually decided.** `01-data-model.md` asked for
sign-off on "Alembic now versus an idempotent SQL script now" and neither was chosen. What
exists is the third option nobody picked: `create_all` plus `ALTER`s applied by hand, discovered
one production error at a time. `roadmap.md` lists Alembic as next; the question of what to do
in the meantime — for instance a checked-in, ordered, idempotent migration script — is open.
