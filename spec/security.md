# Security posture

What is deliberately open, what that costs, and what has not been reviewed. Everything here is a
known trade-off rather than an oversight — but several were taken for a single-operator proof of
concept and do not survive contact with real users.

A full security review has not been done. This file is the input to it.

## `/internal/*` is unauthenticated in production

**The top item.** The intended auth is OIDC — a Google-signed token from Cloud Tasks and from the
Pub/Sub push subscription, verified against Google's certs with an expected audience. It was
deferred and **never wired**.

The interim guard is a shared secret (`LIMON_INTERNAL_TASK_TOKEN`), and **it is unset in
production**, which makes `require_internal_auth` return immediately. The service is
`--allow-unauthenticated` because the mobile client cannot produce Google IAM identity tokens.

So anyone on the internet can `POST /internal/transcribe`, `/internal/tag`, or `/internal/uploaded`.

The blast radius is genuinely small, which is why this was accepted: acting on a forged call
requires a real, currently-`pending`, owned `recordId` — an unguessable UUID4 — and the worker's
claim makes replays no-ops. The realistic abuse is burning GPU or tagger budget, not reading or
writing another user's data.

This is in-pattern with the JWKS verification already in `app/core/auth.py` when it gets built.

## RLS is the only thing protecting reads

The client reads Supabase directly, so the anon key ships inside the app bundle and is trivially
extractable. Anyone holding it can reach PostgREST. **The row-level security policies are the
entire read gate** (`realtime-reads.md`).

The invariant that matters is not "every table has a policy" but **"every table in the exposed
schema has RLS *enabled*"** — a table with RLS not enabled needs no policy to be world-readable
through the anon key. Today `events` and `tags` carry owner-only `SELECT` policies and
`recordings` and `users` are enabled with no policy, which is deny-all.

A future table shipped without `ENABLE ROW LEVEL SECURITY` would be silently world-readable. The
frontend asked for a tripwire — a CI check asserting RLS-on for every `public` table — and it
**has not been built** (`open-questions.md`).

## Realtime broadcasts deleted rows to everyone

Realtime applies no RLS to DELETE messages, and `REPLICA IDENTITY FULL` makes the DELETE
old-record the whole row. Every subscriber therefore receives **every deleted row's full
contents, across all users** — tag names and colors, event titles and bodies.

This is a privacy leak of real user content, accepted eyes-open with the frontend, because the
same setting is what makes UPDATE delivery work at all. It is the most serious known exposure
and the mitigations are recorded in `open-questions.md`.

## CORS defaults to `*`

`LIMON_CORS_ORIGINS` defaults to `["*"]` for development convenience. Production currently sets
it to the Vercel origin. It must be restricted deliberately, not left to the default.

Note that CORS is not a meaningful control for the mobile client, which does not enforce it —
it matters only for browser origins.

## Secrets

The database URL, the Supabase service-role key, and the tagger API key come from Secret Manager
and are mounted as environment variables. The transcriber URL and token are plain environment
variables — they rotate on every endpoint recreate and are short-lived by nature, but the token
is a bearer credential sitting in the service config, and `scripts/endpoint/.create.json` on the
operator's machine is world-readable and has held one (`ops.md`).

No service-account key files exist anywhere: signing uses Application Default Credentials plus
IAM `signBlob`, and on Cloud Run the attached service account signs as itself.

## Logging discipline

Audio bytes, transcript text, and the tagger's reasoning are **never** logged. The pipeline
markers carry ids, states, counts, and timings only. `recordings.error` holds a short reason,
never transcript content or endpoint internals. Delete-account logs its failure cause
server-side but returns a generic message to the client.

## Ownership and enumeration

Every app-facing query is scoped by `user_id`, and a foreign row is answered `404` rather than
`403` so the API never confirms that another user's id exists (`api.md`). Identity always comes
from the verified token; the client cannot supply a user id.

## Not reviewed

Rate limiting (none), request size limits beyond the signed-URL cap, abuse of the demo-seed
endpoint, and the cost ceiling on the tagger. None of these have been looked at.
