# LimON Backend

FastAPI backend for the [LimON](../LimON) React Native app.

**Stack:** Python 3.11+ · FastAPI · Pydantic v2 · SQLAlchemy 2 (async) · SQLite

## Project layout

```
app/
├── main.py          # App factory, CORS, lifespan (table creation)
├── core/config.py   # Settings via pydantic-settings (.env, LIMON_ prefix)
├── db/              # Engine, session dependency, declarative base
├── models/          # SQLAlchemy ORM models
├── schemas/         # Pydantic request/response models
├── services/        # Business logic + persistence (keeps routers thin)
├── routers/         # HTTP endpoints, mounted under /api/v1
└── dependencies.py  # Shared dependencies (SessionDep)
tests/               # pytest + httpx, isolated in-memory DB per test
```

## Getting started

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

# run the dev server
uvicorn app.main:app --reload
```

Interactive docs: http://127.0.0.1:8000/docs

## Configuration

Copy `.env.example` to `.env`. All variables use the `LIMON_` prefix
(`LIMON_DATABASE_URL`, `LIMON_CORS_ORIGINS`, `LIMON_DEBUG`,
`LIMON_SUPABASE_URL`, `LIMON_SUPABASE_JWT_SECRET`).

## Authentication

All API routes require a Supabase access token (`Authorization: Bearer <jwt>`).
The client signs in with Supabase Auth (Google OAuth); we verify the token
against the project's JWKS endpoint (derived from `LIMON_SUPABASE_URL`) —
or the legacy HS256 shared secret if `LIMON_SUPABASE_JWT_SECRET` is set —
and provision a local user row on first sight (see `app/core/auth.py`).

## Events API

| Method | Path                  | Description                                      |
| ------ | --------------------- | ------------------------------------------------ |
| POST   | `/api/v1/events`      | Create an event (201)                            |
| GET    | `/api/v1/events`      | List events — `limit`, `offset`, optional `tag`  |
| GET    | `/api/v1/events/{id}` | Fetch one event (404 if missing)                 |
| PATCH  | `/api/v1/events/{id}` | Partial update — only provided fields change     |
| DELETE | `/api/v1/events/{id}` | Delete (204)                                     |

## Tags API

Tags belong to the authenticated user (`user_id` foreign key, cascade on user
delete); names are unique per user. Foreign tags answer 404.

| Method | Path                | Description                                          |
| ------ | ------------------- | ---------------------------------------------------- |
| POST   | `/api/v1/tags`      | Create a tag for the current user (201; 409 dup)     |
| GET    | `/api/v1/tags`      | List own tags A→Z — `limit`, `offset`                |
| GET    | `/api/v1/tags/{id}` | Fetch one of your tags (404 if missing/foreign)      |
| PATCH  | `/api/v1/tags/{id}` | Rename (409 if the name is taken)                    |
| DELETE | `/api/v1/tags/{id}` | Delete (204)                                         |

## Users API

LimON's own user identity (rows carry `provider` + `provider_subject`, the
Supabase token's `sub` claim). Accounts are provisioned automatically on the
first authenticated request — the API is self-service only.

| Method | Path                | Description                                         |
| ------ | ------------------- | --------------------------------------------------- |
| GET    | `/api/v1/users/me`  | Fetch your profile (creates the account on first use)|
| PATCH  | `/api/v1/users/me`  | Update profile fields (provider identity immutable) |
| DELETE | `/api/v1/users/me`  | Delete your account and its tags (204)              |

## Tests

```powershell
pytest
```

## Notes

- Tables are created automatically on startup; switch to Alembic migrations
  once the schema needs to evolve in production.
- CORS defaults to `["*"]` for development — restrict `LIMON_CORS_ORIGINS`
  before deploying.
