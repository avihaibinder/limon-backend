# 2026-08 — Where the design truth currently lives

Snapshot taken 2026-08-06, to plan the first pass of `spec/`. Question it answers: **what
already documents this system, and how much of it can be trusted?**

## The short version

There is no spec today. There are roughly 3,000 lines of *build plans* and *FE/BE negotiation
transcripts*, all point-in-time, plus 2,800 lines of code that is the only thing guaranteed to
be current. Porting the documents into `spec/` one-for-one would import their staleness.

**The approach that follows from this:** write each spec file from the **code as ground truth**,
and mine the documents only for the *why* — the decisions, the rejected alternatives, and the
trade-offs accepted with eyes open. Code states what the system does; it cannot state what was
considered and dropped, and that is precisely what a future agent will otherwise re-litigate.

## Sources, ranked by trust

### Tier 1 — ground truth (current by construction)

| Source | Size | Covers |
|---|---|---|
| `app/**` | 2,828 lines | everything the service actually does |
| `scripts/supabase/setup.sql` | — | Realtime publication, replica identity, RLS policies |
| `scripts/deploy_gcp.sh`, `provision_trigger.sh`, `endpoint/up`\|`down` | — | deploy and the Nebius endpoint lifecycle |
| `tests/**` | 103 passing | the behavioral contract, including edge cases |

### Tier 2 — actively maintained prose (trust, but verify against tier 1)

| Source | Size | Notes |
|---|---|---|
| `CLAUDE.md` | 257 lines | the *Notes* section is the closest thing to a current spec; kept up to date through the demo-seed work. Much of `spec/` is a re-carve of this file. |
| `README.md` | 336 lines | HTTP surface + local dev; updated today for the route removal |
| `../fe-be-comms/*.md` | 5 docs | the **live** FE/BE contracts: tags-crud, tags-realtime, audio-duration, ios-auth questions, and the tags-snapshot migration (closed today) |

### Tier 3 — historical build plans (mine for rationale, do not port)

`spec-local/plan/` — `PLAN.md` (230 lines) plus domain files `01`–`09`. These are *action plans*:
they carry status markers ("DONE", "pending a live run"), "not in scope" fences, and open
questions that have since been answered. The feature they planned has shipped.

**Their lasting value is `PLAN.md`'s "Settled decisions" log — 18 numbered decisions with their
reasoning and their supersessions.** That is the single richest source of *why* in the repo and
should be distributed across the spec files by concern (not kept as a numbered list, which is a
changelog, not a design).

`E2E_WALKTHROUGH.md` (402 lines, 2026-07-26) is the live-run runbook and is the most recently
maintained plan file; `E2E_CHECKLIST.md` is its superseded predecessor (stale `europe-west3`
region and bucket).

### Tier 4 — superseded negotiation (archive fodder)

`CONTRACT.md` → `CONTRACT.v2.md` → `FE_CONTRACT.md`; `BE_ANSWERS.v2/v3`, `BE_DECISIONS.v4`,
`FE_DECISIONS.v3/v5`, `FE_MIGRATION.md`, `FE_MIGRATION_FEEDBACK.md`, `BACKEND_INTEGRATION.md`,
`CLAUDE.be.md`. Rounds of a conversation that has concluded. Each contributes at most a few
lines to `archive.md` — the shape that was agreed and then replaced, and why.

