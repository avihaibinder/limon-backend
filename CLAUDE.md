<!-- specflow:start - managed by specflow; do not edit inside these markers (your edits block specflow upgrade). Add your own notes outside them. -->
# CLAUDE.md

This repo uses **[specflow](https://github.com/MatanKoby/specflow)** — a spec-driven protocol
shared by all agents: design is written down and approved before code is written.

**Read [`AGENTS.md`](AGENTS.md) first.** It is the full protocol: propose → approve → spec →
build. The spec procedure lives in `specflow/procedures/` and is also installed as the `spec-edit`
skill, which triggers automatically:

- Before editing any `spec/**` file or persisting a design decision → `spec-edit`

Project-specific guidance (what this codebase is, conventions, tooling) goes **below this line**
or in your own sections — `AGENTS.md` and `specflow/**` are specflow-managed and get overwritten
on `specflow upgrade`.
<!-- specflow:end -->

# LimON Backend

Async FastAPI service for LimON, a React Native / Expo app for capturing life events (text and
voice notes) and reviewing them on a timeline. Python 3.11+, Pydantic v2, SQLAlchemy 2 (async),
Postgres in production via Supabase, SQLite locally.

## Where the design lives

**`spec/` is the design record.** Read it before changing behavior rather than re-deriving from
code — it carries the reasoning, the rejected alternatives, and the trade-offs taken knowingly,
none of which the code can tell you. `spec/README.md` is the map.

Pull the two or three files covering what you are touching, not the whole thing:

| Touching | Read |
|---|---|
| layering, conventions, deployed shape | `spec/architecture.md` |
| tokens, `users.id`, delete-account | `spec/auth.md` |
| tables, columns, schema changes | `spec/data-model.md` |
| anything the client reads | `spec/realtime-reads.md` |
| routes, status codes, wire shapes | `spec/api.md` |
| audio, transcription, the worker | `spec/transcription.md` |
| auto-tagging | `spec/tagging.md` |
| demo data | `spec/demo-seed.md` |
| deploy, endpoint lifecycle, rebuilds | `spec/ops.md` |
| exposures and trade-offs | `spec/security.md` |
| what is next, what was deferred | `spec/roadmap.md` |
| unresolved questions, known defects | `spec/open-questions.md` |
| why something *used* to be different | `spec/archive.md` |

`docs/`, `spec-local/`, and `../fe-be-comms/` were retired on 2026-08-06; everything
load-bearing from them is in `spec/`. A comment or memory pointing at those paths is stale.

## Commands

Dependency management and execution use [`uv`](https://docs.astral.sh/uv/), never raw `pip`.
`uv.lock` is the source of truth for pinned versions and must be committed with any
`pyproject.toml` change.

```bash
uv sync --extra dev                    # create .venv, install deps
uv run uvicorn app.main:app --reload   # dev server, docs at /docs
uv run pytest                          # tests
uv run ruff check .                    # lint
uv run ruff format .                   # format
uv add <package>                       # add a runtime dependency
uv add --dev <package>                 # add a dev-only dependency
docker compose up --build              # run the API in its container
```

Ruff config lives in `pyproject.toml`, 100-char lines. CI runs lint, format check, pytest inside
the `api` container, and a compose smoke test. `uv run python scripts/hooks/install.py` installs
an optional pre-push hook that blocks a push failing lint or format.

## Writing code here

- **Follow the shape**: `models/<x>.py` → `schemas/<x>.py` → `services/<x>.py` → `routers/<x>.py`.
  Routers stay thin — validation and status codes only; queries and business rules live in
  services. Rationale in `spec/architecture.md`.
- **Async everywhere.** Use `SessionDep` from `app.dependencies`.
- **Add tests with behavior changes.** `tests/` is pytest + httpx against an isolated in-memory
  database per test.
- **Don't restate the spec in code comments.** Comment the non-obvious *why* at the line; the
  design belongs in `spec/`.

## Before you touch production

- **There are no migrations.** Adding a model column requires the matching `ALTER` on the live
  database *first*, or every query on that table 500s service-wide. See `spec/data-model.md`.
- **Matan runs production DDL and data changes himself.** Hand him the SQL; do not execute it.
- **The transcriber box is shared and its ack is destructive.** One queue, no per-caller scoping:
  an unfiltered drain returns every caller's results and the ack that follows deletes them. Filter
  by your own ids when anyone else is using it. `spec/transcription.md`.
- **Don't use `scripts/deploy_gcp.sh` for a code deploy** — it replaces the service environment.
  `spec/ops.md` has the right command.

## Commit conventions

`spec: <change>` for anything under `spec/**`, `meta: <change>` for tooling and structure.
Full protocol in `AGENTS.md`.
