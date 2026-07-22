# Supabase setup (Realtime + RLS)

`setup.sql` configures the two things SQLAlchemy's `create_all` cannot: the
Supabase **Realtime** publication and **Row-Level Security**. It is idempotent,
so re-run it every time the tables are dropped and recreated
(`spec-local/plan/PLAN.md` step 7). Domain doc: `spec-local/plan/09-realtime-rls.md`.

## When to run

1. Start the app once against the Supabase DB so `create_all` builds the tables.
2. In the Supabase dashboard, make sure **Realtime is enabled** for the project
   (the `supabase_realtime` publication must exist; `setup.sql` errors loudly if
   it does not).
3. Apply the SQL:
   ```bash
   psql "<direct/session connection string>" -f scripts/supabase/setup.sql
   ```
   or paste it into the Supabase **SQL editor**. Use a direct/session connection
   for DDL, not the transaction pooler.

## What it does

- `events`: `REPLICA IDENTITY FULL` (so Realtime can evaluate the RLS policy on
  UPDATE, which keys off the non-PK `user_id`) + added to `supabase_realtime` +
  an owner-only `SELECT` policy (`auth.uid()::text = user_id`).
- `recordings` / `tags` / `users`: RLS enabled with **no policy** (deny-all for
  the anon/authenticated roles) so a leaked anon key cannot read them. The BE
  writes through the `postgres` role, which bypasses RLS, so nothing server-side
  is affected.

## FE handoff (the four Realtime config values)

The FE's `REALTIME` config (`client/src/features/recording/config.ts` in the FE
repo) must use these final values, replacing its `records / id / state / text`
placeholders:

| what | value |
|---|---|
| table | `public.events` |
| subscribe key (id column) | `id` |
| transcript column | `description` |
| done-signal | `description IS NOT NULL` (there is no `state` column) |
