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

```bash
uv run pytest
```

## Linting & formatting

Both are handled by [Ruff](https://docs.astral.sh/ruff/):

```bash
uv run ruff check .            # lint
uv run ruff check --fix .      # lint, auto-fixing what it can
uv run ruff format .           # format
uv run ruff format --check .   # format check only (what CI runs), no changes
```

### Pre-push hook (optional, recommended)

Install a local `pre-push` git hook that runs `ruff check` and `ruff format
--check` before every `git push`, aborting the push if either fails:

```bash
uv run python scripts/hooks/install.py
```

This only runs locally for whoever installs it — CI's `lint` job is the
real enforcement backstop for everyone else.

## CI

GitHub Actions (`.github/workflows/ci.yml`) runs on every push to `main` and
every pull request, with three jobs:

- **lint** — `ruff check` and `ruff format --check` via `uv`
- **test** — `pytest` executed inside the `api` container
  (`docker compose run`), so tests run against the same environment the app
  actually ships in (this matters more once a real database service is
  added)
- **compose-smoke-test** — `docker compose up --build`, waits for
  `/health` to respond, then tears the stack down; catches breakage in the
  Dockerfile/compose setup itself, not just the app code

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

### Blob storage (Google Cloud Storage)

Production blobs such as voice notes and generated PDFs are stored in a
private Google Cloud Storage bucket configured by `LIMON_GCS_BUCKET`.
`GCSBlobStorage` uses Application Default Credentials, so Cloud Run obtains
credentials from its runtime service account and no key file is stored in the
repository or environment variables.

The storage boundary lives in `app/services/storage.py`. Run the
upload/read/delete smoke test after authenticating with Google Cloud:

```bash
uv run python scripts/smoke_gcs.py
```

The local SQLite compose stack does not emulate GCS. Unit tests use a fake
client and production verification runs the smoke script as a one-task Cloud
Run Job with the same service account as the API.

## Deploying to Google Cloud

Cloud Run, GCS, IAM, Secret Manager, and the Frankfurt region setup are
documented in [`docs/GCP_DEPLOYMENT.md`](docs/GCP_DEPLOYMENT.md). The included
PowerShell script is idempotent and supports a bootstrap-only phase:

```powershell
.\scripts\deploy_gcp.ps1 `
  -ProjectId "TEAM_PROJECT_ID" `
  -SupabaseUrl "https://PROJECT_REF.supabase.co" `
  -BootstrapOnly
```

## Notes

- Tables are created automatically on startup; switch to Alembic migrations
  once the schema needs to evolve in production.
- CORS defaults to `["*"]` for development — restrict `LIMON_CORS_ORIGINS`
  before deploying.
