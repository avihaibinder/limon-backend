# Architecture

The LimON backend is a small async FastAPI service. It has one job description that explains
most of its shape: **the API owns writes, Supabase owns reads.** The client posts every event
through this service and reads none of them back from it — snapshots and live updates both come
straight from Supabase Postgres. See `realtime-reads.md` for that half.

Two things happen outside the request path: transcribing audio (`transcription.md`) and
suggesting tags (`tagging.md`). Both run as workers driven by queued tasks, not by the user
waiting on an HTTP response.

## Stack

Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2 (async). Postgres in production via Supabase;
SQLite is the local dev and test default, and the code stays dialect-neutral so the two behave
alike. Dependency management is [`uv`](https://docs.astral.sh/uv/), with `uv.lock` as the source
of truth for pinned versions — it must be committed alongside any `pyproject.toml` change.

Lint and format are Ruff, 100-char lines, enforced in CI.

Two dependencies are worth explaining:

- **`greenlet` is a direct dependency**, not left as SQLAlchemy's transitive extra. SQLAlchemy's
  platform marker for it omits macOS Apple Silicon, so `uv sync` would skip it there and every
  async DB call would fail.
- **`pyjwt[crypto]`** rather than a Supabase SDK: token verification is a JWKS fetch and a
  signature check, and doing it directly keeps the auth path readable (`auth.md`).

## Layering

Each resource follows the same four-file shape, in dependency order:

```
models/<x>.py     SQLAlchemy ORM: the table
schemas/<x>.py    Pydantic: the wire shape, request and response
services/<x>.py   business logic and queries
routers/<x>.py    HTTP: validation, status codes, nothing else
```

**Routers stay thin.** A router validates input, calls one service, and maps outcomes to status
codes (typically a 404 for a missing or non-owned row). Queries and business rules live in
services, which take an `AsyncSession` and know nothing about HTTP. Anything new — a resource, a
worker, an integration — follows this shape.

`dependencies.py` holds `SessionDep`, the per-request `AsyncSession`. `core/` holds the
cross-cutting pieces: settings, auth, logging.

## Conventions

These are repo-wide and not restated in the individual spec files:

- **Async everywhere.** Routers and services are `async def`.
- **IDs are UUID4 strings** in `String(36)` columns, never integers and never a library UUID
  type. The one exception is `users.id`, which is not minted here at all (`auth.md`).
- **Timestamps are timezone-aware UTC**, `DateTime(timezone=True)`, and ISO-8601 on output. The
  one input in another format is `clientCreatedAt` (epoch milliseconds), which the client sends
  on create; see `api.md`.
- **Wire casing is camelCase; column casing is snake_case.** The HTTP surface matches the
  client's idiom. Reads bypass the API entirely and arrive as raw snake_case columns, so the
  client applies one generic transform on its read path — which is why camelCasing responses is
  not a goal worth extending.
- **Settings come from `get_settings()`**, an `lru_cache`d Pydantic `Settings`, with every env
  var prefixed `LIMON_`. Anything account-specific (bucket, project, endpoint URLs, keys) is
  configuration, never a constant in code, so pointing the service at a different GCP or
  Supabase account is an env-only change.

## Deployed topology

```
   mobile client (React Native / Expo)
      │                      ▲
      │ writes               │ reads: snapshot + Realtime
      ▼                      │
   Cloud Run ──────────► Supabase Postgres
   (limon-api,          (session pooler, RLS)
    us-east1)
      │  presigned PUT
      ▼
   Google Cloud Storage ──finalize──► Pub/Sub ──► /internal/uploaded
                                                        │
                                                   Cloud Tasks
                                                        │
                                                        ▼
                                              /internal/transcribe ──► the box
```

**Cloud Run** runs the service; it must stay publicly reachable because Pub/Sub push and Cloud
Tasks both POST *into* it. **Supabase** is a separate managed Postgres reached over the network
— the database is not inside this service, and "the backend is the source of truth" is loose
shorthand for "the database is". Connections go through the **IPv4 session pooler**, not the
direct `db.<ref>.supabase.co` host, which is IPv6-only and unreachable from Cloud Run. The
engine sets `pool_pre_ping=True` to survive pooler and idle disconnects.

Audio bytes never pass through this service: the client PUTs them straight to **GCS** using a
signed URL the API mints. Deploy and endpoint-lifecycle procedures are in `ops.md`.

## Observability

The audio path crosses four process boundaries, so a stalled event needs to be diagnosable after
the fact rather than by watching it live. Every hop emits one structured marker —
`STEP=<name> recordId=...` on the `limon.pipeline` logger (`app/core/logging.py`) — and Cloud Run
streams stdout to Cloud Logging. Filtering by `recordId` shows how far an event travelled, and a
*missing* marker names the hop it never reached.

The logger is deliberately self-contained (`propagate=False`, its own stdout handler) so it
neither depends on nor duplicates uvicorn's config. It never logs audio bytes or transcript
text, only ids and counts.

This replaced a proposal for distributed tracing (Grafana/OTel), rejected as disproportionate for
a solo prod validation: it would need manual trace-context propagation across GCS, Pub/Sub, and
Cloud Tasks plus a new vendor, to learn what `recordId` already tells us. Note for future test
authors: `propagate=False` means `caplog` will not capture this logger by default.

## Local development

`docker compose up --build` runs the API in a container mirroring local dev (`uv sync --frozen`
at build, `uv run uvicorn` at run), persisting SQLite to the `limon-data` volume. As real infra
arrives, extend `docker-compose.yml` rather than adding a second compose file.

CI runs Ruff (lint and format check), pytest **inside the `api` container** so tests exercise the
environment the app ships in, and a compose smoke test that boots the stack and checks `/health`.

There is an opt-in `pre-push` hook (`uv run python scripts/hooks/install.py`) that blocks a push
failing lint or format. It is per-machine; CI is what enforces this for everyone.
