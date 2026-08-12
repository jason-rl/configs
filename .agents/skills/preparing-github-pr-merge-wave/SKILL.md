---
name: preparing-github-pr-merge-wave
description: Use when coordinating multiple open GitHub pull requests to merge near the same time, especially when branches may conflict, form stacks, need rebasing, require independent review, have failing CI, or need ready, reviewer, and auto-merge updates.
argument-hint: "<PRs, branches, or ranges> [--review] [--ready] [--auto-merge] [--reviewer LOGIN ...]"
allowed-tools: Bash, Read, Grep, Edit, Write, Task
disable-model-invocation: true
---

# Preparing a GitHub PR merge wave

## Overview

Turn the requested PR set into verified exact remote heads that can land together without hidden dependency, conflict, review, or CI surprises.

**Core principle:** selection is not authorization to guess topology or overwrite work. Plan from immutable snapshots, require decisions where intent changes, rewrite bottom-up, and invalidate every result tied to a superseded SHA.

**REQUIRED SUB-SKILLS:** Use `jujutsu` before any VCS operation. Use `github:gh-address-comments` for existing review feedback, `github:gh-fix-ci` for GitHub Actions failures, `superpowers:receiving-code-review` before applying findings, and `superpowers:verification-before-completion` before reporting success.

## Inputs

Accept a union of:

- PR URLs or numbers and exact head branches.
- Inclusive PR ranges (`100..120`) and number bounds (`>=100`, `<=120`, `>100`, `<120`).
- ISO creation dates, inclusive date ranges, and the same comparison prefixes.
- `pr:`, `branch:`, or `date:` prefixes to disambiguate numeric/date-like branches.
- `--review`, `--ready`, `--auto-merge`, and repeated `--reviewer LOGIN` requests.

Ranges select open PRs authored by the current `gh` user. Explicit foreign-authored PRs require permission; remove declined PRs and recompute the graph.

Read [selection-and-topology.md](references/selection-and-topology.md) completely before selection, fetching, or rebasing. Read [execution-and-verification.md](references/execution-and-verification.md) completely before any mutation, review dispatch, push, or GitHub metadata update.

## Required workflow

Copy and maintain this checklist:

```text
Merge-wave progress:
- [ ] Detect VCS, repository, gh identity, permissions, and gh stack
- [ ] Expand selectors and approve foreign-authored PRs
- [ ] Normalize external roots and existing stack membership
- [ ] Fetch exact heads/root and resolve local divergence
- [ ] Analyze overlaps and approve every nondeterministic order
- [ ] Rebase and validate bottom-up
- [ ] Complete independent reviews and feedback loops if requested
- [ ] Push with exact leases and link every multi-PR chain
- [ ] Converge exact-current-head CI
- [ ] Apply ready/reviewer/auto-merge changes last
- [ ] Re-read and report every final PR state
```

1. Run the read-only planner before mutations:

   ```bash
   uv run --script <skill-dir>/scripts/plan_pr_wave.py \
     --selector '<selector>' --format json
   ```

   Repeat `--selector`. Add `--repo OWNER/REPO` and `--timezone AREA/LOCATION` when needed. Treat its output as analysis, not authorization. `stack_membership_complete: false` is intentional: inspect stack membership separately with `gh stack` before mutation.

2. Resolve every gate before rewriting:
   - Foreign author: warn and ask whether to include; declining drops it.
   - Multiple external roots: ask to drop minority groups or include their connector paths. For a tie, first ask which root is canonical.
   - Ahead/diverged local PR branch: ask whether to preserve/replay local work, preserve it but use remote, drop the PR, or abort.
   - Siblings needing linearization with no dependency-derived order: recommend an order with evidence, then require approval.
   - Existing stacks with unselected members: ask before inclusion or restructuring.

3. Rebase the bottom PR onto the latest external root, then each descendant onto its rewritten parent. If a reviewed/fixed lower layer changes, cascade only its upstack descendants.

4. When `--review` is requested, create one isolated worktree or jj workspace per PR before dispatching concurrent work. Independently review every exact PR head using fresh minimal context, the latest frontier model, and effort `max(current, high)`. Reviewers remain read-only. Apply fixes or feedback concurrently only in each PR's owning workspace and only after that PR and every base-side ancestor has finished review; independent components may proceed in parallel. Only the orchestrator may rebase, cascade, push, or relink shared branches. Re-review every rewritten head.

5. Push rewritten/fixed branches with exact leases. Prefer atomic multi-ref pushes; otherwise push bottom-up and stop/replan after any partial failure. Link each chain of two or more existing PRs using PR numbers:

   ```bash
   gh stack link --base <external-root> <bottom-pr> ... <top-pr>
   gh stack view --json
   ```

6. Check CI only for exact pushed heads. Fix branch-caused failures in the lowest responsible layer, rebase descendants, re-review changed heads when requested, repush, and repeat. Cancelled/superseded runs are historical; pending and infrastructure failures remain unresolved.

7. Apply `--ready` and reviewer requests after the last history rewrite, then wait for any newly triggered checks. Enable `--auto-merge` last with `--match-head-commit`. Ask before substituting atomic `gh stack merge` for native per-PR auto-merge.

## Quick reference

| Condition | Required response |
|---|---|
| Foreign-authored explicit PR | Ask permission; inclusion does not imply branch write access |
| Minority external root | Drop group or include connector path only after user choice |
| Local branch diverged | Preserve; never silently merge, reset, or overwrite |
| Siblings share a base | Linearize; approve order when dependencies do not decide it |
| Overlapping hunks | Confirm with disposable merge/rebase simulation |
| Lower PR changes | Rebase and invalidate every changed descendant result |
| Push lease fails | Stop, fetch, preserve concurrent work, recompute |
| Old CI is red/cancelled | Ignore only if it is not for the exact current head |
| Current CI pending | Not ready and not green |
| Metadata requested | Apply after final rewrite; auto-merge is last |

## Red flags — stop and re-plan

- “The user said force-push whatever is needed, so divergence is authorized.”
- “The minority PR was explicitly selected, so no root decision is needed.”
- “Oldest/smallest first is reasonable; no need to approve sibling order.”
- “Changing PR bases is enough; GitHub stack linkage is optional.”
- “The cancelled red run means CI failed” or “the new run is pending but probably fine.”
- “Reviews on the old SHA still count after its ancestor changed.”
- “Enable auto-merge now while CI catches up.”

All indicate a violated safety gate. Preserve state and return to the applicable workflow step.

## Common rationalizations

| Excuse | Reality |
|---|---|
| “Release cutoff is close.” | Time pressure changes reporting cadence, not ownership, divergence, order, lease, review, or CI gates. |
| “This branch is probably just stale.” | Ahead and divergent refs can contain unpublished user work; ask before choosing a source of truth. |
| “The graph has an obvious majority.” | The user decides whether minority PRs are dropped or connected. |
| “Disjoint siblings need no stack.” | A GitHub stack cannot represent in-degree greater than one; siblings still need a linear order. |
| “The rebase was clean, so conflict analysis is unnecessary.” | Semantic conflicts and later merge order still require diff and test validation. |
| “`gh stack push` is safe enough.” | It is lease-protected but non-atomic; verify every branch and handle partial success explicitly. |
| “Review passed before the cascade.” | A result belongs to an exact head SHA; a rewritten head requires fresh validation. |

## Completion contract

Report, per remaining PR: old and final SHA, external root, stack position/base, rebase and push result, review status, exact-current-head checks, draft state, requested reviewers, and auto-merge state/method. Distinguish verified success, pending work, infrastructure limits, and user-declined exclusions.
