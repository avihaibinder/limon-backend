---
name: branch-check
description: Enforce that git branches are tied to an issue, named issue-<issue#>-<short-descr>. Use before creating a new branch, or when asked to check/validate the current branch name.
---

# Branch Check

LimON backend convention: every branch must relate to a tracked issue and be
named:

```
issue-<issue#>-<short-descr>
```

- `<issue#>` — the numeric issue id (no leading zeros, no `#`), e.g. `1`, `42`.
- `<short-descr>` — lowercase, hyphen-separated, short (a few words max).

Examples: `issue-1-claude-init`, `issue-42-pdf-export`.

## Instructions

### When asked to create a new branch

1. Require the caller to supply (or ask the user for) an issue number and a
   short kebab-case description. Never invent an issue number — if none is
   given, ask the user which issue this work relates to before creating
   anything.
2. Build the name as `issue-<issue#>-<short-descr>` (slugify the description:
   lowercase, spaces/underscores → hyphens, strip anything not
   `[a-z0-9-]`).
3. Create it from an up-to-date base branch:
   ```bash
   git checkout -b issue-<issue#>-<short-descr>
   ```
4. Confirm the created name back to the user.

### When asked to check/validate a branch name

1. Get the branch name to check (current branch if none given):
   ```bash
   git branch --show-current
   ```
2. Validate against the pattern `^issue-[0-9]+-[a-z0-9]+(-[a-z0-9]+)*$`.
3. Skip/allow `main`, `master`, and any release/hotfix branches the user
   tells you are exempt — the rule targets feature/work branches.
4. If it doesn't match, tell the user exactly why (missing issue number,
   wrong separator, uppercase letters, etc.) and propose a corrected name
   rather than silently renaming or force-pushing anything. Renaming an
   existing branch (`git branch -m`) is a rewrite of shared-looking state —
   confirm with the user before doing it.

## Notes

- Do not fabricate an issue number if the user hasn't given one; ask.
- Do not auto-rename branches without explicit confirmation.
