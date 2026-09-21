# Operations

## Fixed values

| | |
|---|---|
| GCP project | `limon-502611` |
| Region | `us-east1` |
| Cloud Run service | `limon-api` — `https://limon-api-610976310144.us-east1.run.app` |
| Runtime service account | `limon-api-runtime@limon-502611.iam.gserviceaccount.com` |
| Bucket | `limon-502611-limon-blobs-us-east1` |
| Cloud Tasks queue | `limon-transcribe` (us-east1) |
| Pub/Sub | topic `limon-uploads`, push subscription `limon-uploads-push` |
| Secrets | `limon-database-url`, `limon-supabase-service-role-key`, Groq key injected as `LIMON_TAGGER_API_KEY` |
| Supabase project | ref `jgwizkcobefvhrndojij` — `https://jgwizkcobefvhrndojij.supabase.co` (EU) |
| Transcriber box | Oracle free-tier ARM VPS, `ssh oracle-vps` (its own repo owns it) |

Everything runs in `us-east1`. The Supabase project is in the EU; the cross-Atlantic latency is
known and accepted.

**Both deploy scripts default to the wrong region and bucket** — they predate the current setup.
Always pass `--region us-east1`, and see the bucket defect below.

## Deploying

For a routine code deploy:

```bash
gcloud run deploy limon-api --source . --region us-east1 --project limon-502611
```

**Do not use `scripts/deploy_gcp.sh` to ship a code change.** It bootstraps infrastructure
(service account, bucket, IAM bindings, database secret) and its deploy step passes
`--set-env-vars`, which **replaces** the service's environment — wiping the Cloud Tasks,
transcriber, and tagger variables it knows nothing about. Use it for first-time bootstrap or
infrastructure changes, and re-apply the rest of the environment afterwards.

Tagging uses GroqCloud. Its Cloud Run configuration is
`LIMON_TAGGER_API_KEY` (secret), `LIMON_TAGGER_MODEL=qwen/qwen3.8-27b`, and
`LIMON_TAGGER_BASE_URL=https://api.groq.com/openai/v1`; `LIMON_TAGGER_TIMEOUT_S` is optional.

`scripts/provision_trigger.sh` stands up the trigger chain (topic, GCS finalize notification,
push subscription, queue) and wires `LIMON_TASKS_*` plus transcriber and service-role values. It
runs **after** the service exists, since it needs the service URL.

**Known defect:** `provision_trigger.sh:78` derives the bucket as `${PROJECT}-limon-blobs`, not
the `-us-east1` bucket the service actually uses. A bucket by that name still exists, so the
script fails silently by wiring the GCS notification to the wrong one. The trigger chain was
provisioned by hand because of this. Either add a `--bucket` flag or fix the derivation.

The manual equivalent, which is what was actually run:

```bash
gcloud services enable pubsub.googleapis.com cloudtasks.googleapis.com --project $PROJECT
gcloud tasks queues create limon-transcribe --project $PROJECT --location $REGION
gcloud pubsub topics create limon-uploads --project $PROJECT
gcloud pubsub topics add-iam-policy-binding limon-uploads --project $PROJECT \
  --member="serviceAccount:$(gcloud storage service-agent --project $PROJECT)" \
  --role=roles/pubsub.publisher
gcloud storage buckets notifications create "gs://$BUCKET" --project $PROJECT \
  --topic=limon-uploads --event-types=OBJECT_FINALIZE --payload-format=json
gcloud pubsub subscriptions create limon-uploads-push --project $PROJECT \
  --topic=limon-uploads --push-endpoint="$API/internal/uploaded" --ack-deadline=60
gcloud run services update limon-api --project $PROJECT --region $REGION \
  --update-env-vars "LIMON_TASKS_PROJECT=$PROJECT,LIMON_TASKS_LOCATION=$REGION,\
LIMON_TASKS_QUEUE=limon-transcribe,LIMON_TASKS_WORKER_URL=$API"
```

### Schema changes come first

There are no migrations (`data-model.md`). **After deploying code that adds a model column, the
matching `ALTER` must already be applied to the live database** — otherwise every query touching
that table `500`s service-wide. Postgres reports only the first missing column, so a batch of
adds must all be applied before the error clears.

Production DDL is run by hand in the Supabase SQL editor, by Matan. Hand him the SQL; do not run
it against the live database.

## Watching a run

`recordId` is the correlation id, and Cloud Run streams stdout to Cloud Logging:

```bash
gcloud logging read \
  'resource.type=cloud_run_revision AND resource.labels.service_name="limon-api"
   AND textPayload:"STEP=" AND textPayload:"<recordId>"' \
  --project limon-502611 --freshness=1h --order=asc --format='value(textPayload)'
```

