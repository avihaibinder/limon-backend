# Identity and authentication

Every app-facing request carries a Supabase-issued JWT as `Authorization: Bearer <token>`.
There is no sign-up call, no password handling, and no user id on the wire — the client signs in
with Supabase Auth (Google OAuth) and this service verifies what Supabase issued.

## The one identity: `users.id` **is** the JWT `sub`

We mint no user ids. `users.id` holds the Supabase `sub` — Supabase's own user uuid, never
Google's — and `events.user_id`, `tags.user_id`, and `recordings.user_id` are foreign keys to it
(`data-model.md`).

This is the decision the rest of the system leans on, so it is worth stating why:

- **RLS becomes a one-liner with no join.** The client reads directly from Supabase, so the
  read gate is a Postgres policy, and `auth.uid()::text = user_id` only works if `user_id`
  *is* the Supabase uid. Any other scheme (our own uuid, a `provider_subject` mapping) forces a
  join inside every policy evaluation. See `realtime-reads.md`.
- **The cast is load-bearing.** `user_id` is `String(36)` text while `auth.uid()` is `uuid`.
  Without `::text` the comparison errors and the policy denies everything — a silent failure
  mode, since it looks like "no rows" rather than an error.
- **Columns stay `String(36)`**, no library UUID type and no new import, precisely so the text
  comparison above is the natural one.

There is a sharp operational consequence worth knowing before it bites: **a new Supabase project
orphans every row we hold.** Supabase mints new uids for the same Google accounts, so `users`,
`events`, `tags`, and `recordings` all point at ids that no longer exist. Migrating a project
therefore means copying the `auth` schema rows, not just ours — see `ops.md`.

Other consequences, accepted deliberately: **one account per Supabase identity** — no
multi-provider linking and no re-keying an account onto a different login. An earlier design carried a
`provider_subject` column and a `(provider, provider_subject)` unique constraint; both only ever
mirrored the `sub`, and both were dropped. `provider` survives for display only.

## Verifying the token

`app/core/auth.py` verifies against the Supabase project's JWKS endpoint for `RS256`/`ES256`,
or a configured shared secret for `HS256` (legacy Supabase projects still signing symmetrically;
if the secret is unset, HS256 tokens are refused rather than trusted). The expected audience is
`authenticated`. Failures are `401` with `WWW-Authenticate: Bearer`.

Two deliberate details:

- **Misconfiguration is a `500`, not a `401`.** If `LIMON_SUPABASE_URL` is unset, the deployment
  is broken and the caller did nothing wrong; answering `401` would send a client into a
  pointless re-login loop.
- **Strict X.509 verification is disabled** (certificates are still fully verified). Python
  3.13 defaults to `VERIFY_X509_STRICT`, and TLS-inspecting middleboxes — corporate proxies,
  antivirus — present slightly non-conformant CA certs that strict mode rejects. Without this,
  every valid token would `401` on such machines.

`PyJWKClient` caches signing keys, so the blocking fetch happens on the first request after
startup and again on key rotation.

## Just-in-time provisioning

The first authenticated request from an identity creates its `users` row: a plain primary-key
fetch on the `sub`, then an insert if absent. There is no separate registration step, and the
row exists before any handler runs — which is what lets `events`/`tags`/`recordings` carry a
real foreign key to it with no ordering problem on the first write.

Profile fields (`email`, `display_name`, `provider`) are seeded **only at creation**. Later
token claims never overwrite them, so a user's own `PATCH /users/me` edits stick rather than
being reverted by the next login.

## Deleting an account

`DELETE /users/me` has to reach two stores, because our cascade only covers `public.*`:

1. Delete the Supabase `auth.users` identity through the Auth Admin API (service-role key).
2. Delete our `users` row, which cascades away the user's events, recordings, and tags.

**Remote first, and abort on failure.** If the Supabase call fails, nothing local is deleted and
the route answers `502`. The alternative ordering leaves an account half-removed — local data
gone, login still working — with no way to retry into a clean state. This way a failed delete is
fully retryable.

Because re-signing-in mints a **new** Supabase uid, the same Google account returning after a
delete starts genuinely empty rather than reattaching to anything. The one thing delete does not
remove is the user's audio in GCS (`open-questions.md`).

The admin client (`app/services/supabase_admin.py`) treats a `404` from Supabase as idempotent
success, no-ops entirely when `supabase_url` is unset (local dev and tests, where there is no
Supabase), and raises when the URL is set but the service-role key is missing — that
combination is a real misconfiguration, not a dev environment. The `502` body is deliberately
generic; the cause is logged server-side, or a misconfigured key and a Supabase outage would be
indistinguishable in the logs.

## What is *not* authenticated

The `/internal/*` worker routes are not user-gated — they are called by Cloud Tasks and Pub/Sub,
not the app. Their intended auth is OIDC, which is **not yet wired**; see `security.md`, where
this is the top item.