Note `spec-local/` is **gitignored and purged from git history** (Matan's call, 2026-07-22). A
fresh clone does not have it. That is an argument for the spec carrying the durable content:
today the reasoning behind this system exists on exactly one laptop.

## Staleness found (a naive port would carry these in as fact)

1. **`PLAN.md` decision 18** offers the FE's launch snapshot as "`GET /events` via the BE, or a
   direct Supabase `.select()`". `GET /events` was deleted today (`fc9848d`). Supabase is the
   only read path.
2. **`PLAN.md` / `04-trigger.md`** document the dev shim `POST /internal/dev/transcribe/{id}`.
   Removed by decision 17; the route does not exist.
3. **`09-realtime-rls.md` and `PLAN.md`** describe `tags` as deny-all RLS. False since
   2026-07-23: `tags` has an owner-only SELECT policy, `replica identity full`, and is in the
   realtime publication — configured identically to `events`.
4. **`scripts/supabase/setup.sql:84`** still says "the FE keeps reading snapshots via
   `GET /tags`". Stale as of today; that route is gone. **This one is a live comment in a
   tier-1 file and should be fixed in the repo, not just noted here.**
5. **`01-data-model.md`** proposes `scripts/migrations/001_*.sql` and asks for sign-off on
   Alembic-now vs SQL-script-now. Neither happened. The real answer, undocumented as a
   decision: `create_all` on startup plus hand-applied `ALTER`s, listed ad hoc in `CLAUDE.md`.
6. **`PLAN.md`** lists domain 06 as "scripts written, pending a live run". Proven live
   2026-07-26.
7. **`scripts/endpoint/README.md` does not exist.** It has been referred to as the operator
   runbook; the directory holds only `up`, `down`, and their state files. The runbook content
   lives in `E2E_WALKTHROUGH.md`.

## Assessment of the proposed carve

`spec/README.md` currently proposes eleven files. Reviewing them against what the sources
actually contain, two concerns have no home:

- **Identity and auth.** Supabase JWT verification via JWKS, `users.id` *is* the JWT `sub`
  (decision 15), one account per Supabase identity, and delete-account having to reach into
  `auth.users` through the Admin API because our cascade cannot. This is a first-class concern
  and it currently falls between `data-model.md` and `api.md`.
- **The read path.** "The API owns writes; Supabase owns reads" is now the system's defining
  rule, and it is enforced by `setup.sql`: RLS policies, `replica identity full`, the realtime
  publication, and what the FE subscribes to. Today this would scatter across `architecture.md`,
  `data-model.md`, and `security.md` — the classic sign of a missing file.

Everything else in the proposed list holds up. `api.md` is the one at risk of growing past the
600-line cap (four routers), but it is genuinely one concern; split it only if it does.

*Resolved 2026-08-06: both files were added, giving thirteen.*

## Coverage matrix

Where each source went. Written as the spec files were, so it is auditable: pick any source and
see what absorbed it. "Archived" means it contributed the *reason a design was replaced* to
`archive.md`, not a port of its content.

| Source | Read | Landed in |
|---|---|---|
| `app/**` (2,828 lines) | full for models, config, main, auth, logging, session, routers, events/tags/users/transcription/tagging services, schemas; skimmed `tagger.py` internals, `storage.py`, `task_queue.py`, `demo_seed.py` data rows | all thirteen — the ground truth every file was checked against |
| `scripts/supabase/setup.sql` | full | `realtime-reads.md` (and a stale comment in it fixed) |
| `scripts/endpoint/up`, `down` | full | `ops.md`, `open-questions.md` (documentation mismatch) |
| `scripts/deploy_gcp.sh`, `provision_trigger.sh` | full / headers | `ops.md` |
| `CLAUDE.md` | full | distributed across all thirteen; it was the closest thing to a spec |
| `README.md` | full | `architecture.md`, `api.md` |
| `docs/SPEC.md` (387) | full | `archive.md` (most of it), `roadmap.md`, product framing in `roadmap.md` |
| `docs/GCP_DEPLOYMENT.md` (115) | full | `ops.md`, `security.md`; its `europe-west3` and "events have no `user_id`" are archived |
| `plan/PLAN.md` (230) | full | the 18 decisions distributed by concern across every file |
| `plan/01-data-model.md` | full | `data-model.md`; its undecided migration question → `open-questions.md` |
| `plan/02-nebius-client.md` | full | `transcription.md` (error taxonomy, timeouts) |
| `plan/03-worker.md` | full | `transcription.md` (claim, hard/soft failures) |
| `plan/04-trigger.md` | full | `transcription.md`, `api.md`; dev shim → `archive.md` |
| `plan/05-events-api.md` | full | `api.md` |
| `plan/06-ops-endpoint.md` | full | `ops.md`, `roadmap.md` (deferrals); its "hardened 2026-07-26" claim → `open-questions.md` |
| `plan/07-user-scoping.md` | full | `auth.md`, `api.md` |
| `plan/08-signed-url.md` | full | `transcription.md`; the owed GCS `<Code>` map → `open-questions.md` |
| `plan/09-realtime-rls.md` | full | `realtime-reads.md` |
| `plan/E2E_WALKTHROUGH.md` (402) | headings + STATE + re-run section | `ops.md` |
| `plan/E2E_CHECKLIST.md` | superseded, not read in full | nothing — stale region/bucket |
| `BACKEND_INTEGRATION.md` | full | `transcription.md` (endpoint contract, single-flight, Hebrew, VAD) |
| `CLAUDE.be.md` | full | `transcription.md`, `ops.md`; its `POST /recordings` shape → `archive.md` |
| `CONTRACT.v2.md` | full | `api.md`, `transcription.md`, `realtime-reads.md` |
| `CONTRACT.md` (v1) | not read in full | `archive.md` (polling, `POST /recordings`) via v2's supersession record |
| `FE_CONTRACT.md` | not read in full | superseded by CONTRACT.v2, which records its resolutions |
| `BE_ANSWERS.v3.md` | full | `realtime-reads.md`, `api.md`; Model A → `archive.md` |
| `BE_DECISIONS.v4.md` | full | `realtime-reads.md` (writes/reads split, casing) |
| `FE_DECISIONS.v3.md` | full | `archive.md` (its snapshot pick was overridden) |
| `FE_DECISIONS.v5.md` | full | `realtime-reads.md` (frozen contract), `security.md` + `open-questions.md` (tripwire) |
| `BE_ANSWERS.v2.md`, `FE_QUESTIONS.v2.md` | not read in full | superseded by v3; the one live item (GCS `<Code>` map) is carried |
| `FE_MIGRATION.md`, `FE_MIGRATION_FEEDBACK.md` | not read in full | Model B → `archive.md` via BE_ANSWERS.v3's §0 |
| `FE_USES.write-apis.md`, `FE_QUESTIONS.golive.md` | not read in full | conventions already carried by CONTRACT.v2 and the live contracts |
| `SUPABASE_SETUP.md` | not read in full | `setup.sql` itself is authoritative and was read |
| `FE_DEMO_SEED.md` | full | `demo-seed.md` |
| `fe-be-comms/FE_CONTRACT.tags-crud.md` | full | `api.md` (upsert, detach-on-delete, color) |
| `fe-be-comms/FE_CONTRACT.tags-realtime.md` | full | `realtime-reads.md` (replica identity, delete trade-off) |
| `fe-be-comms/FE_CONTRACT.audio-duration.md` | full | `api.md`, `data-model.md` (the denormalization) |
| `fe-be-comms/FE_QUESTIONS.ios-auth.md` | full | `open-questions.md` (never answered) |
| `fe-be-comms/BE_MIGRATION.tags-snapshot.md` | full | `realtime-reads.md`, `archive.md`; closed out 2026-08-06 |

### Not read in full, and why that is defensible

Eight documents were not read line by line. All eight are tier-4 negotiation rounds whose
conclusions are recorded in a later document that *was* read in full — v2 by v3, v3 by v4, v4 by
v5, `FE_CONTRACT.md` by `CONTRACT.v2.md`'s explicit resolutions section, `FE_MIGRATION_FEEDBACK`
by `BE_ANSWERS.v3`'s section-by-section reply.

The residual risk is a detail raised in a round and never restated in its successor. If
something turns out to be missing, these eight are where to look first.
