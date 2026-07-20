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
`LIMON_SUPABASE_URL`, `LIMON_SUPABASE_JWT_SECRET`). Audio uploads add
`LIMON_GCS_BUCKET` and `LIMON_GCS_SIGNER_SERVICE_ACCOUNT` — see
[Blob storage (Google Cloud Storage)](#blob-storage-google-cloud-storage).

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

## Audio uploads API

Clients upload audio (e.g. voice notes) straight to Google Cloud Storage via a
short-lived presigned URL — the file bytes never pass through the backend. See
[Blob storage (Google Cloud Storage)](#blob-storage-google-cloud-storage) for
the one-time GCP setup this endpoint needs.

| Method | Path                             | Description                                                   |
| ------ | -------------------------------- | ------------------------------------------------------------ |
| POST   | `/api/v1/uploads/audio/presign`  | Get a signed PUT URL for one audio file (201; 400 bad type; 503 if unconfigured) |

Request body is `{"content_type": "audio/mp4"}` (allowed: `audio/mp4`,
`audio/aac`, `audio/mpeg`, `audio/ogg`, `audio/wav`, `audio/webm`). The
response returns `upload_url`, `object_key` (`audio/{user_id}/{uuid}.ext`),
`content_type`, and `expires_at`. The client then `PUT`s the file to
`upload_url` with a matching `Content-Type` header. Persisting the returned
`object_key` to a voice-note record is a separate, not-yet-implemented step.

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

## Blob storage (Google Cloud Storage)

Audio uploads use Google Cloud Storage. The backend generates **V4 signed PUT
URLs** so clients upload directly to a bucket. Signing is done through
Application Default Credentials (ADC) plus the IAM `signBlob` API — **no
service-account key file is stored anywhere**. Everything account-specific
lives in two env vars, so pointing this at *your own* GCP account is a
config-only change; no code edits.

The upload endpoint returns **503** until `LIMON_GCS_BUCKET` is set — the rest
of the API runs fine without any GCS setup.

### One-time GCP setup (your own account)

Prerequisites: the [`gcloud` CLI](https://cloud.google.com/sdk/docs/install)
installed, and a GCP project with **billing enabled** (the free tier still
requires a billing account attached — a bucket can't be created without one).

```bash
# 1. Authenticate and select your project
gcloud auth login                                   # your Google account
gcloud config set project <YOUR_PROJECT_ID>
gcloud auth application-default login               # sets up ADC (what the app uses)

# 2. Enable the APIs signing and uploading need
gcloud services enable storage.googleapis.com iamcredentials.googleapis.com

# 3. Create the bucket (name must be globally unique)
gcloud storage buckets create gs://<YOUR_BUCKET> --location=us

# 4. Create the service account whose identity signs the upload URLs
gcloud iam service-accounts create limon-signer

# 5. Let YOUR user impersonate that SA, so ADC (you) can sign as it.
#    This is what the IAM signBlob call needs; without it you get a 403.
PROJECT=$(gcloud config get-value project)
YOU=$(gcloud config get-value account)
gcloud iam service-accounts add-iam-policy-binding \
  limon-signer@$PROJECT.iam.gserviceaccount.com \
  --member="user:$YOU" --role="roles/iam.serviceAccountTokenCreator"

# 6. Let the signer SA read/write the bucket
gcloud storage buckets add-iam-policy-binding gs://<YOUR_BUCKET> \
  --member="serviceAccount:limon-signer@$PROJECT.iam.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"
```

IAM changes can take a minute or two to propagate — if signing 403s right
after step 5, wait briefly and retry.

### Configure the app

Set these in `.env` (see `.env.example`; the values are yours, never
committed — `.env` is git-ignored):

```bash
LIMON_GCS_BUCKET=<YOUR_BUCKET>
LIMON_GCS_SIGNER_SERVICE_ACCOUNT=limon-signer@<YOUR_PROJECT_ID>.iam.gserviceaccount.com
# Optional — signed-URL lifetime in seconds (default 900 = 15 min)
# LIMON_GCS_SIGNED_URL_TTL_SECONDS=900
```

The setup above is for **local development**, where ADC is your *user* account
(which cannot sign), so the app impersonates the signer SA. **Cloud Run works
differently** — see below.

### Verify it end-to-end (local)

`scripts/verify_gcs_upload.py` does the real round trip — generates a signed
URL via the service and PUTs a small test object to the bucket:

```bash
uv run python scripts/verify_gcs_upload.py
```

A `HTTP 200` and a printed `object_key` mean it works; the object appears under
`audio/verify/...` in your bucket (delete it afterwards with
`gcloud storage rm`). The unit tests mock GCS, so they need none of this setup.

### Running on Cloud Run

On Cloud Run there is **no key file and no `gcloud auth`** — ADC resolves to the
service account attached to the revision, and the app signs URLs *as that SA
itself* (via IAM `signBlob`). So you don't set `LIMON_GCS_SIGNER_SERVICE_ACCOUNT`
there; only `LIMON_GCS_BUCKET`. What the runtime SA needs:

```bash
PROJECT=$(gcloud config get-value project)

# A runtime service account for the Cloud Run revision
gcloud iam service-accounts create limon-run --display-name="LimON Cloud Run runtime"

# Read/write the bucket
gcloud storage buckets add-iam-policy-binding gs://<YOUR_BUCKET> \
  --member="serviceAccount:limon-run@$PROJECT.iam.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"

# Sign blobs AS ITSELF: signBlob is an IAM call, so the SA needs
# token-creator on its OWN identity. Without this, signing 403s.
gcloud iam service-accounts add-iam-policy-binding \
  limon-run@$PROJECT.iam.gserviceaccount.com \
  --member="serviceAccount:limon-run@$PROJECT.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountTokenCreator"
```

Then deploy with that SA attached and the bucket in env (signer var omitted):

```bash
gcloud run deploy limon-backend \
  --source . \
  --region=<REGION> \
  --service-account=limon-run@$PROJECT.iam.gserviceaccount.com \
  --set-env-vars=LIMON_GCS_BUCKET=<YOUR_BUCKET>
```

For the Expo/web client, keep Cloud Run publicly invokable with
`--allow-unauthenticated`; application routes remain protected by Supabase JWT
verification. Use Cloud Run IAM authentication only for administrative or
non-client deployments.

### Automated deployment

The manual steps above are scripted end-to-end (bootstrap IAM + bucket +
Secret Manager, deploy, run a GCS round-trip smoke test) — idempotent, so
re-running ships a new revision:

```bash
scripts/deploy_gcp.sh --project <id> --supabase-url https://<ref>.supabase.co   # macOS/Linux/Cloud Shell
pwsh scripts/deploy_gcp.ps1 -ProjectId <id> -SupabaseUrl https://<ref>.supabase.co  # Windows
```

Both take `--bootstrap-only` (infra only, no deploy) and `--require-iam-auth`
(deploy without `--allow-unauthenticated`). The DB URL is read from a Secret
Manager secret (`limon-database-url`) rather than passed on the command line.
See [`docs/GCP_DEPLOYMENT.md`](docs/GCP_DEPLOYMENT.md) for the full walkthrough.

## Notes

- Tables are created automatically on startup; switch to Alembic migrations
  once the schema needs to evolve in production.
- CORS defaults to `["*"]` for development — restrict `LIMON_CORS_ORIGINS`
  before deploying.
