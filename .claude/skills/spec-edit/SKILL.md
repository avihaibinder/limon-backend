---
name: spec-edit
description: Use before editing any file under `spec/**`, or when persisting a design decision the user just made. Covers concern-matching, cross-reference-don't-restate, the archive rule, the 600-line size cap.
---

# Edit the spec (or persist a design decision)

Follow **`specflow/procedures/spec-edit.md`** in this repo — that file is the authoritative,
up-to-date procedure (kept in sync by `specflow upgrade`; this skill is a thin trigger).

In short: open the relevant `spec/` index first and write to the **single** file whose concern
matches (flag cross-file changes instead of duplicating) → cross-reference by path, don't restate
→ move stale content to `archive.md`. Past **600 lines** (`archive.md` and `research/` exempt), stop
and ask the user to split or keep; never decide it yourself, and never ask them for a number.
When persisting a design decision, write it to `spec/` in a `spec:` commit — the user must have
approved it first.
If the decision surfaced mid-execution, surface it to the user before persisting.
