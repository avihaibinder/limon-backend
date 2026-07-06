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
- [ ] Blob storage for voice note audio — evaluate MinIO
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

```powershell
pip install -e ".[dev]"
uvicorn app.main:app --reload   # dev server, docs at /docs
pytest                          # run tests
```

## Notes

- Tables are created automatically on startup; move to Alembic migrations
  before this needs real schema evolution in production.
- CORS defaults to `["*"]` for development — restrict `LIMON_CORS_ORIGINS`
  before deploying.
- This file is intentionally a starting point — update it as decisions are
  made (DB choice, auth provider, storage, etc.).
