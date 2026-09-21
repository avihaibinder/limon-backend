# Audio and transcription

Audio never passes through this service. The client uploads it straight to GCS, and the object
landing there is what triggers transcription — there is no "confirm" call.

```
POST /events (audio)  ──►  event + pending recording + signed PUT URL
        │
client PUT ─► GCS ─► OBJECT_FINALIZE ─► Pub/Sub ─► POST /internal/uploaded
                                                          │
                                                    Cloud Task
                                                          ▼
                                              POST /internal/transcribe
                                                          │
                                              claim ─► fetch ─► submit
                                                          │
                                                   (the box works)
                                                          │
                          the box ─► POST /internal/transcripts-ready  ("a nudge")
                                                          │
                                     drain ─► persist ─► acknowledge
                                                          │
                                          events.description ─► Realtime ─► client
```

**The transcript does not arrive in the request that submits the audio**, and that is the whole
shape of this design. The box runs at about 0.98x real time on two ARM cores, so holding a request
open for it is not an option -- and Cloud Run scales to zero, so there is no instance left to poll
for it either. The box therefore calls us back, and the callback is a nudge that carries no
transcript: we then run the ordinary drain-persist-acknowledge cycle a timer would have run.

## Upload

The create response carries a V4 signed PUT URL for `v0/{userId}/{recordId}.m4a`, where
`recordId` is the `recordings.id`. The key layout is centralized in
`storage.audio_object_key` / `record_id_from_audio_key` — one function mints it, the other
parses it — so the two ends cannot drift.

Signing uses Application Default Credentials plus IAM `signBlob`, so **no private key exists on
disk**. On Cloud Run the attached service account signs as itself; locally a configured signer
service account is impersonated.

The client must PUT with `Content-Type: audio/mp4` and echo
`x-goog-content-length-range: 0,26214400`, both of which are baked into the signature. GCS
rejects the PUT if either differs from what was signed.

That number is **25 MiB** (25 × 1024 × 1024), not decimal 25 MB, and the distinction is
deliberate: it must stay at or below the transcription endpoint's own `MAX_UPLOAD_MB` so a file
cannot pass GCS and then fail transcription on size. The **cap is enforced by GCS at the
signature**, so an over-cap upload is rejected and the bytes never land — a client that ignores
the limit cannot bypass it.

URLs are valid for 15 minutes by default (`LIMON_GCS_SIGNED_URL_TTL_SECONDS`).

Re-PUTting the same URL replaces the object. If the URL has expired, the client re-creates with
the same `clientEventId` for a fresh one (`api.md`).

## The trigger

GCS publishes `OBJECT_FINALIZE` to a Pub/Sub topic; a push subscription delivers it to
`/internal/uploaded`, which recovers `recordId` from the object name and enqueues **one Cloud
Task** targeting `/internal/transcribe`.

A Cloud Task rather than pushing Pub/Sub straight at the worker, because the queue is what gives
a **capped retry budget** and a single place to tune backoff without a redeploy. The task
carries `{recordId}` only — never audio bytes — keeping it far under the size threshold.

Duplicate finalize notifications are harmless: they enqueue duplicate tasks, and the worker's
claim makes the second one a no-op.

## The worker: submission

`/internal/transcribe` claims a recording and hands its audio to the box. It is idempotent under
at-least-once delivery. In order:

1. **No recording** → no-op. (The event was deleted; nothing to do.)
2. **Already `done`** → no-op. A retry after success must never resubmit or double-write.
3. **Atomic claim.** A single conditional `UPDATE … SET state='transcribing' WHERE id=… AND
   state IN ('pending','failed')`, then check the row count. Losing the race means another worker
   owns it → no-op. This one statement is what makes duplicate delivery safe; a read-then-write
   would not be.
4. Resolve the event by `recording_id`.
5. **Pre-flight the limits** — `duration_sec` against 600 s, and the downloaded byte length against
   25 MiB — so a submission the box would refuse is never sent.
6. Fetch the audio and `POST /jobs`.
7. Return `200` and stop. **The row stays `transcribing`.**

**`transcribing` means "submitted, waiting for a callback"**, and it can legitimately sit there for
as long as the box's queue is deep. That is the one meaning that changed when this moved off the
synchronous endpoint, and the backstop sweep below is what keeps it from becoming a black hole.

The consequence worth stating plainly: **the Cloud Tasks retry budget now covers submission only.**
Transcription itself is under no deadline at all. The failure class this deletes is the one that
used to dominate — anything recorded while the endpoint was down exhausted its retries permanently
(`archive.md`).

