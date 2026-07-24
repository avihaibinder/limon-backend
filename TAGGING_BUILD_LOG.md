# Auto-tagging build log

## What it does

- After a **text event** is created or edited, or an **audio event's
  transcription completes**, if the event has no user-selected tags and has
  some title/description text, the backend asks Nebius (Qwen3-32B) to
  suggest tags for it.
- The model may only pick from the user's **existing** tags — it can never
  create a new one (enforced server-side by filtering the response, not just
  by prompting).
- The model also reports a sentiment (used only to help it reason — logged,
  not stored) and an optional explicit location mention, stored for later use
  but not yet returned to the client.
- Runs the same way transcription does: enqueued as a Cloud Task, processed
  by an internal worker endpoint, idempotent under retries.

## Files added

- `app/services/tagger.py` — Nebius chat-completions HTTP client (Hebrew
  system prompt, strict `json_schema` response format, exception taxonomy
  mirroring `transcriber.py`).
- `app/services/tagging.py` — worker orchestration (load event → call tagger
  → write result), mirrors `transcription.py`.
- `app/schemas/tagging.py` — `TagTask` (Cloud Tasks call body for
  `/internal/tag`).
- `scripts/test_tagging_manual.py` — throwaway manual script, not wired into
  the app or pytest (`pyproject.toml`'s `testpaths = ["tests"]` already
  excludes `scripts/`). Calls `tagger.suggest_tags()` directly against the
  real Nebius endpoint with a sample Hebrew entry and fake tags; prints the
  raw result. Makes one real, billed API call — run it yourself, it isn't
  run automatically.
- `TAGGING_BUILD_LOG.md` — this file.

## Files changed

- `app/core/config.py` — added `tagger_api_key`, `tagger_model`,
  `tagger_base_url`, `tagger_timeout_s` settings (`LIMON_TAGGER_*` env vars).
- `app/models/event.py` — added nullable `suggested_location` (varchar 200)
  and `tag_reasoning` (varchar 2000) columns.
- `app/services/task_queue.py` — added `enqueue_tagging(event_id)`, refactored
  the shared Cloud Tasks creation logic (`_create_task`) so both transcription
  and tagging enqueue through it.
- `app/routers/internal.py` — added `POST /internal/tag` (same shape as
  `/internal/transcribe`: retry → 503, everything else → 200).
- `app/services/transcription.py` — after a successful transcript write, if
  the event still has no tags, enqueues tagging. Enqueue failures are logged
  and swallowed — they must not turn a successful transcription into a retry.
- `app/services/events.py` — added `_maybe_enqueue_tagging()`, called after
  `create_event()` (text events only) and after `update_event()` when
  `title`/`description` was part of the patch. Enqueue failures are logged
  and swallowed here too, since this runs inline in the user's request and
  Cloud Tasks is normally unconfigured in local dev.
- `CLAUDE.md` — documented the feature, config, and the manual `ALTER TABLE`
  needed on live databases.

## Follow-up: non-Hebrew characters leaking into `reasoning`

Live testing found the model sometimes mixes Chinese/Cyrillic characters
mid-sentence into the `reasoning` field, even with `enable_thinking: false`
(observed examples: `מ夹括 פעילות גופנית`, `לчувתי די לחוץ`). Two changes in
`app/services/tagger.py`, `reasoning` only:

- **Strengthened the system prompt** with an explicit, separate instruction:
  Hebrew/Latin/digits/basic punctuation only, never Chinese/Cyrillic/Arabic
  anywhere in the response including `reasoning`, and to rephrase in Hebrew
  rather than reach for a foreign character.
- **Added `_sanitize_reasoning()`** as a safety net, applied to `reasoning`
  inside `_parse()` after schema validation. It's an *allowlist*, not a
  per-script denylist: anything outside the Hebrew block (`֐`–`׿`),
  basic Latin, digits, whitespace, and common punctuation is replaced with a
  single space (not deleted outright, so removing a stray character doesn't
  jam the two surrounding Hebrew words together), then repeated whitespace is
  collapsed. `tag_ids`, `sentiment`, and `suggested_location` are untouched,
  as asked.

