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
| Secrets | `limon-database-url`, `limon-supabase-service-role-key`, `nebius_token_factory_tagger_api_key` |
| Supabase project | ref `jgwizkcobefvhrndojij` — `https://jgwizkcobefvhrndojij.supabase.co` (EU) |
| Nebius parent project | `project-e00mbv9spr00twx6t5saw7` |

The Supabase project is in the EU while Cloud Run is in `us-east1`; the cross-Atlantic latency
is known and accepted. Earlier resources were in `europe-west3` — anything naming that region
predates the move, including both deploy scripts' defaults and the still-existing
`limon-502611-limon-blobs` bucket.

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

`scripts/provision_trigger.sh` stands up the trigger chain (topic, GCS finalize notification,
push subscription, queue) and wires `LIMON_TASKS_*` plus transcriber and service-role values. It
runs **after** the service exists, since it needs the service URL.

**Known defect:** `provision_trigger.sh:78` derives the bucket as `${PROJECT}-limon-blobs`,
which is the retired EU bucket, not `limon-502611-limon-blobs-us-east1`. The trigger chain was
provisioned by hand because of this. Either add a `--bucket` flag or fix the derivation before
relying on the script.

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
| `transcribed` | transcript written (`chars=N`) | the endpoint — `retry reason=…` is down, `failed reason=…` is bad audio |

`recordings.state` is the durable companion, readable from Supabase after logs age out.

A JWT for manual API calls (they expire in about an hour):

```bash
export JWT=$(curl -s "$SUPABASE_URL/auth/v1/token?grant_type=password" \
  -H "apikey: $ANON_KEY" -H "Content-Type: application/json" \
  -d '{"email":"…","password":"…"}' | jq -r .access_token)
```

The `STEP=` logs need no JWT and are the audit trail when tokens are inconvenient.

## The transcription endpoint lifecycle

The Nebius L40S is **down by design** — an idle GPU is the only way this feature becomes
expensive — and there is **no automated dead-man switch**. A standing Cloud Scheduler teardown is
itself a way to drift past the free tier, and there is one operator watching one endpoint. The
safety net is discipline. **Teardown is Matan's**, through the Nebius web console.

Raising it costs money. Ask first.

```
scripts/endpoint/up     # create + warm, print URL and token
scripts/endpoint/down   # delete it
```

`up` is idempotent — it reuses the endpoint in its gitignored state file rather than creating a
second billing one — self-generates the data-plane token, and warms the endpoint with one
throwaway transcription. **Warmup doubles as the readiness probe**: only an inference request
wakes a cold endpoint, so never health-ping to probe. State (`id`, `url`, `token`) is written
*immediately after create, before URL resolution*, so a billing endpoint can never be orphaned
without its id.

`down` **deletes** rather than stops: deleted is true zero cost, and both values change on every
recreate anyway, so stopping preserves nothing.

**`LIMON_TRANSCRIBER_ENDPOINT_URL` and `_TOKEN` must be pushed onto Cloud Run after every
raise** — they change every time, and they are dead the moment the endpoint is torn down.

### These scripts do not match how they were documented

Planning documents described a `scripts/endpoint/wire` helper, a `scripts/endpoint/README.md`
runbook, and a 2026-07-26 hardening pass on `up` (parsing the CLI's text output, an auth
precheck, a by-name orphan guard, a `0600` log with the token masked).

**None of it exists in this repository, on any branch.** What is on disk is the earlier version:

- `up` finds the URL by walking the create response for the first `https://` value. Nebius CLI
  0.12.x prints **text**, not JSON, for `create`, so the walk misses and the script dies with
  "URL not found" against an endpoint that was created and **is billing**. Id and token are
  saved first, so `down` works and a re-run retries the URL — but expect the failure.
- There is no `wire`. Pushing URL and token onto Cloud Run is manual, or
  `provision_trigger.sh --transcriber-url/--transcriber-token`.
- `scripts/endpoint/.create.json` is world-readable and has held a token.

`scripts/e2e_recording_test.sh`, referenced in older notes, was written and deleted without ever
being committed.

Cost safety, since nothing enforces it: after any session, confirm `nebius ai endpoint list`
shows nothing running.

## Demo sequencing

Raise the endpoint **first, then record.** Nothing sweeps up recordings made while it was down
(`roadmap.md`). Cold start is the slow part; once running the endpoint is fast (`rtf` around
0.05). Raise it 30–45 minutes ahead, verify with one real transcription, keep it up through the
demo, delete it immediately after.

## Rebuilding the GCP side

1. `deploy_gcp.sh` — service, bucket, runtime service account, database secret.
2. Start the API once so `create_all` builds the tables.
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
4. **Tables**: start the API once against the new database.
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
