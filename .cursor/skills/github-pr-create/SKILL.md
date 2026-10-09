---
name: github-pr-create
description: Creates a GitHub pull request for the current branch using the gh CLI. Use when the user asks to open a PR, create a pull request, or push and raise a PR. Verifies branch name follows the project convention, builds a compliant PR body, and stops before merging.
allowed-tools: Bash, Read
---

# Create GitHub Pull Request

Follow every step in order. Stop immediately on any violation — do not proceed past a STOP condition.

## Step 1 — Verify branch

```bash
git branch --show-current
```

- If the branch is `main`, `master`, or `develop` → **STOP**: "Cannot create a PR from a protected branch. Switch to a feature branch first."
- Branch must match `{type}_{description-in-kebab-case}` where type is one of:
  `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `perf`, `ci`, `build`, `revert`, `chore`, `hotfix`, `bugfix`, `epic`
- If the format is wrong → **STOP**: "Branch name doesn't follow `{type}_{description}` convention. Rename it first — e.g. `feat_heatmap-summary-endpoint`."

## Step 2 — Check working tree

```bash
git status --short
```

If there are uncommitted changes, warn the user: "There are uncommitted changes. Commit or stash them before opening the PR."

## Step 3 — Summarise commits for the PR body

```bash
git log origin/main..HEAD --oneline
```

Use the commit list to draft the PR summary. All commits should follow `type[(scope)]: description` — flag any that don't.

## Step 4 — Gather missing fields

Ask the user for any of the following that are not already clear from context:

- **Ticket** — issue or ticket reference (e.g. `TT-421`)
- **Summary** — one or two sentences describing what this PR does and why
- **Affected Areas** — list of modules, services, routes, or migrations changed
- **Test status** — one of: `Unit tests added`, `Unit tests updated`, `Integration tests added`, `Not required — <reason>`

## Step 5 — Push the branch

```bash
git push -u origin <branch-name>
```

Only push if the branch has no upstream yet. If it already has an upstream, skip this step.

## Step 6 — Create the PR

```bash
gh pr create \
  --title "type(scope): short summary of the PR" \
  --body "$(cat <<'EOF'
### Ticket
<ticket reference>

### Summary
<summary>

### Affected Areas
- <area 1>
- <area 2>

### Tests
<test status>
EOF
)"
```

- The PR title must follow Conventional Commits: `type[(scope)]: description`
- Substitute all placeholders with the values gathered in steps 3–4

## Step 7 — Return PR URL

Print the PR URL returned by `gh pr create`. Stop here.

**NEVER merge, approve, or auto-close the PR.**