| Marker | Means | Absent ⇒ stalled at |
|---|---|---|
| `event_created` | event + pending recording + signed URL | create, or GCS signing |
| `finalize_received` | GCS finalize reached the service | notification or push subscription (look for `finalize_ignored reason=…`) |
| `task_enqueued` | Cloud Task enqueued | queue permissions (enqueue failure `5xx`s, Pub/Sub retries) |
| `claimed` | worker claimed it (`pending → transcribing`) | the task never arrived, or `noop reason=claim_lost/already_done` |
| `submitted` | audio handed to the box (`bytes=N`) | the box — `retry reason=…` is unreachable or its queue is full, `failed reason=…` is bad or over-long audio |
| `callback_received` | the box says results are waiting | the callback — check `CALLBACK_URL` on the box and `callback.*` in its `GET /status` |
| `transcribed` | transcript written (`chars=N`) | collection — run the sweep by hand; `collect_*` markers say why a job could not be stored |

`recordings.state` is the durable companion, readable from Supabase after logs age out.

A JWT for manual API calls (they expire in about an hour):

```bash
export JWT=$(curl -s "$SUPABASE_URL/auth/v1/token?grant_type=password" \
  -H "apikey: $ANON_KEY" -H "Content-Type: application/json" \
  -d '{"email":"…","password":"…"}' | jq -r .access_token)
```

The `STEP=` logs need no JWT and are the audit trail when tokens are inconvenient.

## The transcriber box

An always-on Oracle free-tier ARM VPS, reachable as `ssh oracle-vps`. It costs nothing, runs
continuously, and **replaced the Nebius L40S on 2026-09-21** — so the raise-before-recording
sequencing this section used to carry is gone with it (`archive.md`). Nebius teardown is Matan's and
the endpoint is already down.

Its own repo (`~/dev-projects/hebrew-transcriber`, private) owns deployment, the contract and the
runbook for the box itself. What follows is only the LimON-side configuration.

### The base URL rotates, and that is normal

The box is fronted by a **Cloudflare Quick Tunnel**, whose address is reminted whenever
`cloudflared` restarts — typically a reboot. There is no API to discover it; the startup banner is
the only place it exists, and the tunnel is deliberately `restart: "no"` so that an automatic
restart cannot silently invalidate an address already in use.

**Treat "the transcriber stopped resolving" as an operating condition, not an incident.** Nothing is
lost while it is stale: submissions fail soft, work sits `pending`, and the daily sweep re-drives it
once the URL is corrected. What does happen is that transcription silently stops, and nothing alerts
on that (`open-questions.md`).

After any reboot of the box:

```bash
ssh oracle-vps 'cd ~/hebrew-transcriber && docker compose --profile tunnel up -d cloudflared'
ssh oracle-vps 'cd ~/hebrew-transcriber && ./deploy/cpu/tunnel-url.sh --check'
```

Then push the new address onto Cloud Run. `--update-env-vars` merges rather than replacing, so it
will not clobber the rest of the environment:

```bash
gcloud run services update limon-api --project limon-502611 --region us-east1 \
  --update-env-vars "LIMON_TRANSCRIBER_BASE_URL=$BOX_URL"
```

### Wiring the two sides together

The token and the callback secret are **Secret Manager references**, not plain environment
variables, matching `limon-database-url` and the other two. `LIMON_TRANSCRIBER_ENDPOINT_TOKEN` was a
plain env var on the old endpoint and should not be copied.

On the box, so it can call us back:

```
CALLBACK_URL=https://limon-api-610976310144.us-east1.run.app/internal/transcripts-ready
CALLBACK_AUTH=secret
CALLBACK_SECRET=<the value of LIMON_TRANSCRIBER_CALLBACK_SECRET>
```

**`CALLBACK_AUTH=oidc` cannot work from this box** and must not be set: it mints the token from the
GCP metadata server, which only resolves when the sender is itself on GCP (`transcription.md`).

**There is no Cloud Scheduler job and no cron.** Recovery rides on the box's callback rather than a
clock, because audio is never deleted from GCS and so nothing stranded ever expires
(`transcription.md`). `cloudscheduler.googleapis.com` is not enabled on the project and does not
need to be.

The one case that stays slow is this section's own failure mode: while the tunnel URL is stale
nothing submits, so nothing finishes, so nothing wakes the service. Fixing the URL does not itself
trigger recovery — the next recording does. To not wait:

```bash
curl -X POST -H "X-Callback-Token: $CALLBACK_SECRET" \
  https://limon-api-610976310144.us-east1.run.app/internal/transcripts-sweep
```

### Verifying it end to end

`scripts/transcriber/roundtrip.py` drives the whole cycle against the live box — submit, drain,
persist, acknowledge — using the real service functions and a throwaway in-memory database. It
covers everything except the wakeup, which needs an endpoint the box can reach:

```bash
uv run python scripts/transcriber/roundtrip.py \
  ~/dev-projects/hebrew-transcriber/audio/clip1_normal.ogg
```

