# Automatic tagging

When an event ends up with text and **no user-selected tags**, a worker asks a language model
which of the user's *existing* tags apply. The user's own choices are never overridden: tagging
only ever fills a gap.

## When it fires

Enqueued from three places, all with the same condition — the event has some `title` or
`description` text and an empty `tag_ids`:

- a text event is created;
- an event's `title` or `description` is edited;
- an audio event's transcription completes (`transcription.md`).

The first two run **inline in the user's request**, unlike the Pub/Sub-driven transcription
chain. That makes the enqueue best-effort by necessity: a failure — including Cloud Tasks being
unconfigured, which is the normal case in local dev — is logged and swallowed rather than
failing the create or update the user asked for.

## The worker

`POST /internal/tag` takes `{"eventId": …}`, and no-ops on a missing event, an event that
already carries tags, or one with no text.

The already-tagged check is a **plain read, not an atomic claim** like the recording state
machine. This is a conscious asymmetry: the worst case here is one redundant model call, not a
double-write or a lost transcript, so the cost of a claim is not worth paying. Duplicate
delivery is therefore possible and harmless.

On success it writes the validated `tag_ids`. The existing `suggested_location` and
`tag_reasoning` columns are not populated by the current tag-only integration.

## The existing-tags-only rule

The model is given the user's full tag list (id and name) and must pick from it. It may return
an empty list. It may never invent a tag.

**That rule is enforced twice, not merely requested in the prompt.** The strict response schema
dynamically enumerates the ids that were actually offered, and backend validation rejects the
whole response if any out-of-list id nevertheless appears. A prompt instruction alone would be
only a suggestion; these checks are what make the rule true.

The worker deliberately fetches the full tag list with no pagination cap, unlike the API's
paged reads — tagging needs every candidate, not a page of them.

## Talking to the model

GroqCloud's OpenAI-compatible chat-completions API, default model `qwen/qwen3.8-27b`, receives
the Hebrew event text and the owning user's existing tag ids and names. It uses strict
`json_schema` Structured Outputs with a single visible field: `tag_ids`.

Several details were learned the hard way and should not be undone casually:

- **Reasoning effort is high and hidden.** `reasoning_effort: high` asks the model to evaluate its
  choices, while `reasoning_format: hidden` keeps reasoning out of the visible response.
- **Strict Structured Outputs are enabled.** Every property is required and additional properties
  are forbidden. The only property is the selected tag-id list.
- **The schema is owner-specific.** Its enum is built from the tag rows loaded for
  `event.user_id`; another user's id is not a legal model output.
- **Backend validation remains authoritative.** Malformed JSON, extra fields, and invented ids are
  rejected before the event is committed, even though strict decoding should already prevent them.
- **`max_completion_tokens` is explicit.** The request leaves enough room for hidden reasoning and
  the small final JSON response.

Failure handling mirrors transcription: busy or rate-limited and endpoint-down are **soft**
(`503` + `Retry-After`, retry within budget); an unparseable or schema-invalid response is
**hard** — retrying will not fix a model that answered wrongly.

## Logging

Never entry text, tag names, model output, or reasoning. The `STEP=tagged` marker carries the
event id and tag count only.

## Configuration

`LIMON_TAGGER_API_KEY` (unset means tagging is treated as unavailable rather than crashing),
`LIMON_TAGGER_MODEL` (default `qwen/qwen3.8-27b`), `LIMON_TAGGER_BASE_URL` (default
`https://api.groq.com/openai/v1`), and `LIMON_TAGGER_TIMEOUT_S`.
