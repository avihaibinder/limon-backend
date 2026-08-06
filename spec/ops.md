# Operations

Production is GCP project **`limon-502611`**, region **`us-east1`**: Cloud Run service
`limon-api`, bucket `limon-502611-limon-blobs-us-east1`, Cloud Tasks queue `limon-transcribe`,
Pub/Sub topic `limon-uploads` with push subscription `limon-uploads-push`. The database is
Supabase project `jgwizkcobefvhrndojij`, in the EU — the cross-region latency is known and
accepted.

Earlier resources were in `europe-west3`; any document still naming that region is stale.

## Deploying

For a routine code deploy, deploy the source directly:

```bash
gcloud run deploy limon-api --source . --region us-east1 --project limon-502611
```

**Do not reach for `scripts/deploy_gcp.sh` to ship a code change.** It bootstraps
infrastructure (service account, bucket, IAM bindings, the database secret) and its deploy step
passes `--set-env-vars`, which **replaces** the service's environment. That would wipe the
Cloud Tasks, transcriber, and tagger variables, which the script does not know about — they are
set by `provision_trigger.sh` and by hand. Use it for first-time bootstrap or when infrastructure
changes, and re-apply the missing environment afterwards.

`scripts/provision_trigger.sh` stands up the trigger chain (topic, GCS finalize notification,
push subscription, Cloud Tasks queue) and wires `LIMON_TASKS_*` plus the transcriber and
service-role values. It runs **after** the service exists, since it needs the service URL.

Both scripts default to `europe-west3`; pass `--region us-east1`.

### Schema changes come first

There are no migrations (`data-model.md`). **After deploying code that adds a model column, the
matching `ALTER` must already have been applied to the live database** — otherwise every query
touching that table `500`s service-wide. Postgres reports only the first missing column, so a
batch of adds must all be applied before the error clears.

Production DDL is run by hand in the Supabase SQL editor, by Matan. Hand him the SQL; do not
execute it against the live database.

The known-required statements are listed in `CLAUDE.md`'s notes as they accumulate.

## The transcription endpoint lifecycle

The Nebius L40S is **down by design**. An idle GPU is the only way this feature becomes
expensive, and there is **no automated dead-man switch** — no Cloud Scheduler teardown, because a
standing scheduler is itself an easy way to drift past the free tier. Teardown is manual and it
is Matan's: he deletes the endpoint through the Nebius web console.

Raising it costs money. Ask before running it.

```
scripts/endpoint/up     # create + warm the endpoint, print URL and token
scripts/endpoint/down   # delete it (convenience wrapper; Matan runs teardown)
```

`up` is idempotent — it reuses the endpoint recorded in its gitignored state file rather than
creating a second billing one — self-generates the data-plane token, and warms the endpoint with
a single throwaway transcription. **Warmup doubles as the readiness probe**: only an inference
request wakes a cold endpoint, so never health-ping to probe. It writes `{id, url, token}` to
state *immediately after create, before resolving the URL*, so a billing endpoint can never be
orphaned without its id.

`down` **deletes** rather than stops: a deleted endpoint is true zero cost, and the URL and
token change on every recreate anyway, so stopping preserves nothing.

**Both values change on every recreate**, so `LIMON_TRANSCRIBER_ENDPOINT_URL` and
`_TOKEN` must be pushed onto Cloud Run after each raise.

### Known gaps in these scripts

Documents in `spec-local/` describe a `scripts/endpoint/wire` helper and a
`scripts/endpoint/README.md` runbook, and state that `up` was hardened on 2026-07-26 to parse
the CLI's text output, precheck auth with `nebius iam whoami`, guard against orphans by name,
and mask the token in a `0600` log.

**None of that exists in this repository, on any branch.** What is on disk is the earlier
version:

- `up` finds the endpoint URL by walking the create response for the first `https://` value.
  Nebius CLI 0.12.x prints **text**, not JSON, for `create`, so this walk misses and the script
  dies with "URL not found" against an endpoint that was created successfully and *is billing*.
  Its id and token are saved first, so `down` still works and a re-run retries the URL — but the
  failure is expected, not a surprise.
- There is no `wire` script. Pushing the URL and token onto Cloud Run is manual, or via
  `provision_trigger.sh --transcriber-url/--transcriber-token`.
- `.create.json` is world-readable and has held a token.

`scripts/e2e_recording_test.sh`, also referenced in older notes, was written and deleted without
ever being committed. Treat every runbook reference to these four files as describing work that
was lost. See `open-questions.md`.

Cost safety, since nothing enforces it: after any session, confirm `nebius ai endpoint list`
shows nothing running.

## Rebuilding from scratch

Ordered, because each step needs the previous one:

1. `deploy_gcp.sh` — service, bucket, runtime service account, database secret.
2. Recreate the Supabase tables (startup `create_all` creates them; `spec-local/` holds a DDL
   dump generated from the models).
3. Apply `scripts/supabase/setup.sql` — RLS, replica identity, publication (`realtime-reads.md`).
   Idempotent; re-run it every time the tables are recreated. Use a direct session connection,
   not the transaction pooler, for DDL.
4. `provision_trigger.sh` — the trigger chain and its environment.
5. Raise the endpoint and wire its URL and token.
6. Drive one real audio event end to end.

One IAM detail that cost a session: the runtime service account needs
`roles/cloudtasks.enqueuer` granted **at the queue level** on `limon-transcribe`, not at the
project level — a project-level grant is not something a plain `roles/editor` can set. Verify
with `gcloud tasks queues get-iam-policy limon-transcribe --location us-east1`.

## Watching a run

`recordId` is the correlation id. Cloud Run streams stdout to Cloud Logging, so filtering for it
shows the whole chain, and a missing marker names the hop that never happened
(`architecture.md`):

```
event_created → finalize_received → task_enqueued → claimed → transcribed
```

For a demo, raise the endpoint **first**, then record — there is no backlog re-enqueue that
would sweep up recordings made while it was down (`roadmap.md`). Cold start is the slow part;
once running, the endpoint is fast (`rtf` around 0.05). Raise it 30–45 minutes ahead, verify with
one real transcription, keep it up through the demo, and delete it immediately after.

Supabase JWTs expire in about an hour, so fetch a fresh one before any API reads. The `STEP=`
logs need no JWT and are the durable audit trail.
