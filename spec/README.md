# Specification

LimON is an app for quickly capturing life events (text notes and voice notes) and reviewing them
on a timeline. This repo is its backend: an async FastAPI service (SQLAlchemy 2, Pydantic v2)
deployed on Cloud Run against Supabase Postgres, with audio in Google Cloud Storage and
transcription plus auto-tagging running as async workers.

The spec is split across concern-focused files. Each file is small and edited as a unit. When
two sections always change in tandem, they belong in the same file. Edit via the procedure in
`specflow/procedures/spec-edit.md`.

## Files

- **`architecture.md`**: the stack, the `models → schemas → services → routers` layering, the
  repo-wide conventions (async, UUID ids, UTC timestamps, `LIMON_`-prefixed settings), and the
  deployed topology — Cloud Run, Supabase Postgres via the session pooler, GCS, Cloud Tasks.
- **`auth.md`**: identity — Supabase JWT verification, why `users.id` *is* the JWT `sub`, one
  account per Supabase identity, and what deleting an account has to reach.
- **`data-model.md`**: the `events` / `tags` / `users` / `recordings` tables and their columns,
  plus the no-migrations reality: `create_all` on startup, so live databases need hand-applied
  `ALTER`s.
- **`realtime-reads.md`**: the read path — the API owns writes, Supabase owns reads. RLS
  policies, replica identity, the realtime publication, and what the client subscribes to.
- **`api.md`**: the HTTP surface and its semantics — event create/read/update/delete, tag
  upsert-by-name, `/users/me`, the upload presign, and the `/internal/*` worker routes.
- **`demo-seed.md`**: the demo-history backfill — what it writes, the empty-account rule, and
  the deliberate deviations in the seeded data.
- **`transcription.md`**: audio from upload to transcript — GCS finalize, the Cloud Tasks queue,
  the worker, and the retry budget with the failure mode it leaves behind.
- **`tagging.md`**: automatic tag suggestion — when it fires, the existing-tags-only rule, and
  how the model's output is handled.
- **`ops.md`**: running it — deploy, the Nebius endpoint's raise/wire/tear-down lifecycle, how
  production schema changes get applied, and the end-to-end rebuild runbook.
- **`security.md`**: the known exposures and the trade-offs taken deliberately — unauthenticated
  internal routes, open CORS, and Realtime's delete broadcasts.
- **`roadmap.md`**: what is built, what is next, and what was consciously deferred.
- **`open-questions.md`**: decisions not yet made, and known defects with no owner yet.
- **`archive.md`**: designs that were agreed and then replaced, kept only to explain why the
  current shape is what it is.

## Research notes (optional)

Pre-design research (prior-art scans, option/tradeoff analysis) is **not yet design**, so it lives
in an optional `spec/research/` sub-folder rather than the files above: **dated, gate-free
snapshots** (`YYYY-MM-topic.md`) that **graduate upward** into `roadmap.md` / `open-questions.md` as
conclusions mature. Create it with its own `README.md` when you start research. Full lifecycle:
`specflow/procedures/spec-edit.md` → *Research notes*.

## Reading order

For someone new: README → (the architecture/overview file) → the files for the area in front
of them. Don't read everything.

For an agent picking up a task: read the 2–4 spec files relevant to its domain, not the whole
spec. Pull a sub-folder README before reading individual files there.

## Editing convention

Edit the file matching the concern. If a change crosses multiple files, that's a signal the
concern might be miscarved — flag it before duplicating content. Cross-reference by file path
rather than restating. Move historical context to `archive.md` when it stops being part of the
live system. Full procedure: `specflow/procedures/spec-edit.md`.
