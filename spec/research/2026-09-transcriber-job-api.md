# Moving transcription onto the Oracle box's async job API

**Written 2026-09-21.** A snapshot, not design.

**Graduated the same day.** The design is now live in `../transcription.md` (the cycle),
`../api.md` (the two routes), `../ops.md` (the box and its rotating URL), `../open-questions.md`
(what it leaves open) and `../archive.md` (the synchronous design it replaced). **Read those, not
this.** What is kept here is the evidence trail: the sources, the twelve contract defects the read
turned up, and the reasoning behind each decision -- including the two places this note was wrong
before the code was written.

The Hebrew transcriber now runs always-on on the Oracle ARM VPS behind an **asynchronous job
API** — submit, get woken by a callback, drain, persist, acknowledge. That is a different shape
from what this backend does today (one synchronous multipart POST that returns the transcript in
the same request), so the change is not the URL swap `../roadmap.md` predicted.

## Sources read

Three files in `~/dev-projects/hebrew-transcriber`, all read 2026-09-21:

| File | What it is |
|---|---|
| `BACKEND_BRIEF.md` | the orientation: what to read, what will bite, what is unsettled |
| `spec/job-api.md` | **the authority**; where the brief disagrees, this wins |
| `bench/backend_sim.py` | a working reference caller, run against the live box |

**That file was split on 2026-09-21** (their commit `069cc68`): `job-api.md` is now the contract
and only the contract, and `job-internals.md` is the box's own design, which a backend does not
need. Anything citing a line number in the pre-split file has moved.

Plus the box's own `transcriber/callback.py`, `transcriber/server.py` and `transcriber/jobs.py`,
read to settle three things the prose leaves implicit (the callback body, the drain's default
`limit`, and how OIDC is minted).

## What the contract is, in the shape this backend needs it

```
worker  ──POST /jobs (multipart file + job_id)──►  box     202 queued
        ...nothing. no polling...
box     ──POST callback (a nudge, no transcript)──►  backend
backend ──GET /jobs?status=done,failed&include=text──►  box   everything ready, ONE request
backend  persist
backend ──POST /jobs/ack {job_ids}──►  box                    the only thing that deletes
```

Every route but `/health` takes `Authorization: Bearer $AUTH_TOKEN`, header only.

**Persist before ack, always.** The ack is the only deletion path; acking before the text is
durably stored is the single way to lose a transcript. Everything else in the contract is
forgiving: a lost callback, a failed drain and a crashed instance all leave the results sitting
on the box, collectable next time.

### Where it is, read off the box on 2026-09-21

`ssh oracle-vps 'cd ~/hebrew-transcriber && ./deploy/cpu/tunnel-url.sh --check'` →
`https://graph-donors-mats-loaded.trycloudflare.com`, `/health` answering
`{"status":"ok","model":"/models/whisper-large-v3-turbo-ct2","device":"cpu","compute_type":"int8_float32"}`.
**Volatile — see finding 3.** That command is how the current one is read; there is no other way
to discover it.

Its `.env` confirms the limits rather than leaving them to the defaults in prose:
`MAX_AUDIO_DURATION_S=600`, `MAX_QUEUE_AUDIO_S=7200`, `JOB_TTL_DAYS=30`,
`CALLBACK_BATCH_SIZE=10`. **`MAX_UPLOAD_MB` is unset**, so it takes the code default of `25`,
which `server.py:96` computes as `25 * 1024 * 1024` — exactly 25 MiB, matching our signed-URL cap.

`CALLBACK_URL` is empty and `CALLBACK_AUTH=none`: **the box has never been pointed at anything of
ours.** Confirmed independently by the transcriber session — the variable did not exist before
2026-09-21, and its only two values were loopback addresses during their own end-to-end test.

**`AUTH_TOKEN` verified against the live box**, 2026-09-21: `GET /status` returns `200`. That
response also settles three things on the wire rather than in prose — timestamps really are
ISO-8601 with an offset (finding 5), `limits.max_upload_mb` really is `25.0`, and the box
publishes `capacity_audio_s_per_hour: 3526` (the 0.98x ceiling) plus the `cost_model`
(`fixed_s: 13.6`, `marginal: 1.021`) that `poll_after_s` is fitted from.

