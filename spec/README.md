# Specification

LimON is an app for quickly capturing life events (text notes and voice notes) and reviewing them
on a timeline. This repo is its backend: an async FastAPI service (SQLAlchemy 2, Pydantic v2)
deployed on Cloud Run against Supabase Postgres, with audio in Google Cloud Storage and
transcription plus auto-tagging running as async workers.

The spec is split across concern-focused files. Each file is small and edited as a unit. When
two sections always change in tandem, they belong in the same file. Edit via the procedure in
`specflow/procedures/spec-edit.md`.

## Files

<!-- One line per spec file, naming its concern. Add files as the design grows; when a
     sub-folder appears, give it its own README and point to it here instead of listing every
     file. Example:

- **`architecture.md`**: tech stack, deployment, the major moving parts.
- **`schema.md`**: data model.
- **`flows.md`**: end-to-end flows.
- **`roadmap.md`**: the single home for roadmap content, milestones and deferred / what's-next directions (never restated in the project README).
- **`archive.md`**: historical content no longer reflecting live code.
-->

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
