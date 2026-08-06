<!-- specflow:start - managed by specflow; do not edit inside these markers (your edits block specflow upgrade). Add your own notes outside them. -->
# specflow

This repo uses **specflow**, a shared protocol for AI coding agents. Google Antigravity reads
`AGENTS.md` at the repo root natively (its highest-priority context file) — that file is the full
protocol. This rule just reinforces it.

**Read `AGENTS.md` before doing anything.** Design is written down in `spec/`, and the user
approves a design before it is persisted or built. The spec procedure lives in
`specflow/procedures/` — read it before acting:

- Before editing any `spec/**` file or persisting a design decision → `specflow/procedures/spec-edit.md`

Commit grammar: `spec:` for spec edits, `meta:` for tooling. Never force-push the shared branch.
<!-- specflow:end -->