### Numbers that constrain us

| | | Consequence here |
|---|---|---|
| Throughput | 0.98x real time, one worker | fine at our volume; the box never catches up on a continuous feed |
| Max upload | `MAX_UPLOAD_MB=25`, computed as `25 * 1024 * 1024` | **exactly 25 MiB — identical to the signed-URL cap in `../transcription.md`.** No gap. |
| Max audio duration | 600 s | a cap this backend does not currently enforce anywhere |
| Max resident queue | 7200 s of audio, then `503` + `Retry-After` | can exceed the Cloud Tasks retry budget by a wide margin |
| Result TTL if never acked | 30 days | a backstop; if it fires, it is our bug |
| Max ids per `ack` / `GET /jobs` | 200 | and `GET /jobs` defaults to `limit=50`, so the drain must pass `limit=200` and page |


### Failure taxonomy on a `failed` job's `error.code`

`not_audio`, `transcription_failed`, `retry_exhausted` are all **permanent by the time we see
them** — the box has already spent both of its attempts. `error.retryable` describes the nature of
the failure, not an instruction to resubmit. Only `audio_missing` wants a resubmit, under a new
`job_id`.

**The field is `error.code`, nested, not the flat `error_code` their contract section describes.**
`jobs.py:551` returns `{"error": {"code", "message", "retryable"}}` and `_decorate` does not
flatten it; `error_code` / `error_retryable` are SQLite column names leaking into prose written for
someone who will never see that database. Branching on `error_code` gets `None` on every failed job
and silently misclassifies every permanent failure as unrecognised — the same failure shape as
`include_text=true`.

Relayed 2026-09-21 and **fixed the same day** (their commit `6ef8d54`): the taxonomy section now
leads with the real JSON and states there is no flat `error_code` on the wire.

## Findings that change the design

### 1. The box cannot use OIDC to authenticate to us

`spec/job-api.md` and `BACKEND_BRIEF.md` both recommend a GCP OIDC identity token, "since Cloud
Run validates it natively". But `transcriber/callback.py:304` mints that token from
`http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity`
— the **GCP metadata server**, which does not exist on an Oracle VPS. `CALLBACK_AUTH=oidc` would
fail on every callback from this deployment.

So **the shared secret header (`CALLBACK_AUTH=secret`) is not the floor here, it is the only
option**, and this backend should be built for it rather than for the recommendation.

The upside is that it removes a service account and a GCP IAM binding from our side entirely.

**Confirmed and fixed on the transcriber side the same day** (their commit `e99020d`, relayed
2026-09-21). They verified `metadata.google.internal` does not resolve from inside the container
(`gaierror -2`), so `oidc` would have failed all six delivery attempts every time. `job-api.md`,
`BACKEND_BRIEF.md` and `callback.py` now say `secret` is the mode for this deployment and state
the precondition — `oidc` becomes correct if and when the box runs on GCP, and minting a token
off-GCP would need a service-account key plus a JWT exchange, which is exactly the
credential-to-rotate that OIDC exists to remove. The failure is now legible too: it raises
`OidcUnavailable` and surfaces in `callback.last_error` on `GET /status`, rather than appearing as
a bare DNS error six retries deep.

### 2. The `413` ambiguity does not block this backend, because we can avoid `413` entirely

The transcriber repo flags `413` meaning two things (body over `MAX_UPLOAD_MB` vs audio over
`MAX_AUDIO_DURATION_S`) as blocking, on the grounds that the remedies differ. They do differ, and
both remedies — re-encode at a lower bitrate, split the recording — are things **this backend**
could do, since it holds the bytes. Neither is phone-side.

We can avoid most of it by pre-flighting, but **not all of it, and the gap matters**:

- **Bytes: always checkable.** Not from `recordings.byte_size`, which is nullable and never
  populated at create — the audio goes straight to GCS and this service never sees a size until it
  downloads. Use `len(audio)` after the download, which is always available.
