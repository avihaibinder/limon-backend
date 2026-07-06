# LimON Backend

FastAPI backend for the [LimON](../LimON) React Native app.

**Stack:** Python 3.11+ · FastAPI · Pydantic v2 · SQLAlchemy 2 (async) · SQLite · [uv](https://docs.astral.sh/uv/)

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

Dependencies and the virtual environment are managed with
[`uv`](https://docs.astral.sh/uv/). Install `uv` itself first (once per
machine):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # macOS/Linux
# or: pipx install uv / brew install uv
```

Then, from the repo root:

```bash
# create .venv and install the project + dev dependencies, pinned via uv.lock
uv sync --extra dev

# run the dev server (auto-reload)
uv run uvicorn app.main:app --reload
```

`uv sync` creates `.venv` automatically — there's no separate "activate"
step needed; `uv run` executes commands inside that environment. You can
still `source .venv/bin/activate` if you prefer working inside the shell
directly.

Interactive docs: http://127.0.0.1:8000/docs

### Adding or updating dependencies

```bash
uv add <package>              # add a runtime dependency
uv add --dev <package>        # add a dev-only dependency
uv sync --extra dev           # re-sync .venv after pulling changes to uv.lock
uv lock --upgrade              # upgrade locked versions
```

Always commit `uv.lock` alongside `pyproject.toml` changes so installs stay
reproducible across machines and (later) Docker builds.

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

```bash
uv run pytest
```

## Running with Docker

The API can also run in a container via Docker Compose, using the same `uv`
commands as local dev under the hood:

```bash
docker compose up --build
```

This builds the image (`uv sync --frozen` at build time), starts the API on
http://localhost:8000, and persists the SQLite database file to a named
volume (`limon-data`, mounted at `/app/data`) so data survives container
restarts. Stop it with `docker compose down` (add `-v` to also drop the
volume and its data).

Compose sets `LIMON_DATABASE_URL` to point at that volume path; override any
`LIMON_*` variable via the `environment:` block in `docker-compose.yml` or a
`.env` file as needed. As more services (a real DB, etc.) are introduced,
they'll be added to `docker-compose.yml` alongside `api`.

### Blob storage (MinIO)

`docker compose up` also starts a [MinIO](https://min.io/) container as an
S3-compatible object store for future blob storage needs (e.g. voice note
audio):

- S3 API: http://localhost:9000
- Web console: http://localhost:9001 (default credentials `minioadmin` /
  `minioadmin` — override via `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` in
  `docker-compose.yml` for anything beyond local dev)
- A `minio-init` one-shot service waits for MinIO to become healthy and
  creates the default bucket (`limon`) automatically
- Data persists in the `minio-data` volume across restarts

The API container is passed `LIMON_S3_ENDPOINT_URL`, `LIMON_S3_ACCESS_KEY`,
`LIMON_S3_SECRET_KEY`, and `LIMON_S3_BUCKET` so a future storage client can
pick them up; no app code uses them yet.

## Notes

- Tables are created automatically on startup; switch to Alembic migrations
  once the schema needs to evolve in production.
- CORS defaults to `["*"]` for development — restrict `LIMON_CORS_ORIGINS`
  before deploying.
