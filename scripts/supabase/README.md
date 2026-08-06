# Supabase setup

Two SQL files, run in this order on a fresh database:

| file | what it does | re-runnable |
|---|---|---|
| `create_tables.sql` | the four tables + indexes | no — creating an existing table errors |
| `setup.sql` | Realtime publication, replica identity, RLS policies | yes, idempotent |

Design rationale for all of this lives in `spec/realtime-reads.md` and
`spec/data-model.md`; the operational runbook is `spec/ops.md`.

## `create_tables.sql`

For standing up a **new** Postgres — a fresh Supabase project, or a move to another database.
The app's `create_all` on startup would also build these tables, but only from a running app
already wired to the target; this file needs nothing but a SQL editor.

It is **not a migration**: it creates tables that do not exist and will never add a column to an
existing one. There are no migrations in this project, so schema changes against a live database
are hand-applied `ALTER`s.

Generated from the SQLAlchemy models, which are the source of truth. When a model changes,
regenerate rather than hand-editing:

```bash
uv run python scripts/supabase/gen_create_tables.py > scripts/supabase/create_tables.sql
```

## `setup.sql`

Configures what `create_all` cannot: the Realtime publication and Row-Level Security. Idempotent,
so re-run it every time the tables are dropped and recreated.

1. Make sure **Realtime is enabled** for the project in the dashboard — the `supabase_realtime`
   publication must exist, and `setup.sql` errors loudly if it does not.
2. Apply it:
   ```bash
   psql "<direct/session connection string>" -f scripts/supabase/setup.sql
   ```
   or paste it into the Supabase **SQL editor**. Use a direct/session connection for DDL, never
   the transaction pooler.

What it configures:

- **`events` and `tags`**: `REPLICA IDENTITY FULL` (required — Realtime evaluates the RLS policy
  for UPDATEs against the WAL old-image, which under the default identity lacks the non-PK
  `user_id`, so the check fails closed and the message is silently dropped), both added to
  `supabase_realtime`, and an owner-only `SELECT` policy (`auth.uid()::text = user_id`).
- **`recordings` and `users`**: RLS enabled with **no policy**, which is deny-all for the
  anon/authenticated roles, so a leaked anon key cannot read them.

The backend writes through the `postgres` role, which bypasses RLS, so none of these policies
affect server-side writes or delete-account.

## What the client subscribes to

| what | value |
|---|---|
| table | `public.events` |
| subscribe key | `id` |
| transcript column | `description` |
| done signal | `description IS NOT NULL` (there is no `state` column) |

The client also reads its `tags` snapshot directly from `public.tags` — the API has no read
routes. Both tables' column names are therefore a read contract for installed clients; see
`spec/realtime-reads.md` before renaming anything.