- **Duration: checkable only sometimes.** `recordings.duration_sec` is **client-supplied and
  nullable** (`schemas/event.py:81` — "null for text events and for audio without a stored
  length"). When it is null there is nothing to pre-flight against.

So `413` is unreachable when `duration_sec` is present and **live when it is null**. The handler is
a real fallback, not dead code. It stays a permanent failure either way, so we still never need to
tell the two causes apart — which is what keeps their Batch M from blocking us — but the earlier
claim that pre-flighting makes `413` impossible was wrong.

Separately, the byte limit is currently unreachable by construction: the signed-URL cap is exactly
25 MiB and `MAX_UPLOAD_MB` computes to `25 * 1024 * 1024`, so nothing that passes GCS can exceed
it. **That is two numbers coinciding, not a guarantee** — it breaks the moment either side moves,
which is why the pre-flight is the real defense and the coincidence is only a note.

So: not blocking for us, but not resolved either. Worth relaying, since it was held open partly on
"there is no consumer yet" and now there is one.

**Recordings over 600 s are rejected.** Matan, 2026-09-21. Marked `failed` with a short reason,
not split. Splitting the audio, submitting the pieces as several jobs and reassembling the
transcripts in order is a genuine feature rather than a patch, and is deferred rather than
dismissed — the rejection is what makes deferring it safe, since a too-long recording fails
visibly instead of vanishing.

### 3. The base URL is volatile by design

A Cloudflare Quick Tunnel, reminted on every reboot of the box, with no API to discover it — the
`cloudflared` startup banner is the only place it exists. It has already rotated once. Two things
follow: it must live in config changeable without a rebuild, and **"the transcriber URL stopped
resolving" is an expected operating condition**, not an incident. On Cloud Run that is
`gcloud run services update limon-api --update-env-vars`, which is a new revision but not a build.

The recovery path is the backstop sweep below: while the URL is stale, submissions fail soft and
rows sit `pending`, and the sweep re-drives them once the URL is fixed.

### 4. `include=text`, not `include_text=true`

Unknown query parameters are **ignored, not rejected**. The wrong name returns `200` with no
transcripts and no error, and it already cost the reference implementation an unnoticed N+1. This
deserves an explicit test asserting the query string, not just the parsed result.

### 5. Two more document-vs-wire mismatches, both now fixed

Same category as findings 1 and 2, and worth recording because they change code we write.

- **`GET /jobs` defaults to `limit=50`** against a cap of 200, and a truncated page carries no
  indication that it is truncated. They measured it: 60 finished jobs, no `limit`, exactly 50
  returned, silently. **Pass `limit=200`.**
- **Better than paging until a short page:** the response envelope already carries `done_unacked`
  and `failed_unacked` next to `jobs`. For an unfiltered `?status=done,failed` drain those two
  summed are the authoritative count of what is waiting, so `len(jobs) < done_unacked +
  failed_unacked` says there is more **as a number rather than an inference**. It does not hold
  when filtering by `ids`, where the counts stay global. Values above the cap are clamped, not
  rejected.
- **`queue_position` is absent on a running job** (the field is simply missing from the body, not
  null) and is a **snapshot that moves** — the queue drains between two reads, so the same
  position can legitimately appear twice across sequential reads of different jobs. Never diff
  positions across reads and infer anything. We do not use it; recorded so nobody starts.
- **`POST /jobs` also returns `queued_audio_s`**, which their route table does not list.
- **Timestamps are ISO-8601 with an offset** (`"2026-09-21T09:09:21+00:00"`), not epoch seconds.
  I had this wrong, and the reason is worth keeping: the job rows were always ISO-8601, but the
  callback's `sent_at` was genuinely epoch, so the API spoke two formats at once. Fixed in
  `6ef8d54` — `sent_at` and `last_sent_at` are now ISO-8601 UTC like everything else, with a test
  asserting every timestamp the API emits parses as ISO-8601 with a timezone. **The epoch values
  in their `bench/results/callback/` logs predate that commit** and were deliberately left as the
  historical record, so they are not a counter-example.


## What this backend has to build

Listed as work items. Nothing here is committed to until Matan signs off on the open decisions at
the bottom.

### A. A job client to replace the synchronous one

`app/services/transcriber.py` today is one `POST /transcribe` returning a transcript
(`../transcription.md` → *The transcription endpoint*). The job API needs four calls instead:

1. `submit(job_id, audio)` → `POST /jobs`, multipart `file` + `job_id`. `202` queued, `200`
   idempotent hit.
2. `drain()` → `GET /jobs?status=done,failed&include=text&limit=200`, paged while
   `len(jobs) < done_unacked + failed_unacked` from the same envelope (finding 5).
3. `ack(job_ids)` → `POST /jobs/ack`, batched at 200.
4. `get(job_id)` → `GET /jobs/{id}`, used **only** by the sweep to chase a straggler, never in
   the drain loop.

Plus `discard(job_id)` → `DELETE /jobs/{id}` for the `409` path below.

The existing soft/hard exception split (`EndpointBusyError`, `AudioRejectedError`, …) maps over
almost unchanged; `409` is the one genuinely new outcome.

### B. `job_id` = `recordings.id`

It is a UUID4, so it satisfies the contract's unguessability obligation (a shared token means any
token-holder could otherwise walk ids and read another caller's transcripts). It maps back to a
recording with no extra column and no correlation table.

Why unguessability is *our* obligation and not the box's: there is one shared `AUTH_TOKEN` for
every caller, so anyone holding it can `GET /jobs/{id}`. The box cannot enforce a property of a
value it does not generate. A UUID4 settles it.

**Rejected: the object key** (`v0/{userId}/{recordId}.m4a`), which the brief suggests as a shape.
It would put Supabase user ids in a third party's job table for no benefit.

**Rejected: a separate `transcriber_job_id` column.** It would need production DDL
(`../data-model.md`) and buys nothing that the recording id does not already have.

**`409` is nearly unreachable, and the handling is cheap insurance rather than a live path.** It
needs the *same* id with *different* bytes. The obvious route — the client re-PUTs the same signed
URL, minting a new GCS generation and a second `OBJECT_FINALIZE` — is already closed by the atomic
claim: the second task finds the row `transcribing`, fails `WHERE state IN ('pending','failed')`,
and no-ops. A resubmit after a transport error carries the *same* bytes and is an idempotent
`200`. And once a result is acked the box has deleted the row, so the id is free again.

Handle it anyway — `DELETE /jobs/{id}`, resubmit once, a second `409` is a permanent failure —
because three lines is less than the cost of the silent mismatch if one of those arguments is
wrong. The new audio is the truth in every sub-case.

Note that **acking frees the id**, since the ack deletes the row on the box. That is what makes a
later resubmit of the same recording (the `audio_missing` path, a sweep re-drive) work without a
generation counter.

### C. `/internal/transcribe` becomes a submitter, not a transcriber

The same route, the same Cloud Task, the same atomic claim. What changes is everything after the
claim:

1. no recording → noop; already `done` → noop *(unchanged)*
2. atomic claim `pending|failed → transcribing` *(unchanged)*
3. no event linked → hard fail *(unchanged)*
4. **new:** pre-flight `duration_sec > 600` **and** `byte_size > 25 MiB` → hard fail without
   spending an upload (finding 2)
5. download from GCS *(unchanged)*
6. **`POST /jobs`** instead of `POST /transcribe`
7. return `200` and stop. **The transcript does not arrive in this request.**

Outcome mapping, extending the `noop | done | failed | retry` vocabulary with `submitted` → `200`:

| From the box | Outcome | Row left as |
|---|---|---|
| `202` / `200` | `submitted` | `transcribing` |
| `409` | retry the `DELETE`+resubmit once, then `failed` | `transcribing` / `failed` |
| `400` | `failed` (undecodable, or a bad `job_id`) | `failed` |
| `413` | `failed` — and a loud log: the pre-flight in C4 should have caught it (finding 2) | `failed` |
| `503` + `Retry-After` | `retry` | `pending` |
| `401` | `retry`, logged loudly — a config error, not a transient | `pending` |
| transport error / DNS | `retry` — **the common case when the tunnel URL has rotated** | `pending` |

**The Cloud Tasks retry budget now covers submission only** (3 attempts, roughly a three-minute
window — `../transcription.md` → *Retry budget*). Transcription itself is no longer under any
deadline, which removes the whole "recorded while the endpoint was down" failure class. What it
does *not* remove is a submission failing for longer than three minutes — a rotated URL, a full
queue — and that is what the sweep is for.

Auto-tagging **moves out of this route**: there is no transcript here to tag. It belongs in the
collection step.

### D. A callback endpoint

`POST /internal/transcripts-ready`.

- **Authenticates, mandatorily.** Header `X-Callback-Token`, verified against their contract
  2026-09-21 (`job-api.md:343`, which now shows the literal request with headers). It is
  `CALLBACK_SECRET_HEADER` on the box and is configurable if we ever want a different name. Getting
  it wrong `401`s every callback, and a `401` is a `4xx` so it is deliberately not retried — silent
  on their side, invisible on ours until the sweep. Checked against
  `LIMON_TRANSCRIBER_CALLBACK_SECRET`. **Fail closed:** secret unset → reject. It must not inherit
  the `require_internal_auth` behavior of going open when the token is unset
  (`../security.md` → *`/internal/*` is unauthenticated in production*) — the contract requires
  authentication and this is the one internal route with a caller that can supply it.
- **Ignores the body except for logging.** It names a job; we must not fetch that job. Drain
  everything, because an earlier callback may have been lost and this one is the first wakeup
  since.
- **Idempotent.** It will be called twice. Both times must be boring.
- **Drains inside the request, then returns `200`.** Cloud Run throttles CPU outside a request,
  so work deferred past the response is not guaranteed to run. Returning `200` early and crashing
  loses nothing (the results stay on the box), but it does lose the wakeup, so the drain belongs
  in the request.
- **A `4xx` is not retried by the box** (only `408`/`429` are). A secret mismatch therefore
  strands results silently until the sweep. Acceptable, and worth a log line that says so.

Callback body, for reference — `transcriber/callback.py:240`:

```json
{"event": "results_ready", "job_id": "…", "results_waiting": 3, "done_unacked": 3,
 "failed_unacked": 0, "queued": 0, "queue_empty": true,
 "sent_at": "2026-09-21T09:09:21+00:00"}
```

**Firing rule:** a job finishing wakes us only if the queue is empty *or* ≥10 results are already
waiting. Twelve jobs submitted at once produced **two** callbacks in the transcriber's own
testing. Delivery is one attempt plus five retries (2, 4, 8, 16, 32 s), then it stops.

### E. The collection cycle, shared by the callback and the sweep

One function, two callers:

1. `GET /jobs?status=done,failed&include=text&limit=200`, paged on the envelope's counts
   (finding 5).
2. For each job, in its own committed transaction:
   - `done` → resolve `recordings.id = job_id`; write `text` to `events.description`, set the
     recording `done`. `description` going non-null is the client's completion signal
     (`../realtime-reads.md`). Then enqueue auto-tagging if the event has no tags, best-effort
     (`../tagging.md`).
   - `done`, recording already `done` → skip the write, still ack. This is the idempotency case.
   - `failed`, `error.code` in `not_audio` / `transcription_failed` / `retry_exhausted` → mark the
     recording `failed` with a short reason. Permanent; both attempts are spent.
   - `failed`, `error.code = audio_missing` → revert to `pending` and let the sweep resubmit.
     Acking first frees the id.
   - **no such recording** (deleted account, deleted event) → nothing to store, **ack anyway**.
     Otherwise it sits on the box until the 30-day TTL. This is a deliberate "we decided there is
     nothing to persist", and it needs to be written down rather than discovered.
3. `POST /jobs/ack` with the ids that reached one of those terminal decisions, batched at 200.
   **An id whose persist raised is not in the list.** It will come back on the next drain.

**The box is one queue, one namespace, with no per-caller scoping**, and the consequence is sharper
than the shared token it follows from: anyone holding `AUTH_TOKEN` sees every job and can
acknowledge — therefore destroy — any result, including one they never submitted and have never
read. A `job_id` collision between two parties who never met is a real collision.

So **the unfiltered drain above is correct only while LimON is the only caller.** It is the right
production behaviour against a box we effectively own and it destroys other people's work against
a box we share. If a second consumer ever appears, the drain has to filter by `ids` we submitted,
and this design changes shape.

Raised with the transcriber session 2026-09-21 as an operating courtesy; they took it as a contract
defect and documented it (`5431297`), with a test pinned to it so the warning is removed if scoping
is ever added.

### F. A backstop sweep

`POST /internal/transcripts-sweep`, driven daily by Cloud Scheduler. Three jobs, in order:

1. Run the collection cycle unconditionally — catches results whose callback never landed, or
   landed while the secret was wrong.
2. Recordings `transcribing` beyond a threshold: `GET /jobs/{job_id}`. `404` means unknown,
   acknowledged or expired — the box no longer has it — so revert to `pending` and re-enqueue. A
   non-terminal status means it is still queued behind other work; leave it.
3. Recordings `pending` beyond a threshold: re-enqueue. **This closes the open defect in
   `../open-questions.md` → *Exhausted transcriptions are indistinguishable from new ones***,
   which today needs a human to re-drive by hand.

`updated_at` carries the threshold, so **no new column is needed** for any of this.

### G. Configuration

| Setting | Notes |
|---|---|
| `LIMON_TRANSCRIBER_BASE_URL` | volatile; one `gcloud run services update` to change |
| `LIMON_TRANSCRIBER_TOKEN` | secret; Secret Manager |
| `LIMON_TRANSCRIBER_CALLBACK_SECRET` | secret **we** generate and Matan sets on the box as `CALLBACK_SECRET` |
| `LIMON_TRANSCRIBER_MAX_AUDIO_DURATION_S` | 600, mirroring the box, for the pre-flight in C4 |
| `LIMON_TRANSCRIBER_SUBMIT_TIMEOUT_S` | the submit returns after `ffprobe`, not after transcription, so this sizes an upload rather than a transcription |

The existing `transcriber_endpoint_url` / `transcriber_endpoint_token` / `transcriber_timeout_s`
belong to the Nebius client and their fate is decision 1 below.

### H. No schema change

Worth stating explicitly given `../data-model.md` → no migrations. `job_id` is `recordings.id`,
the sweep's clock is `updated_at`, and `transcribing` absorbs its new meaning ("submitted, waiting
for a callback") without a new state. **Nothing for Matan to run against the live database.**

The cost is that a row submitted ten seconds ago and a row stranded for a day are the same state,
distinguishable only by `updated_at` — which is exactly what the sweep reads, so the cost is paid
where it does no harm.

## Testing it

**Unit**, with `httpx.MockTransport` over the job client:

- every submit status: `202`, `200`, `400`, `409` (→ delete → resubmit → success, and → `409`
  again → failed), `413`, `503` with `Retry-After`, `401`, transport error
- the drain's **exact query string**, asserting `include=text` **and** `limit=200` — findings 4
  and 5
- paging: a full page at `limit=200` with `done_unacked + failed_unacked` higher than the page
  fetches again; equal stops
- a `failed` job parses from nested `error.code`, and a flat `error_code` is **not** read
- timestamps parse as ISO-8601 with a timezone, not epoch
- **persist-before-ack ordering**: with a persist that raises, assert `ack` was never called with
  that id, and that a second drain re-collects it
- the callback called twice over the same result set writes once and acks both times
- callback auth: missing header → reject, wrong secret → reject, secret unset → reject (fail
  closed), correct → `200`
- an unmappable `job_id` is acked, not stranded
- the `600 s` pre-flight rejects without an upload

**Integration against the live box, in this order:**

**The box was handed over quiet on 2026-09-21** — zero queued, zero unacked, the transcriber
session finished testing and will say before it puts results there again. So the `ids` filter below
is belt-and-braces rather than the thing standing between us and deleting their work.

1. **Local, no callback.** `scripts/transcriber/roundtrip.py` (shaped on `bench/backend_sim.py`):
   point `LIMON_TRANSCRIBER_BASE_URL` at the tunnel, submit a real clip with
   `LIMON_LOCAL_AUDIO_DIR` supplying the bytes, then invoke the **sweep** by hand to drain,
   persist and ack. This exercises the whole cycle except the wakeup, needs no inbound
   reachability, and is the test to get green first.
2. **Deployed, with the callback.** Matan sets `CALLBACK_URL`, `CALLBACK_AUTH=secret` and
   `CALLBACK_SECRET` on the box, pointed at the Cloud Run URL. Record from the phone and watch the
   `STEP=` markers. This is the only step that proves the wakeup.

A local callback is possible (`cloudflared tunnel --url http://localhost:8000` on this machine,
URL handed to Matan) but costs a box-side reconfiguration per run, so it is a fallback rather than
the plan.

`../ops.md` → *Watching a run* gains two markers: `submitted` (job accepted by the box) and
`collected` (`chars=N` written), and `transcribed` stops meaning "the endpoint answered".

## What Matan has to do

**On the box**, once we have an endpoint:

```
CALLBACK_URL=https://limon-api-610976310144.us-east1.run.app/internal/transcripts-ready
CALLBACK_AUTH=secret
CALLBACK_SECRET=<generated here, handed over>
```

**In GCP.** Checked 2026-09-21: **the backend did not move.** `limon-api` is still
`limon-502611` / `us-east1` / `https://limon-api-610976310144.us-east1.run.app`, last deployed
2026-08-06, and `limon-502611` is the only project id that appears anywhere in the repo's history
across all branches. `fe-limon/.env` points at that same URL. There is no deploy workflow in CI
(`.github/workflows/ci.yml` is lint and tests only), so a deploy leaves no trace in git — the
check is evidential, not conclusive.

Needed, and **the `matankoby88@gmail.com` account already holds every permission**, confirmed by
`testIamPermissions` on 2026-09-21:

- enable `cloudscheduler.googleapis.com` (**not currently enabled**) and create the daily sweep job
  — have `serviceusage.services.enable`, `cloudscheduler.jobs.create`
- two Secret Manager secrets (`limon-transcriber-token`, `limon-transcriber-callback-secret`) plus
  `secretAccessor` for the runtime service account — have `secretmanager.secrets.create`,
  `versions.add`, `secrets.setIamPolicy`
- `gcloud run services update` — have `run.services.update`, `iam.serviceAccounts.actAs`
- **no new service account**, because of finding 1

Not held: `resourcemanager.projects.setIamPolicy` (project-level IAM). Not needed — the secret
binding is per-secret. `cloudresourcemanager.googleapis.com` is also not enabled; it blocks
`gcloud projects` calls but nothing on this path.

**The base URL is settled** — read off the box directly (above), so it is not something a human
has to hand over after each rotation as their contract assumes; `tunnel-url.sh` is reachable from
here.

**`AUTH_TOKEN` is the one thing still outstanding.** It is in the box's `.env` and reachable over
the same SSH, but the sandbox blocks both reading it and copying it into a local file. It is
needed only to make a real call, so nothing above is blocked on it — see *Still open*.

## Decisions taken

**The Oracle box replaces Nebius.** Matan, 2026-09-21. Not a switch, not a second backend: the
always-on box is the transcriber, and the whole reason for the async job API is that it is *not*
fast enough to answer a live request per file. `../ops.md` → *The transcription endpoint
lifecycle* and the "raise the endpoint before recording" step go away with it, as does the
pending-backlog caveat in `../roadmap.md`.

Quality is worth stating plainly since it is now the only transcriber: it is **essentially
unmeasured on Hebrew** (one four-clip smoke test in the transcriber repo, explicitly not a
ranking — there is no number to quote).

**`job_id` is `recordings.id`.** Matan, 2026-09-21: the id is already in Supabase and is unique,
so it can safely carry the correlation. This is what keeps the whole change schema-free.

## Still open

1. ~~Confirm `job_id = recordings.id`.~~ **Decided.**
2. ~~Relay the findings to the transcriber repo.~~ **Done 2026-09-21.** Findings 1 and 5 are fixed
   on their side (`e99020d`, `6ef8d54`); finding 2 is recorded in their Batch M and is Matan's
   call, which is *why* we pre-flight instead of branching on `413`.
3. ~~Recordings over 600 s.~~ **Decided: reject.**
4. ~~`AUTH_TOKEN` has to reach this machine.~~ **Done** — pasted into `.env` by Matan and verified
   against the live box, 2026-09-21.

## What is stale because of this

`../roadmap.md` → *The transcription container now lives in its own repo* ends with "the only
change here is where `TRANSCRIBE_ENDPOINT_URL` points; the wire contract in `transcription.md` is
unaffected." **Confirmed wrong by Matan, 2026-09-21.** The box does not run the model fast enough
to serve a live request per audio file, which is the whole reason the integration is asynchronous.
That sentence, and the Nebius endpoint lifecycle in `../ops.md`, need rewriting when this
graduates.
