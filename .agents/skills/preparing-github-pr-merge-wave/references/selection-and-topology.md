# Selection and topology

## Contents

- Snapshot and selector rules
- Polyforest normalization
- Fetching and local divergence
- Diff and ordering analysis

## Snapshot and selector rules

1. Confirm the current repository and authenticated login with `gh auth status`, `gh api user`, and `gh repo view`.
2. Snapshot every open PR needed for selector expansion in two phases. First fetch a lean catalog containing only identity, author, repository-qualified head/base branches and OIDs, creation time, draft state, repository permission, and changed-file count. After resolving selectors, hydrate checks, reviews, and review requests only for selected PRs in sequential GraphQL batches of at most five; fetch their file patches through the paginated REST endpoint. A GraphQL HTTP 502/504 timeout splits only the failed batch recursively without retrying it at the same size. A singleton timeout or any snapshot identity drift fails closed and requires a fresh run. Never replace this with an all-open query containing nested check or review collections. Separately inspect GitHub stack membership with `gh stack`; the planner reports `stack_membership_complete: false` because ordinary PR-list APIs do not expose authoritative stack membership.
3. Run `scripts/plan_pr_wave.py`. Selectors are a deduplicated union:
   - A bare number is an explicit PR before it can be a numeric branch; use `branch:` for the latter.
   - Explicit PRs/branches may be foreign-authored. Warn and ask once with the exact list. Declined PRs leave the set.
   - PR/date ranges and bounds select only open PRs authored by the authenticated user.
   - Date predicates use `createdAt`. Plain dates and ranges include whole local calendar days. `>` begins after the named day; `<` ends before it.
4. Reject cross-repository URLs, closed PRs, ambiguous branch matches, duplicate heads, cycles, or an empty result.

## Polyforest normalization

Model each selected PR as `head -> base`. For each selected head, follow selected edges until the first branch outside the set; this is its external out-neighbor/root.

When there is more than one root:

1. Count selected PRs per root. If tied, ask which root is canonical.
2. For each minority group, present two choices: drop that group, or include the unique open-PR connector path from its root to the canonical root.
3. If no unique path exists, report that it cannot be automatically connected. If multiple connector PRs share a head, ask which path is intended.
4. Apply the foreign-author and write-permission gates to connectors. Re-run the planner after every inclusion/exclusion.

Inspect GitHub stack membership for every selected and connector PR. Existing stack members outside the set are not implicitly authorized. Ask whether to include them or leave their stack untouched; never silently remove or relink them.

## Fetching and local divergence

Detect plain Git versus colocated/non-colocated jj before commands. Use isolated worktrees/workspaces and preserve the user's current checkout.

Fetch targeted remote refs for every selected head, connector, and external root. Record fetched OIDs. Then classify each local PR ref:

- Missing: create it at the fetched remote head.
- Equal: no action.
- Behind and fast-forwardable: advance it.
- Ahead or divergent: stop and ask whether to preserve/replay local work, preserve it under recovery metadata while using remote, drop the PR, or abort.

Do not infer that an untracked local commit is disposable. Do not use reset, checkout-discard, unqualified force, or automatic merge to resolve divergence.

Before mutations, create exact recovery refs/bookmarks for every branch that may change. Re-read remote heads immediately before pushes; a changed remote invalidates the plan.

## Diff and ordering analysis

For every base-relative PR diff, collect status, old/new path, additions/deletions, and zero-context hunks. Conservatively flag:

- overlapping changed-line intervals;
- rename/copy/delete interactions;
- binary, submodule, or unavailable patches;
- whole-file replacements and generated artifacts.

Confirm risks with disposable `git merge-tree` or temporary rebase simulations. The planner's overlap report is a warning, not proof that a merge is safe or unsafe.

Every selected node with in-degree greater than one must have its predecessors linearly ordered because GitHub stacks do not support multiple immediate parents. Existing dependencies decide order first. Otherwise recommend a bottom-to-top order using:

1. semantic dependency;
2. conflict resolution direction;
3. least history/review-anchor churn;
4. smaller foundational diff;
5. creation time and PR number only as final tie-breakers.

Show the proposed graph and require approval before changing bases or histories. Never interpret selector order as stack order.