**Verified:**
- Offline, against the two exact garbled strings above — both scripts
  stripped cleanly, clean Hebrew passed through unchanged (spot-checked via
  `_sanitize_reasoning()` directly, no network call).
- Live, via `scripts/test_tagging_manual.py` — no visible leakage in that
  run's `reasoning` output.

**Known limitation — this is a mitigation, not a guaranteed fix:**
- The prompt change can't force the model's behavior; it can still leak
  characters on some runs. The sanitizer is the actual backstop, and as an
  allowlist it should catch *any* disallowed script on `reasoning`, not just
  Chinese/Cyrillic/Arabic — but it only fixes the character-set problem, not
  semantic garbling (e.g. a wrong-but-still-Hebrew word).
- Per your instruction, only `reasoning` is sanitized. `suggested_location`
  is also free text extracted from the entry and could theoretically suffer
  the same leakage — it currently has no safety net. Say the word if you want
  it covered too.

## Follow-up: `reasoning` truncated mid-sentence

Cause: no `max_tokens` was set on the request at all — it relied entirely on
Nebius's own server-side default, which was too low for `reasoning` plus the
rest of the structured response to complete.

Fix: added `_MAX_TOKENS = 1000` in `app/services/tagger.py`, wired into the
request body as `"max_tokens": 1000`.

**Verified live** via `scripts/test_tagging_manual.py`: `reasoning` now ends
on a complete sentence instead of stopping mid-word. No forbidden-script
(Chinese/Cyrillic/Arabic) characters observed in this run either.

**Observation, not acted on:** the same run showed a couple of spots where a
Latin/English word fragment glues directly onto a Hebrew word with no space
(e.g. `קulinריים`, `כneutral`) — a different, lower-severity issue than the
forbidden-script leak, since Latin letters are explicitly allowed by
`_sanitize_reasoning()`'s character range. Not fixed; flagging for awareness.

## Manual steps you still need to do

- **Live/Supabase databases** created before this change need the new
  columns added by hand (local SQLite gets them automatically on next
  startup):
  ```sql
  ALTER TABLE events ADD COLUMN suggested_location VARCHAR(200);
  ALTER TABLE events ADD COLUMN tag_reasoning VARCHAR(2000);
  ```
- **Local end-to-end testing needs Cloud Tasks configured** (`LIMON_TASKS_*`
  + `LIMON_TASKS_WORKER_URL` pointed at your own running instance) — same as
  transcription today, there's no dev shim. Without it, `POST /events` and
  `PATCH /events/{id}` will still work fine, just log
  `STEP=tagging_enqueue_failed` and skip tagging.
- **No tests yet** — you said you'd test manually and add tests in a
  follow-up. When you do, mirror `tests/test_transcriber.py` (for
  `tagger.py`), `tests/test_worker.py` (for `/internal/tag`), and the
  `enqueue_transcription` mocking pattern in `tests/test_trigger.py` /
  `tests/test_events.py` (for `enqueue_tagging`).

## Verification run (this environment)

`uv` wasn't installed anywhere on this machine; installed via
`pip install --user uv`, then `uv sync --extra dev`.

- `uv run ruff check .` — found and fixed one real line-length violation in
  `tagger.py`. Clean now.
- `uv run ruff format --check .` — clean.
- `uv run pytest` — **95 passed, 3 failed.** The 3 failures are **pre-existing
  and unrelated to tagging**: `test_supabase_admin.py::test_no_op_when_supabase_url_unset`,
  `test_tags.py::test_deleting_account_cascades_to_tags`,
  `test_users.py::test_delete_me_then_next_request_reprovisions_same_id`.
  Cause: local `.env` has `LIMON_SUPABASE_URL` set (to the placeholder value)
  but no `LIMON_SUPABASE_SERVICE_ROLE_KEY`, so tests that assume Supabase is
  unconfigured instead hit the real (unconfigured) delete-account code path.
  Nothing this feature touched — worth a separate fix if it bothers you.
- The Qwen3 `chat_template_kwargs: {enable_thinking: false}` request shape
  was confirmed against the live endpoint (via `scripts/test_tagging_manual.py`)
  — no request-shape errors, structured output parsed successfully.
