# Roadmap

What is built, what is next, and what was consciously deferred. This is the single home for
that; the project README does not restate it.

LimON is an app for capturing life events — text notes and voice notes — and reviewing them on a
timeline. Its original framing was PTSD event tracking, which is why the capture path is
optimized for a single tap and why the timeline, not analysis, is the product.

## Built

**Capture.** Empty quick-capture ("press the lemon"), text notes, voice notes with
transcription, and tags. Auto-tagging suggests tags for untagged entries.

**Identity.** Supabase JWT auth with just-in-time provisioning, profile read and update,
delete-account across both stores.

**Timeline.** Create, edit, delete, and read events. The timeline itself is read from Supabase,
not from this API.

**Infrastructure.** Deployed on Cloud Run against Supabase Postgres, with audio in GCS,
transcription driven by Cloud Tasks, RLS and Realtime configured, and CI running lint, tests in
the shipping container, and a compose smoke test.

The full recording chain — record on the phone, upload, transcribe, transcript on the timeline —
was proven end to end on production in one hands-free pass on 2026-07-24.

## Next

- **Sort and display ordering**, and **date-range selection** (default a week, plus
  week/month/quarter). Both are unbuilt and both are MVP.
- **PDF export** and **insights**. Both are MVP, both need the async job shape that
  transcription already demonstrates, and neither has been started.
- **Lock-screen widget.** MVP, platform work.
- **Sign-out.** MVP; currently only delete-account exists on the backend side.
- **Alembic**, before the schema needs to evolve under real data. Hand-applied `ALTER`s have
  already caused one production outage (`data-model.md`).
- **OIDC on `/internal/*`**, the top item of `security.md`.
- **Delete audio blobs on account deletion.** `DELETE /users/me` removes the auth identity and
  cascades the database rows, but the GCS objects at `v0/{userId}/{recordId}.m4a` stay in the
  bucket, now referenced by nothing. This is a **retention** problem, not a storage-cost one:
  audio belonging to someone who asked to be deleted is still there. Three plausible shapes —
  delete the object prefix inline during delete-account (slow, and a partial failure is awkward
  against the remote-first ordering in `auth.md`), a GCS lifecycle rule, or an asynchronous
  sweep. Decided to do; method open.
- **A security review.** Not started; `security.md` is its input.

## Deferred deliberately

These are decisions, not omissions. Each has a reason that should be argued with before
reversing.

**No scheduled sweep, and no Cloud Scheduler.** Recovery of stranded work rides on the
transcriber's callback instead of a clock. The reason it can is specific rather than general:
**audio is never deleted from GCS after transcription**, so a recording that never reached the box
stays recoverable indefinitely and no window is being raced. What it costs is that recovery begins
with the next recording rather than at a fixed hour, which is acceptable while nobody is waiting on
a transcript in an app nobody is using (`transcription.md`). Revisit if ingestion ever runs
unattended, or if audio retention changes — the second would remove the premise entirely.

**No distributed tracing.** Rejected in favour of per-hop `STEP=` markers — trace-context
propagation across GCS, Pub/Sub, and Cloud Tasks plus a new vendor, to learn what `recordId`
already tells us (`architecture.md`).

**No join table for tags.** `tag_ids` stays a JSON array; the normalized alternative is the
migration path if referential integrity ever proves worth a join on every read
(`data-model.md`).

**No camelCasing of the tags and users APIs.** It would only change responses the client no
longer reads (`realtime-reads.md`).

**No `(user_id, client_event_id)` composite unique constraint.** `client_event_id` is a globally
unique UUID4 and the create lookup already filters by owner, so it buys nothing today.

**Sentiment is not persisted.** The tagger computes it and it is logged only; nothing in the
product consumes it yet (`tagging.md`).

**`suggested_location` and `tag_reasoning` are not exposed** to any client. The columns are
written and available when there is a use for them.

## Later

GPS tagging, granularity controls for event display, and an alert when nothing has been recorded
for a configurable period. All P2.

Realtime DELETE privacy is P2 by label but is a genuine exposure; see `security.md` and
`open-questions.md`.

The transcription container is expected to eventually graduate into this repository as
`transcriber/` rather than being called over HTTP from a separately-managed endpoint.

## The transcription container now lives in its own repo

**Further progress on transcription deployment is tracked there, not here.** A future session
asking "what is next for the transcriber?" should be pointed at that repository, which carries its
own `spec/` and its own roadmap.

- **Repo:** `MatanKoby/hebrew-transcriber` (private), checked out locally at
  `~/dev-projects/hebrew-transcriber`.
- **Its roadmap and design record:** `spec/README.md` there, with `spec/roadmap.md`,
  `spec/deploy-cpu.md`, `spec/deploy-gpu.md` and `spec/benchmark.md`. Work is queued in its
  `BUILD_QUEUE.md`.

The container built in `nbs-endpt-poc` was lifted into that repo on 2026-08-08 and given two
deployment targets: the existing Nebius L40S GPU endpoint, and an always-on CPU deployment on an
Oracle free-tier ARM VPS (aarch64 Ampere A1, 2 vCPU, 11GB RAM, reachable as `ssh oracle-vps`).

The motive is cost. The L40S is down by design and costs money to raise (`ops.md`), which is why
`transcription.md` had to tell operators to raise the endpoint *before* recording. An always-on
zero-cost endpoint would delete that step, and with it the pending-backlog caveat under **Deferred
deliberately** above.

**This happened on 2026-09-21, and it was not the URL swap this section predicted.** The ARM box
is now the only transcriber and Nebius is gone (`transcription.md`, with the old design in
`archive.md`).

The prediction was wrong in the way worth recording: it assumed the box would be fast enough to
answer a live request, so that only `TRANSCRIBE_ENDPOINT_URL` would move and the wire contract would
be unaffected. It is not fast enough — about 0.98x real time, against the L40S `rtf` of 0.087 — so
the integration is **asynchronous**: the backend uploads audio, the box calls back when it is done,
and the backend drains the transcripts. That is a different shape, not a different address, and it
is the larger part of what shipped.

Transcription **quality** on CPU remains genuinely open and that repo still owns the benchmark. It
is essentially unmeasured on Hebrew — one four-clip smoke test, explicitly not a ranking — which now
matters more rather than less, since there is no GPU to fall back to.

The graduation note above still stands as the longer-term intent and is not cancelled by this move.
