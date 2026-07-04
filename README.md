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
(`LIMON_DATABASE_URL`, `LIMON_CORS_ORIGINS`, `LIMON_DEBUG`).

## Events API

| Method | Path                  | Description                                      |
| ------ | --------------------- | ------------------------------------------------ |
| POST   | `/api/v1/events`      | Create an event (201)                            |
| GET    | `/api/v1/events`      | List events — `limit`, `offset`, optional `tag`  |
| GET    | `/api/v1/events/{id}` | Fetch one event (404 if missing)                 |
| PATCH  | `/api/v1/events/{id}` | Partial update — only provided fields change     |
| DELETE | `/api/v1/events/{id}` | Delete (204)                                     |

## Tags API

Each tag belongs to a user (`user_id` foreign key, cascade on user delete) and
names are unique per user.

| Method | Path                | Description                                          |
| ------ | ------------------- | ---------------------------------------------------- |
| POST   | `/api/v1/tags`      | Create a tag (201; 404 unknown user; 409 duplicate)  |
| GET    | `/api/v1/tags`      | List tags A→Z — `limit`, `offset`, optional `user_id`|
| GET    | `/api/v1/tags/{id}` | Fetch one tag (404 if missing)                       |
| PATCH  | `/api/v1/tags/{id}` | Rename (409 if the name is taken)                    |
| DELETE | `/api/v1/tags/{id}` | Delete (204)                                         |

## Users API

Holds LimON's own user identity; designed for OAuth (rows carry `provider` +
`provider_subject`, the token's `sub` claim). Once authentication is wired up,
users will be provisioned automatically from verified tokens and `POST /users`
will go away.

| Method | Path                 | Description                                        |
| ------ | -------------------- | -------------------------------------------------- |
| POST   | `/api/v1/users`      | Create a user (201; 409 on duplicate identity)     |
| GET    | `/api/v1/users`      | List users — `limit`, `offset`                     |
| GET    | `/api/v1/users/{id}` | Fetch one user (404 if missing)                    |
| PATCH  | `/api/v1/users/{id}` | Update profile fields (provider identity immutable)|
| DELETE | `/api/v1/users/{id}` | Delete (204)                                       |

## Tests

```powershell
pytest
```

## Notes

- Tables are created automatically on startup; switch to Alembic migrations
  once the schema needs to evolve in production.
- CORS defaults to `["*"]` for development — restrict `LIMON_CORS_ORIGINS`
  before deploying.