**It filters its drains by the ids it submitted, and that is not optional.** The box has one queue
and no per-caller scoping, so an unfiltered drain returns every caller's results and the ack that
follows deletes them. Coordinate before running anything unfiltered against a box someone else is
using.

## Rebuilding the GCP side

1. `deploy_gcp.sh` — service, bucket, runtime service account, database secret.
2. Create the tables: run `scripts/supabase/create_tables.sql`, or start the API once and let
   `create_all` build them.
3. Apply `scripts/supabase/setup.sql` — RLS, replica identity, publication
   (`realtime-reads.md`). Idempotent; re-run it every time the tables are recreated. Use a
   direct session connection, **never the transaction pooler**, for DDL.
4. `provision_trigger.sh` — trigger chain and environment (mind the bucket defect above).
5. Raise the endpoint, wire its URL and token.
6. Drive one real audio event end to end.

The runtime service account needs `roles/cloudtasks.enqueuer` granted **at the queue level** on
`limon-transcribe`, not project-level — a project-level grant is not something plain
`roles/editor` can set. Verify:

```bash
gcloud tasks queues get-iam-policy limon-transcribe --location us-east1
```

## Rebuilding the Supabase project

A project's region cannot be changed; moving regions means creating a new project and migrating
into it. The same checklist covers a greenfield setup.

> **The hazard that loses everything: user ids.** `users.id` **is** the Supabase auth uid
> (`auth.md`). On a fresh project, Supabase mints **new** uids for the same Google accounts, so
> every existing `users`, `events`, `tags`, and `recordings` row is orphaned. Keeping data
> therefore requires copying the `auth` schema rows, not just ours. Skip that only when
> discarding all accounts on purpose.

1. **Create the project** in the target region. Save the database password.
2. **Google OAuth**: enable the provider with the same Google OAuth client, add the new
   callback `https://<ref>.supabase.co/auth/v1/callback` to that client's authorized redirect
   URIs, and copy the Site URL and redirect allow-list (Expo deep links) from the old project.
3. **Connection string**: use the **session pooler** (IPv4). The direct `db.<ref>.supabase.co`
   host is IPv6-only and unreachable from Cloud Run. The SQLAlchemy form is
   `postgresql+asyncpg://postgres.<ref>:<password>@<pooler-host>:5432/postgres?ssl=require`.
   Add it as a new version of `limon-database-url`; Cloud Run picks up `:latest` on the next
   revision.
4. **Tables**: run `scripts/supabase/create_tables.sql` in the SQL editor. It exists precisely
   for this — `create_all` would also build them, but only from a running app already wired to
   the new database, and here you want the schema in place before anything connects. It is
   generated from the models; regenerate with `scripts/supabase/gen_create_tables.py` if they
   have moved on.
5. **Realtime + RLS**: ensure Realtime is enabled for the project (so the `supabase_realtime`
   publication exists), then apply `setup.sql` and verify:

   ```sql
   select 1 from pg_publication_tables where pubname='supabase_realtime' and tablename='events';
   select relreplident from pg_class where relname='events';   -- want 'f'
   select tablename, policyname from pg_policies;
   select tablename, rowsecurity from pg_tables where schemaname='public';
   ```

   Also confirm the **Data API is on with `public` exposed** (Settings → API). Without it the
   client's snapshot select fails and the timeline comes up empty with no other error.
6. **Backend env**: update `LIMON_SUPABASE_URL` (JWKS verification points at the new project, so
   old tokens stop working immediately — everyone re-signs-in) and
   `LIMON_SUPABASE_SERVICE_ROLE_KEY` (delete-account breaks without it).
7. **Frontend**: project URL and **anon key** both change. Realtime table and column config does
   not.
8. **Migrating data**, only when keeping it — auth rows first, since our FKs hang off ids that
   must equal the migrated auth uids:

   ```bash
   pg_dump "<old session-pooler url>" --data-only \
     --table='auth.users' --table='auth.identities' > auth_rows.sql
   pg_dump "<old session-pooler url>" --data-only --schema=public > public_rows.sql
   ```

   Restore auth first, then public. Verify no orphans:
   `select count(*) from public.users u left join auth.users a on a.id::text = u.id
   where a.id is null;` — must be `0`. Sessions are not migrated; everyone re-signs-in, and
   because uids are preserved, provisioning finds their existing row and the data reappears.
9. **Cutover**: verify a fresh sign-in, `GET /users/me`, event writes, the demo-data button on an
   empty account, and the audio flow. Only then retire the old project — keep the dumps until
   confident, since its dashboard backups age out with it.

## Verifying a deployment

1. `GET /health` returns `{"status":"ok"}`.
2. A protected route without a token returns `401`.
3. `GET /api/v1/users/me` with a valid token returns the caller.
4. Logs contain no database URL, token, or object contents.
