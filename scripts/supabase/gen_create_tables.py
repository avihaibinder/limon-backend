"""Regenerate scripts/supabase/create_tables.sql from the SQLAlchemy models.

The models are the source of truth for the schema; that file is a convenience
artifact for standing up a fresh Postgres database before an app is wired to it
(see its header). Whenever a model changes, regenerate rather than hand-editing:

    uv run python scripts/supabase/gen_create_tables.py > scripts/supabase/create_tables.sql

Tables come out in FK-dependency order (``sorted_tables``), each followed by its
indexes. Output is compiled for the postgresql dialect, so it carries Postgres
types rather than the SQLite ones a default compile would emit.
"""

from datetime import UTC, datetime

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

import app.models  # noqa: F401  -- imported for the side effect of registering every model
from app.db.base import Base

_HEADER = """\
-- LimON tables, for standing up a fresh Postgres database.
--
-- Run this FIRST on a new/empty database, then scripts/supabase/setup.sql (which
-- adds RLS, replica identity, and the Realtime publication). Order within this
-- file matters: users -> recordings -> tags -> events, by FK dependency.
--
-- WHY THIS FILE EXISTS. The app calls Base.metadata.create_all on startup, which
-- would also build these tables -- but that requires a running app already wired
-- to the new database. When moving to another Postgres, you want the schema in
-- place before anything connects. This file is that, runnable in a SQL editor
-- with nothing else set up.
--
-- NOT A MIGRATION. It only creates tables that do not exist; it will not add a
-- column to an existing table. There are no migrations in this project (see
-- spec/data-model.md), so schema changes against a live database are hand-applied
-- ALTERs. Running this against a database that already has these tables errors,
-- which is the intended safety.
--
-- KEEPING IT CURRENT. The SQLAlchemy models are the source of truth. Regenerate
-- rather than hand-editing:
--     uv run python scripts/supabase/gen_create_tables.py > scripts/supabase/create_tables.sql
--
-- Generated from the models on {date} (postgresql dialect).
"""


def _statement(compiled: object) -> str:
    """One DDL statement, terminated, with SQLAlchemy's trailing spaces removed.

    The compiler emits a trailing space after each column's comma, which would
    otherwise make every regeneration a whitespace-only diff against the
    committed file (and trip whitespace-stripping editors).
    """
    lines = [line.rstrip() for line in str(compiled).strip().splitlines()]
    return "\n".join(lines) + ";"


def render() -> str:
    dialect = postgresql.dialect()
    parts = [_HEADER.format(date=datetime.now(UTC).date().isoformat())]

    for table in Base.metadata.sorted_tables:
        parts.append(_statement(CreateTable(table).compile(dialect=dialect)))
        # Sort by name so regeneration is stable rather than set-ordered.
        for index in sorted(table.indexes, key=lambda i: i.name or ""):
            parts.append(_statement(CreateIndex(index).compile(dialect=dialect)))
        parts.append("")

    return "\n".join(parts)


if __name__ == "__main__":
    print(render(), end="")
