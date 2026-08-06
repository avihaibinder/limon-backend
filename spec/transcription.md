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
                                            claim ─► fetch ─► Nebius ─► write
                                                          │
                                          events.description ─► Realtime ─► client
```

## Upload

The create response carries a V4 signed PUT URL for `v0/{userId}/{recordId}.m4a`, where
`recordId` is the `recordings.id`. The key layout is centralized in
`storage.audio_object_key` / `record_id_from_audio_key` — one function mints it, the other
parses it — so the two ends cannot drift.

Signing uses Application Default Credentials plus IAM `signBlob`, so **no private key exists on
disk**. On Cloud Run the attached service account signs as itself; locally a configured signer
service account is impersonated.

The client must PUT with `Content-Type: audio/mp4` and echo
`x-goog-content-length-range: 0,26214400`, both of which are baked into the signature. The
**25 MB cap is enforced by GCS at the signature**, so an over-cap upload is rejected and the
bytes never land — the limit cannot be bypassed by a client that ignores it. It sits at or below
the transcription endpoint's own `MAX_UPLOAD_MB` so nothing can pass GCS and then fail
transcription on size.

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

## The worker

`/internal/transcribe` is idempotent under at-least-once delivery. In order:

1. **No recording** → no-op. (The event was deleted; nothing to do.)
2. **Already `done`** → no-op. A retry after success must never re-transcribe or double-write.
3. **Atomic claim.** A single conditional `UPDATE … SET state='transcribing' WHERE id=… AND
   state IN ('pending','failed')`, then check the row count. Losing the race means another
   worker owns it → no-op. This one statement is what makes duplicate delivery safe; a
   read-then-write would not be.
4. Resolve the event by `recording_id`, fetch the audio, call the endpoint.
5. **On success**, in one transaction: write the transcript to `events.description` and set the
   recording `done`. `description` going non-null *is* the client's completion signal
   (`realtime-reads.md`).

Then, if the event carries no tags, auto-tagging is enqueued (`tagging.md`) — best-effort, so a
tagging enqueue failure cannot turn a successful transcription into a retry.

### Hard versus soft failures

The distinction decides whether the queue retries, and it maps onto HTTP status:

- **Hard** — the audio itself is the problem (rejected, too large), no event linked to the
  recording, or the object is missing. Mark the recording `failed` with a short reason and
  return `2xx`: retrying cannot fix it. `error` never holds transcript text or endpoint
  internals.
- **Soft** — the endpoint is busy, unreachable, unconfigured, or errored; or storage is
  temporarily unreadable. Revert to `pending` and return **`503` with `Retry-After`** so the
  queue retries within its budget.

**The endpoint being down is normal, not an alert.** It is deliberately raised only for
sessions (`ops.md`), so "unavailable" is the steady state and must stay cheap.

## The transcription endpoint

A container running `ivrit-ai/whisper-large-v3-ct2` (faster-whisper / CTranslate2) on a Nebius
L40S serverless endpoint, behind a bearer token. `POST /transcribe` takes `multipart/form-data`
with one field named `file` and returns `text`, `segments`, and timing numbers including `rtf`.

Fixed properties worth knowing before changing anything:

- **Single-flight.** It processes one transcription at a time; a second request waits for the
  slot and then gets `503` with `Retry-After`. Concurrency control belongs here, in the backend
  and its queue — never fan out parallel calls at one endpoint.
- **Hebrew is forced.** The model's language autodetect is degraded and guesses wrong; `he` is
  always passed explicitly.
- **VAD stays on.** large-v3 hallucinates confident repeated phrases over silence, and users
  pause while thinking.
- Quality settings are fixed at `float16`, `beam_size=10`, `condition_on_previous_text` on,
  measured at `rtf` about 0.087 on the L40S — roughly `0.09 × audio_seconds` of processing.

`transcriber_timeout_s` defaults to 90s, sized from that ratio with headroom for a warming
endpoint: a 5-minute clip is about 30s of work. The stacked deadlines must stay ordered
outermost-longest — Cloud Tasks dispatch ≥ Cloud Run request timeout ≥ expected endpoint call.

The URL and token change on **every** endpoint recreate, so they are deployment config, never
constants (`ops.md`).

## Retry budget, and the state it leaves behind

The `limon-transcribe` queue (us-east1) is capped at **3 attempts**, 60s min / 600s max backoff.
It was 100 attempts with a 3600s cap, which hammered a dead endpoint for hours. A recording
therefore has roughly a three-minute window to succeed.

**This interacts badly with the endpoint normally being down**, and the result is worth stating
plainly: anything recorded while the endpoint is down exhausts its retries permanently. Worse,
the soft-failure path reverts the row to `pending`, so an exhausted recording is left
`pending` — never `failed` — and is indistinguishable from one that has not started yet.

Re-drive one by hand with `POST /internal/transcribe {"recordId": …}`; the claim makes that
idempotent, and it no-ops with `reason=no_recording` if the event was deleted.

The queue configuration lives in the queue, not in code:
`gcloud tasks queues describe limon-transcribe --location=us-east1`.

## Logging

Never audio bytes, never transcript text. Only ids, state transitions, counts (`chars=N`), and
timing numbers. Each hop emits its `STEP=` marker keyed by `recordId` (`architecture.md`).
