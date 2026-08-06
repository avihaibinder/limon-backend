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
-- Generated from the models on 2026-08-06 (postgresql dialect).

CREATE TABLE users (
	id VARCHAR(36) NOT NULL,
	provider VARCHAR(50) NOT NULL,
	email VARCHAR(320),
	display_name VARCHAR(200),
	demo_seeded_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id)
);
CREATE INDEX ix_users_email ON users (email);

CREATE TABLE recordings (
	id VARCHAR(36) NOT NULL,
	user_id VARCHAR(36) NOT NULL,
	storage_key VARCHAR(512) NOT NULL,
	content_type VARCHAR(100) NOT NULL,
	byte_size INTEGER,
	duration_sec INTEGER,
	state VARCHAR(20) NOT NULL,
	error VARCHAR(2000),
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE INDEX ix_recordings_state ON recordings (state);
CREATE INDEX ix_recordings_user_id ON recordings (user_id);

CREATE TABLE tags (
	id VARCHAR(36) NOT NULL,
	user_id VARCHAR(36) NOT NULL,
	name VARCHAR(100) NOT NULL,
	color VARCHAR(32),
	PRIMARY KEY (id),
	CONSTRAINT uq_tags_user_id_name UNIQUE (user_id, name),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE INDEX ix_tags_user_id ON tags (user_id);

CREATE TABLE events (
	id VARCHAR(36) NOT NULL,
	user_id VARCHAR(36) NOT NULL,
	type VARCHAR(20) NOT NULL,
	title VARCHAR(200),
	description VARCHAR(2000),
	occurred_at TIMESTAMP WITH TIME ZONE NOT NULL,
	tag_ids JSON NOT NULL,
	recording_id VARCHAR(36),
	duration_sec INTEGER,
	suggested_location VARCHAR(200),
	tag_reasoning VARCHAR(2000),
	client_event_id VARCHAR(36),
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
	UNIQUE (recording_id),
	FOREIGN KEY(recording_id) REFERENCES recordings (id),
	UNIQUE (client_event_id)
);
CREATE INDEX ix_events_occurred_at ON events (occurred_at);
CREATE INDEX ix_events_user_id ON events (user_id);
