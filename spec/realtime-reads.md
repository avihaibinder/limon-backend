# The read path: Supabase owns reads

**The API owns writes; Supabase owns reads.** Every mutation goes through this service, which
validates it, stamps the owner from the JWT, mints upload URLs, and enforces idempotency. No
app data is read back through it: the client takes its launch snapshot with a direct
PostgREST `.select()` and receives every subsequent change over Supabase Realtime.

This service therefore exposes **no list routes at all** (`api.md`). `GET /events/{id}` survives
as a single-row fallback, off the client's normal path.

## Why reads bypass the API

Realtime already had to exist: a transcript arrives seconds to minutes after the event is
created, with no request in flight to answer, so the result has to be pushed. Once the client
holds an RLS-scoped subscription to `public.events`, a second read path through the API buys
nothing — it duplicates the shape, doubles the places a field can be forgotten, and adds a
second thing to keep in sync.

The rule extends to writes only in the negative sense: a read endpoint is justified only by a
genuinely complex query a direct select cannot express. None exists today.

The cost is stated plainly under *The frozen contract* below, and it is real.

## What Supabase must be configured with

`scripts/supabase/setup.sql` is the source of truth, and it is idempotent — re-run it every time
the tables are dropped and recreated. SQLAlchemy manages neither RLS nor publications, so
nothing in `app/` can substitute for it.

It does three things, for `events` and `tags` both:

### 1. Owner-only `SELECT` policies

```sql
create policy "owner reads own events"
  on public.events for select
  using (auth.uid()::text = user_id);
```

RLS is the **sole read gate**. The anon key ships inside the app bundle and is trivially
extractable, so anyone can reach PostgREST; the policy is the only thing standing between them
and other users' rows. The `::text` cast is required (`auth.md`).

`recordings` and `users` have RLS **enabled with no policy**, which is deny-all. A table with
RLS *not enabled* needs no policy to leak, so "enabled" is the invariant that matters, not
"has a policy".

The backend writes through the session-pooler `postgres` role, the table owner, which bypasses
RLS entirely. None of these policies affect the worker's writes or delete-account.

### 2. `REPLICA IDENTITY FULL` on both tables

This is load-bearing, not a default worth accepting. **Realtime re-evaluates the RLS policy for
every UPDATE against the WAL old-image.** Under the default replica identity that old-image
holds only the primary key, so `user_id` is absent, the policy cannot be evaluated, and it
**fails closed** — the message is silently dropped. On `events` that means the owner never
receives their transcript; on `tags`, renames and recolors never propagate. Both are silent:
no error anywhere, the update simply never arrives.

Verified live on `events` during rollout, and applied to `tags` for the same reason.

### 3. Both tables in the `supabase_realtime` publication

Guarded so re-runs stay clean, and it raises a clear error if Realtime was never enabled for the
project rather than failing obscurely.

## The delete-broadcast trade-off

Accepted eyes-open, with the frontend, and it remains the least comfortable part of this design.

**Realtime applies no RLS to DELETE messages.** Combined with `REPLICA IDENTITY FULL`, the
DELETE old-record is the *whole row*, so every subscriber receives every deleted row's full
contents, table-wide, across all users: tag names and colors, and event titles and bodies.

It is not a bug to be fixed in passing — it is the price of the UPDATE delivery above, since the
same setting causes both. Two candidate mitigations are recorded in `open-questions.md`; the
real fix is migrating to broadcast authorization with per-user private channels.

## What the client subscribes to

| what | value |
|---|---|
| table | `public.events` |
| subscribe key | `id` |
| transcript column | `description` |
| done signal | `description IS NOT NULL` |

One persistent user-scoped subscription per table, attached at auth — **not** a channel per
event. RLS scopes it per row, so a single channel is already correct.

**Subscribe first, then snapshot, then dedupe by `id`.** Realtime delivers *changes only* and
never replays existing rows, so the snapshot is what seeds the timeline and the subscription is
what keeps it live. Reversing the order drops anything that changes in the gap.

Deleting a tag emits the tag's DELETE and the detached events' UPDATEs from one transaction, so
a subscriber may see them in either order. Both orderings are idempotent on the client.

## The frozen contract

Reads arrive as **raw snake_case columns** — `tag_ids`, `occurred_at`, `client_event_id`,
`duration_sec` — because they come from Postgres, not from a Pydantic response model. The
client applies one generic snake-to-camel transform on its read path rather than per-field
decoders. This is also why camelCasing the API's own responses is not a goal worth extending:
it would only change responses the client no longer reads.

The consequence is the part to take seriously. **The client's read path is coupled to the
`events` and `tags` column names with no API layer in between.** For a mobile client that is
sharper than usual: users do not auto-update, so once there are real installs, those column
names and the `public` schema exposure are effectively a **frozen contract**. The `/api/v1`
prefix versions the write surface; it does not version these reads.

Renaming a column on either table is a breaking change to shipped clients. Flag it to the
frontend before doing it, not after.

Today the risk is bounded by distribution: the app runs through Expo Go, which loads the current
bundle on reload, so client and server still move together. That stops being true the moment
there are store builds.
