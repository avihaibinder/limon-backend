-- LimON Supabase setup: Realtime publication + Row-Level Security.
--
-- Run this against the Supabase Postgres database AFTER the app has created the
-- tables (SQLAlchemy create_all on startup). SQLAlchemy does not manage RLS or
-- logical-replication publications, so this file is the source of truth for both.
-- It is idempotent: re-run it every time the tables are dropped and recreated
-- (see spec-local/plan/PLAN.md step 7). See spec-local/plan/09-realtime-rls.md.
--
-- Apply it with either:
--   psql "$LIMON_DATABASE_URL_DIRECT" -f scripts/supabase/setup.sql
-- or by pasting it into the Supabase SQL editor.
-- (Use a direct/session connection, not the transaction pooler, for DDL.)

begin;

-- ---------------------------------------------------------------------------
-- Realtime: emit postgres_changes for `events` (transcripts, edits) and `tags`
-- (live cross-device sync of tag create/rename/recolor/delete, see
-- fe-be-comms/FE_CONTRACT.tags-realtime.md).
-- ---------------------------------------------------------------------------

-- RLS on Realtime gates each UPDATE by user_id, which is NOT the primary key.
-- With the default replica identity only the PK is written to the WAL old-image,
-- so Realtime cannot evaluate `auth.uid()::text = user_id` on an UPDATE and fails
-- closed, dropping the message: the owner would never receive their transcript.
-- REPLICA IDENTITY FULL puts the whole row in the WAL so the policy can be
-- evaluated. Cost is larger WAL writes on `events`; acceptable for this workload.
alter table public.events replica identity full;

-- Same requirement for `tags`: without FULL, the owner-only policy cannot be
-- evaluated on the UPDATE old-image and renames/recolors are silently dropped
-- (verified live on `events`; see spec-local/plan/09-realtime-rls.md).
-- Eyes-open trade-off: Realtime does NOT apply RLS to DELETE messages, and with
-- FULL the DELETE old-record is the whole row, so every tags subscriber receives
-- deleted tags' id/user_id/name/color table-wide across users. Accepted
-- (FE_CONTRACT.tags-realtime.md Confirm 3); `events` already carries the same
-- exposure for deleted rows.
alter table public.tags replica identity full;

-- Add both tables to Supabase's realtime publication (guarded: ADD errors if
-- the table is already a member, and we want a clean re-run).
do $$
declare
  t text;
begin
  if not exists (select 1 from pg_publication where pubname = 'supabase_realtime') then
    raise exception
      'publication "supabase_realtime" does not exist. Enable Realtime for this '
      'project (Database -> Replication) before running this script.';
  end if;

  foreach t in array array['events', 'tags'] loop
    if not exists (
      select 1 from pg_publication_tables
      where pubname = 'supabase_realtime'
        and schemaname = 'public'
        and tablename = t
    ) then
      execute format('alter publication supabase_realtime add table public.%I', t);
    end if;
  end loop;
end $$;

-- ---------------------------------------------------------------------------
-- RLS: owner-only reads.
-- ---------------------------------------------------------------------------
-- The BE writes through the session-pooler `postgres` role (the table owner),
-- which bypasses RLS, so none of the policies below affect the worker's writes
-- or delete-account. They gate only the FE's direct Supabase access (Realtime +
-- catch-up select via the anon/authenticated roles).

-- events: the FE subscribes to and reads its own rows. The ::text cast is
-- required (user_id is text/String(36); auth.uid() is uuid) or the comparison
-- errors and the policy denies everything.
alter table public.events enable row level security;
drop policy if exists "owner reads own events" on public.events;
create policy "owner reads own events"
  on public.events for select
  using (auth.uid()::text = user_id);

-- tags: same owner-only read gate, for the tags Realtime subscription
-- (FE_CONTRACT.tags-realtime.md). This consciously supersedes the earlier
-- deny-all stance (FE_DECISIONS.v5): the policy also admits direct PostgREST
-- selects on `tags` by authenticated users; the FE keeps reading snapshots via
-- GET /tags, but the surface now exists.
alter table public.tags enable row level security;
drop policy if exists "owner reads own tags" on public.tags;
create policy "owner reads own tags"
  on public.tags for select
  using (auth.uid()::text = user_id);

-- The FE never touches these tables directly (recordings is worker-internal;
-- users go through the BE). Enable RLS with NO policy = deny-all for the
-- anon/authenticated roles, so a leaked anon key cannot read them via PostgREST
-- or Realtime. The BE (postgres role) still has full access.
alter table public.recordings enable row level security;
alter table public.users      enable row level security;

commit;
