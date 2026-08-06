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

On success it writes `tag_ids` plus two columns that are **not yet exposed to any client**:
`suggested_location` and `tag_reasoning`.

## The existing-tags-only rule

The model is given the user's full tag list (id and name) and must pick from it. It may return
an empty list. It may never invent a tag.

**That rule is enforced server-side, not merely requested in the prompt.** The parsed
`tag_ids` are filtered down to the ids that were actually offered. A prompt instruction alone
would be a suggestion; the filter is what makes it true.

The worker deliberately fetches the full tag list with no pagination cap, unlike the API's
paged reads — tagging needs every candidate, not a page of them.

## Talking to the model

Nebius Token Factory's OpenAI-compatible chat completions API, default model `Qwen/Qwen3-32B`,
with a Hebrew system prompt and a strict `json_schema` response format derived from the result
model itself, so the schema and the parser cannot drift apart.

Several details were learned the hard way and should not be undone casually:

- **Native Qwen3 "thinking" is disabled** (`chat_template_kwargs.enable_thinking: false`).
  Combining it with strict `json_schema` output is unreliable across OpenAI-compatible
  providers. The schema carries its own `reasoning` field instead, and the prompt asks the model
  to reason there *first* and then fill the remaining fields from that conclusion.
- **No schema field has a default.** Strict mode requires every property to be `required`; a
  pydantic default would silently drop the field from `required`. A nullable field like
  `suggested_location` is still required, just typed to allow `null`.
- **`max_tokens` is set explicitly (1000).** The endpoint's own default was low enough to
  truncate `reasoning` mid-sentence, with no cap configured at all.
- **Reasoning text is scrubbed** to Hebrew, Latin, digits, and common punctuation. Even with
  thinking disabled and an explicit prompt instruction, the model sometimes mixes stray Chinese,
  Cyrillic, or Arabic characters into free text. Disallowed runs collapse to a single space.
- **Location must be explicit.** The model is told to return a location only when one is
  actually named in the text, and never to guess.

Failure handling mirrors transcription: busy or rate-limited and endpoint-down are **soft**
(`503` + `Retry-After`, retry within budget); an unparseable or schema-invalid response is
**hard** — retrying will not fix a model that answered wrongly.

## Sentiment is computed but not stored

The model returns a sentiment (`positive` / `negative` / `neutral`) and it is **logged only**,
in the `STEP=tagged` marker. It is not persisted anywhere, because nothing in the product
consumes it yet. When something does, it needs a column — the current behavior is not a
half-built feature but a deliberate stop.

## Logging

Never entry text, never the model's reasoning. The `STEP=tagged` marker carries the event id,
sentiment, and tag count only.

## Configuration

`LIMON_TAGGER_API_KEY` (unset means tagging is treated as unavailable rather than crashing),
`LIMON_TAGGER_MODEL`, `LIMON_TAGGER_BASE_URL`, `LIMON_TAGGER_TIMEOUT_S`.
