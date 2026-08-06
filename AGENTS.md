<!-- specflow:start - managed by specflow; do not edit inside these markers (your edits block specflow upgrade). Add your own notes outside them. -->
# AGENTS.md — Shared Agent Protocol (specflow)

> **Managed by [specflow](https://github.com/MatanKoby/specflow).** Everything between the
> `specflow:start` / `specflow:end` markers is generated — `specflow upgrade` refreshes that
> region and **only** that region; text outside the markers is preserved. Add repo-specific
> notes *outside* the markers (project identity is better in `README.md` / `CLAUDE.md` / your
> agent's own config). Don't edit *inside* the markers — `upgrade` flags an edited region and
> leaves it untouched rather than clobbering it, so your change blocks future refreshes.

This is the single source of truth for how one or more AI coding agents collaborate on this
repo. **Every agent must read this before starting work.** It applies to *all* agents equally
(Claude Code, Cursor, Copilot, and any other) — agent-specific quirks live in that agent's own
config file, not here.

## The model in one paragraph

Work is **specced** before it is built: the design lives in `spec/`, organized one concern per
file, and agents keep it current — creating, updating, splitting, and archiving spec files as
the design evolves. This is a **spec-only** install: just the spec discipline, with no batch
queue or claim ledger. One procedure — **edit the spec** — carries it; its full steps live in
`specflow/procedures/`.

## Repo & branches

- There is one **shared working branch** (default `main` — substitute your team's if different).
  Agents commit directly to it, subject to the **commit/push levers** below. Always
  `git pull --ff-only` before you start work.
- No feature branches in the normal flow. (Teams that prefer PR-per-batch can layer that on;
  the default is direct-commit.)
- **Never** force-push the shared branch. Recover from a rejected push with `git pull --rebase`
  (never force), then re-push.

## Commit & push authority

Two independent levers in `specflow/config.json` (`config.commit` / `config.push`) govern what an
agent may do with code it has written. **Read them before any commit or push step** in the
procedures:

- **`commit: agent`** — the agent creates commits itself. **`commit: user`** — the agent does
  **not** commit; on reaching a sensible commit point it **alerts the user and supplies a short
  suggested commit message** (in the convention below), and the user makes the commit.
- **`push: agent`** — the agent pushes after committing. **`push: user`** — the agent commits but
  **never pushes**; the user pushes on their own terms. (Only meaningful when `commit: agent`.)

Where the procedures say "commit … and push," honor these levers: substitute an alert + suggested
message when `commit: user`, and stop before pushing when `push: user`. The default is
`agent` / `agent` — the agent commits and pushes.

## File ownership

| File / path | Owner | Notes |
|---|---|---|
| `spec/**` | user | The design. Agents propose `spec:` edits via the `spec-edit` procedure; don't freelance. |
| `AGENTS.md`, `specflow/**` | specflow | Generated mechanism. Overwritten on `specflow upgrade`; don't hand-edit. |
| source code, assets | shared | Commit with the grammar below. |

## The procedures

Detailed steps live in `specflow/procedures/`. **Read the relevant file before acting** —
don't reconstruct it from memory.

- **`specflow/procedures/spec-edit.md`** — before editing any `spec/**` file or persisting a
  design decision: concern-matching, cross-reference-don't-restate, archive rule. **Run before any spec change.**
  Its *Research notes* section also covers the optional pre-design step: exploratory research
  (prior-art scans, option/tradeoff analysis) has a gate-free home in `spec/research/` — dated
  snapshots written on the go — whose conclusions graduate up into the spec (e.g.
  `open-questions.md` / `roadmap.md`).

> Claude Code users: these procedures are also installed as auto-triggering skills.

## Commit message convention

| Prefix | When to use |
|---|---|
| `spec: <change>` | Edits to any `spec/**` file |
| `meta: <other>` | Tooling / structural changes |

`git log --oneline` is the change log — there is no separate changelog file.

## Editing rules

- Treat `spec/**` as the design — propose edits through the `spec-edit` procedure; don't freelance.
<!-- specflow:end -->
