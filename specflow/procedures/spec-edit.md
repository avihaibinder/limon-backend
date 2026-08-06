<!-- specflow:start - managed by specflow; do not edit inside these markers (your edits block specflow upgrade). Add your own notes outside them. -->
# Procedure: edit the spec (or persist a design decision)

Run this before editing any file under `spec/**`, or when persisting a design decision the
user just made. `AGENTS.md` carries only the pointer to this file.

## Decide where to write — concern-matching

The spec lives in `spec/`, organized into concern-focused files (and sub-folders as it grows).
Open the relevant index **first** — don't read the whole spec:

- Start at `spec/README.md` for the file map.
- When a sub-folder has its own `README.md`, open that before reading individual files in it.

Pick the **single** file whose concern matches the change. If the change naturally crosses
multiple files, that's a signal the concern might be miscarved — **flag it to the user before
duplicating content.** Don't just write to both files.

For example, roadmap, milestones, and "what's next" are one concern: they live in
`spec/roadmap.md` (create it if absent), never in the project README and never scattered across
other files.

## Cross-reference, don't restate

When file A needs a concept that lives in file B, link by **file path** — e.g.
`see schema.md → Tables` or `see signals/zone.md`. Don't restate the concept; restating creates
a second source of truth that will drift.

## Move stale content to `archive.md`

The spec describes the **current intended design**, not the history. When a section stops
reflecting live code — an abandoned approach, a removed table, a deprecated flow — move it to
`spec/archive.md` rather than leaving it inline. `archive.md` is the institutional memory;
everything else is current.

## Research notes — the pre-design exception

Exploratory research (prior-art scans, option/tradeoff analysis) comes *before* design and is
**not yet design**, so it lives outside the rules above — in an optional `spec/research/`
sub-folder (dated snapshots, `YYYY-MM-topic.md`):

- **Gate-free.** A note asserts no design, so write and update it freely. Only *graduating a
  conclusion* into the spec (e.g. `open-questions.md` / `roadmap.md`) needs the user's sign-off.
- **Write as you go.** Checkpoint findings during the session, not only at the end — the
  transcript isn't durable (the reason this procedure exists), so a crashed session loses nothing.
- **Dated snapshots, exempt from the archive rule.** A note records what was true when written;
  don't rewrite it to stay current. Conclusions graduate upward; the note stays as the evidence trail.

## Size cap: 600 lines, and it stops you

The **primary** reason to split a file is that it has started holding **two concerns**. That is the
concern-matching rule above, and it fires long before any line count. The cap is the backstop for
the case where a second concern crept in and nobody noticed.

**600 lines is a hard cap, not a nudge.** Before making an edit that would push a `spec/` file past
its current limit, **stop and ask the user**. Don't split, and don't let it grow, on your own
authority. The ask has four parts:

1. List the file's **section headlines**, so the user can see where a split would fall.
2. State whether you believe the file still holds a **single concern**, and if you think it
   doesn't, name the second one.
3. Give the cost, in these words: *"The bigger a spec file is, the more I read when I need even
   just a small chunk from it, so it's best the file is small in advance. But, you're the boss."*
4. Ask **split or keep**. **Never ask the user to pick a number**: the threshold isn't theirs to
   set, and "how big should this file be?" is not a question they should have to answer.

**If they choose split**, carve the second concern into its own file as part of the same edit, and
update the folder's `README.md` index in the same commit. A per-folder `README.md` is the index
pattern; when a sub-concern grows enough to warrant a file, add it and point at it there.

**If they choose keep**, record the waiver as the **first line of the file**, above the `#` heading:

```
<!-- specflow:size-ok - user approved this file over 600 lines on 2026-01-31 14:05 UTC; next check at 800. -->
```

Timestamp in UTC, and set `next check` to the limit you just asked about **plus 200**. That number
is the file's new limit, so the next ask lands at 800, then 1000, and so on. A waiver silences one
threshold, never the rule. On a later crossing, rewrite the same line rather than stacking a second.

**Exempt: `archive.md` and anything under `research/`.** Both grow monotonically by design: the
archive is institutional memory, and research notes are dated snapshots you're told above not to
rewrite. Neither has a concern to split off, so the ask would have no good answer.

## Persisting a design decision the user just made

A decision belongs in the **spec** (durable design), not just the transcript — if you don't
write it down, a future agent will re-litigate it or silently contradict it.

**Decision made *with* the user (working session):**

1. Update the relevant `spec/` file(s) to reflect the new design, with a `spec:` commit.
2. Then proceed to implementation.

**Decision encountered mid-execution (no user input yet):**

Do **not** quietly make and persist the decision. Instead:

1. Surface it to the user — describe the choice and the tradeoffs.
2. Wait for their call.
3. Once decided, follow the working-session flow above.

Scope: this applies to **design/spec** — architecture, data model, public behavior. Day-to-day implementation forks (library choice, internal file naming, refactor shape)
stay agent discretion.

## Commit convention

| Prefix | When |
|---|---|
| `spec: <change>` | Edits to any `spec/**` file |
| `meta: <change>` | Tooling / structural changes |

<!-- specflow:end -->
