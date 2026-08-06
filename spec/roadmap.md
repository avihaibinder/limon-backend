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

**No pending-backlog re-enqueue.** Nothing sweeps up recordings that piled up while the
transcription endpoint was down. A backlog only forms in an always-on ingestion path running
unattended; in the manual demo workflow the only pending rows are the handful the operator just
made and already knows about. The consequence is real and stated in `transcription.md`: raise the
endpoint *before* recording. Revisit when ingestion runs unattended long enough for a real
backlog to exist.

**No automated dead-man switch** on the GPU endpoint. A standing Cloud Scheduler is itself a way
to drift past the free tier, and there is one operator watching one endpoint. The safety net is
discipline (`ops.md`).

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
