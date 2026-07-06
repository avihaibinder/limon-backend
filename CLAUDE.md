# LimON Backend

FastAPI backend for LimON, a React Native / Expo mobile app for quickly
capturing life events (text notes, voice notes) and reviewing them on a
timeline.

## Stack

Python 3.11+ · FastAPI · Pydantic v2 · SQLAlchemy 2 (async) · SQLite (dev)

## Project layout

```
app/
├── main.py          # App factory, CORS, lifespan (table creation)
├── core/config.py   # Settings via pydantic-settings (.env, LIMON_ prefix)
├── db/               # Engine, session dependency, declarative base
├── models/           # SQLAlchemy ORM models
├── schemas/          # Pydantic request/response models
├── services/         # Business logic + persistence (keeps routers thin)
├── routers/           # HTTP endpoints, mounted under /api/v1
└── dependencies.py    # Shared dependencies (SessionDep)
tests/                 # pytest + httpx, isolated in-memory DB per test
```

Pattern per resource: `models/<x>.py` (ORM) → `schemas/<x>.py` (Pydantic) →
`services/<x>.py` (business logic) → `routers/<x>.py` (thin HTTP layer).
Follow this shape for any new resource (e.g. voice notes, insights).

## Current state (as of 2026-07-06)

Implemented: `events`, `tags`, `users` — full CRUD, mounted under `/api/v1`.
No authentication yet (users are created directly via `POST /users`; the
model already carries `provider` / `provider_subject` for future OAuth).
No voice notes, PDF export, insights, or widget support yet. Tables are
created automatically on startup (no migrations yet — see Notes below).

## Feature backlog

Legend: MVP = required for first release, P2 = later.

### Capture
- [x] Create empty event ("press the lemon") — MVP
- [x] Create a text note — MVP
- [ ] Create a voice note + transcribe to text (60s max) — MVP
- [x] Tags (create/list/rename/delete) — MVP

### Auth & account
- [ ] Authentication (OAuth; `users` model already has `provider`/`provider_subject`) — MVP
- [ ] Sign out — MVP
- [ ] Delete account — MVP
- [ ] Profile (incl. email) — MVP

### Timeline & organization
- [x] Timeline (list events) — MVP
- [x] Edit event
- [x] Delete event
- [ ] Sort/display ordering — MVP
- [ ] Date range selection: default week, plus week/month/quarter — MVP
- [ ] Granularity for event display — P2

### Export & insights
- [ ] Export to PDF — MVP
- [ ] Insights — MVP

### Platform
- [ ] Lock-screen widget — MVP
- [ ] Alert when nothing recorded for a configurable period — P2
- [ ] GPS tagging — P2

### Infra
- [ ] Migrate DB target — evaluate Supabase (Postgres) vs. current SQLite
- [x] Blob storage infra — MinIO running via docker-compose (bucket `limon`); app-level client still TBD once a feature needs it
- [ ] Testing: unit tests exist for events/tags/users; need FE test coverage too
- [ ] Security review
- [ ] "Adi/matn" oracle machine — context TBD, ask user before assuming scope

## Conventions

- Async everywhere: routers and services are `async def`, use `SessionDep`
  (an `AsyncSession`) from `app.dependencies`.
- Routers stay thin: validation + 404/409 handling only; business logic and
  queries live in `services/`.
- IDs are UUID4 strings (`String(36)` primary keys), not integers.
- Timestamps are timezone-aware UTC (`DateTime(timezone=True)`).
- Settings are read via `get_settings()` (lru_cached `Settings`), all env
  vars prefixed `LIMON_`.

## Commands

Dependency management and execution use [`uv`](https://docs.astral.sh/uv/)
(not raw `pip`). `uv.lock` is the source of truth for pinned versions and
must be committed with any `pyproject.toml` dependency change.

```bash
uv sync --extra dev                    # create .venv, install deps (pinned via uv.lock)
uv run uvicorn app.main:app --reload   # dev server, docs at /docs
uv run pytest                          # run tests
uv add <package>                       # add a runtime dependency
uv add --dev <package>                 # add a dev-only dependency
```

## Notes

- Tables are created automatically on startup; move to Alembic migrations
  before this needs real schema evolution in production.
- CORS defaults to `["*"]` for development — restrict `LIMON_CORS_ORIGINS`
  before deploying.
- `greenlet` is declared as a direct dependency (not left as SQLAlchemy's
  transitive/marker-based extra) because SQLAlchemy's platform marker for it
  omits macOS Apple Silicon (`arm64`), so `uv sync` would otherwise skip
  installing it on those machines and every async DB call would fail.
- `docker compose up --build` runs the API in a container (`Dockerfile` +
  `docker-compose.yml`), using `uv sync --frozen` at build time and `uv run
  uvicorn ...` as the run command to mirror local dev. SQLite data is
  persisted to the `limon-data` volume at `/app/data`. As real infra
  (Postgres/Supabase, etc.) is added, extend `docker-compose.yml` with
  those services rather than introducing a separate compose file.
- `docker-compose.yml` also runs a MinIO container (S3-compatible blob
  storage) plus a one-shot `minio-init` service that waits for MinIO's
  healthcheck and creates the default `limon` bucket. This is infra-only
  for now — the API receives `LIMON_S3_*` env vars but no app code reads
  them yet; add a storage client/service once a feature (e.g. voice notes)
  needs to upload/download blobs.
- This file is intentionally a starting point — update it as decisions are
  made (DB choice, auth provider, storage, etc.).