`job_id` **is `recordings.id`.** The box requires the caller to supply the id and requires it to be
unguessable, because one shared token means any token-holder could otherwise walk ids and read
another caller's transcripts; a UUID4 we already hold satisfies that and needs no extra column.

### Hard versus soft failures

The distinction decides whether the queue retries, and it maps onto HTTP status:

- **Hard** — the audio is the problem (`400` rejected, `413` over a limit), no event is linked, the
  object is missing, or a `409` survived one discard-and-resubmit. Mark the recording `failed` with
  a short reason and return `2xx`: retrying cannot fix it. `error` never holds transcript text or
  box internals.
- **Soft** — `503` (the box's queue is full of audio), unreachable, unconfigured, `401`, or storage
  temporarily unreadable. Revert to `pending` and return **`503` with `Retry-After`**.

**The base URL not resolving is normal, not an alert.** The box is fronted by a Cloudflare Quick
Tunnel whose address is reminted on every restart (`ops.md`), so "unreachable" is an expected
operating condition. Work reverts to `pending` and the sweep re-drives it once the URL is corrected.

**`413` is ambiguous on purpose and we do not disambiguate it.** The box uses it for both "body over
`MAX_UPLOAD_MB`" and "audio over `MAX_AUDIO_DURATION_S`", and telling them apart needs
string-matching the message. Both are permanent here and neither remedy (re-encode, split) is work
this service does, so the pre-flight in step 5 is the real defense and reaching `413` at all means
our configured limits have drifted from the box's.

**A recording over 600 s is rejected, not split.** Splitting the audio into several jobs and
reassembling the transcripts in order is a genuine feature and is deferred (`roadmap.md`); the
rejection is what makes deferring it safe, since a too-long recording fails visibly.

### The retry budget

The `limon-transcribe` queue (us-east1) is capped at **3 attempts**, 60s min / 600s max backoff, so
a submission has roughly a three-minute window to land. The configuration lives in the queue, not in
code: `gcloud tasks queues describe limon-transcribe --location=us-east1`.

Three minutes is the right size for a submission and would have been badly wrong for a
transcription. A queue full of audio (`503`) or a rotated tunnel URL can outlast it comfortably, and
that gap is exactly what the backstop sweep exists to cover rather than something to solve by
raising the cap — it was 100 attempts with a 3600s ceiling once, which hammered a dead endpoint for
hours (`archive.md`).

Re-drive one by hand with `POST /internal/transcribe {"recordId": …}`; the claim makes that
idempotent, and it no-ops with `reason=no_recording` if the event was deleted.

## Collection: the callback and the cycle

**`POST /internal/transcripts-ready`** is the box telling us results are waiting. It carries **no
transcript and never will** — it names a job, counts what is waiting, and stops.

The body is deliberately ignored except for logging. The endpoint **drains everything**, not the
job the callback named: an earlier callback may have been lost and this one is the first wakeup
since, so what is ready is not what is announced.

The cycle, and the order is the design:

1. `GET /jobs?status=done,failed&include=text&limit=200`, paged while
   `len(jobs) < done_unacked + failed_unacked` from the same envelope.
2. **Persist** each job in its own committed transaction.
3. `POST /jobs/ack` with the ids that reached a terminal decision.

**Persist, then acknowledge, always.** The ack is the only thing that deletes a result on the box,
so a crash between the two loses a transcript permanently while a crash the other way round costs
one redundant drain. An id whose persist raised is left out of the ack and comes back next time.

Three details in step 1 are load bearing and each has cost someone a working implementation:

- **`include=text`, not `include_text=true`.** An unknown query parameter is *ignored, not
  rejected*, so the wrong name returns `200` with every transcript missing and no error.
- **`limit=200`.** The box defaults to 50 against a cap of 200 and says nothing about having
  truncated.
- **The envelope counts, not a short page.** `done_unacked`/`failed_unacked` are a number rather
  than an inference. They are global to the box, so they only mean this for an *unfiltered* drain.

What each outcome persists:

| Job | Recording becomes | Acked? |
|---|---|---|
| `done` | `done`, transcript written to `events.description` | yes |
| `done`, already `done` here | unchanged | yes — this is the idempotency case |
| `failed`, `audio_missing` | `pending`, for the sweep to resubmit | yes — acking frees the id |
| `failed`, anything else | `failed` with the `error.code` | yes |
| no such recording | nothing to store | **yes, deliberately** |

**A job with no recording is acknowledged rather than left.** The account or event was deleted while
the box worked; not acking would leave it on the box until the 30-day TTL, and that TTL doing real
work is a bug in this caller.

**Two concurrent drains are safe.** Two callbacks can wake two Cloud Run instances onto the same
jobs — the box has no lease. A conditional `UPDATE … WHERE id=… AND state != 'done'` decides which
one writes the transcript; the loser skips the write and still acknowledges.

**The error taxonomy is nested: `error.code`, not a flat `error_code`.** By the time a job reaches
us as `failed` the box has spent both of its attempts, so `not_audio`, `transcription_failed` and
`retry_exhausted` are all permanent. `error.retryable` describes the failure's nature, not an
instruction to resubmit.

Auto-tagging is enqueued here, after a transcript lands (`tagging.md`) — best-effort, so an enqueue
failure cannot undo a transcript that is already stored.

## The backstop sweep

**`POST /internal/transcripts-sweep`**, daily via Cloud Scheduler. The callback is the fast path and
never a guarantee: the box retries delivery five times over about a minute and then gives up, and
giving up is safe precisely because nothing is deleted — the results wait, and something has to
eventually come and get them.

Three jobs, in order:

1. Run the collection cycle unconditionally.
2. Recordings `transcribing` past `LIMON_TRANSCRIBER_STALE_SUBMITTED_HOURS`: ask `GET /jobs/{id}`.
   A `404` is ambiguous — unknown, acknowledged or expired — and all three mean no transcript is
   coming, so revert to `pending` and re-enqueue. A non-terminal status means it is still queued
   behind other work, which is not a stall: the box is slower than real time by design.
3. Recordings `pending` past `LIMON_TRANSCRIBER_STALE_PENDING_MINUTES`: re-enqueue.

Step 3 closes a defect this design inherited: a submission that outlived its Cloud Tasks budget
reverted to `pending` and became indistinguishable from one that had not started, needing a human
to re-drive it (`archive.md`).

Both routes authenticate with the box's shared secret and **fail closed** — an unset secret rejects
rather than opens, unlike the other `/internal/*` routes (`security.md`). The two rejection codes
differ because the box treats them differently: `503` for "not configured" is a 5xx it retries,
`401` for a wrong secret is a 4xx it deliberately does not.

## The transcription box

An always-on Oracle free-tier ARM VPS (2 vCPU, aarch64) serving
`ivrit-ai/whisper-large-v3-turbo-ct2` at `int8` / beam 5, behind an async job API and a bearer
token. It replaced the Nebius L40S endpoint on 2026-09-21; what that used to look like, and why it
changed, is in `archive.md`.

**The contract is not ours and is not restated here.** It lives in
`~/dev-projects/hebrew-transcriber/spec/job-api.md`, which is the authority for routes, status
codes, the error taxonomy and the callback. `BACKEND_BRIEF.md` in the same repo is the orientation.

Numbers this service is built against:

| | | Why it matters here |
|---|---|---|
| Throughput | **0.98x real time** | below real time; a continuous feed grows the queue rather than draining it. Bursts with gaps are the operating assumption. |
| Max upload | 25 MiB (`25 × 1024 × 1024`) | **exactly** the signed-URL cap above. The two are deliberately equal; moving one without the other reopens a gap where a file passes GCS and then fails transcription. |
| Max audio duration | 600 s | the real length limit, and the one we pre-flight |
| Max resident queue | 7200 s of audio, then `503` | can exceed the Cloud Tasks budget by a wide margin, which is what the sweep is for |
| Result TTL if never acked | 30 days | a backstop; if it fires, that is our bug |
| Max ids per `ack` / `GET /jobs` | 200 | |

Two properties are **not** this service's problem and should not be built for: the box detects the
audio type itself (no allowlist, no conversion, no demuxing — filename and content type are not
consulted), and it encrypts audio at rest with a key that never leaves it. No route returns audio,
by contract and by test.

**Its Hebrew quality is essentially unmeasured** — one four-clip smoke test in that repo, explicitly
not a ranking. There is no number to quote.

**The callback cannot use OIDC**, despite it being the better mechanism: the box mints the token
from the GCP metadata server, which only resolves when the *sender* runs on GCP, and it runs on
Oracle. A shared secret is the only option, which is also why this needs no extra service account.

## Logging

Never audio bytes, never transcript text. Only ids, state transitions, counts (`chars=N`), and
timing numbers. Each hop emits its `STEP=` marker keyed by `recordId` (`architecture.md`).
