# Open questions and known defects

Decisions not yet made, and defects with no owner. Distinct from `roadmap.md`, which is work
that is understood and merely unscheduled.

## Defects

### Exhausted transcriptions are indistinguishable from new ones

The soft-failure path reverts a recording to `pending`, so a recording that burned all three
queue attempts against a down endpoint is left `pending` — never `failed`. Nothing can tell it
apart from one that has not started. There is no sweep, so it stays that way forever unless
someone re-drives it by hand (`transcription.md`).

The fix is not obvious: marking it `failed` on budget exhaustion requires knowing the attempt
count, which the worker does not see. Cloud Tasks sends `X-CloudTasks-TaskRetryCount`, which
would work, but nothing reads it today.

### The endpoint scripts do not match their documentation

`scripts/endpoint/wire` and `scripts/endpoint/README.md` are referenced throughout the planning
documents as the operator runbook and the deploy helper. **Neither has ever existed in this
repository, on any branch.** `up` still carries the URL-parsing bug they describe as fixed on
2026-07-26 — it walks the create response for the first `https://` value, but the Nebius CLI
prints text, not JSON, so it dies against an endpoint that was created and *is billing*
(`ops.md`).

Open question: rewrite the hardening, or delete the claims. The work was described in detail and
apparently verified offline, then lost — the same fate as `scripts/e2e_recording_test.sh`.

### `.create.json` is world-readable and has held a token

`scripts/endpoint/.create.json` is `0644` on the operator's machine and contains a create
response. The documented fix (a `0600` `.create.log` with the token masked) is part of the
missing hardening above.

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

## Audio blobs outlive deleted accounts

`DELETE /users/me` removes the Supabase auth identity and cascades away the user's `events`,
`recordings`, and `tags` — but **the audio objects in GCS are never deleted**. A deleted account
leaves its recordings sitting in the bucket at `v0/{userId}/{recordId}.m4a`, with the database
rows that named them gone, so nothing points at them any more.

This is a known gap, acknowledged to the frontend and backlogged rather than fixed. It is a data
retention problem, not just a storage cost one: audio of someone who asked to be deleted is
still there.

Open question: delete the object prefix inline during delete-account (slow, and a partial
failure is awkward given the remote-first ordering in `auth.md`), or apply a GCS lifecycle rule,
or sweep asynchronously.

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
