# Execution and verification

## Contents

- Rebase and stack publication
- Independent review scheduling
- CI convergence
- Ready, reviewers, and auto-merge
- Final verification

## Rebase and stack publication

1. Reconfirm the external-root OID and every remote head lease.
2. Rebase the bottom unique commit range onto the external root, then cascade each descendant onto its newly rewritten parent. Preserve parent-relative intent with range-diff and patch-equivalence checks.
3. Resolve mechanical conflicts using source context and tests. Ask when alternatives encode different product behavior.
4. Validate each parent-relative diff and the integrated top. Run repository-prescribed focused and broad tests.
5. If the external root advanced, restart the cascade before publishing.
6. Push only changed branches. Prefer one atomic multi-ref push with explicit `--force-with-lease=<ref>:<old-oid>`. Otherwise push bottom-up, verify each remote OID, and stop/re-plan after partial success.
7. Link each chain with existing PR numbers so `gh stack link` cannot create PRs or push branches:

   ```bash
   gh stack link --base <root> <bottom-pr> ... <top-pr>
   gh stack view --json
   ```

Verify bases, heads, per-PR diffs, stack membership, and `needsRebase: false`. Do not merely add navigation text or change PR base fields.

## Independent review scheduling

When review is requested:

1. Snapshot exact base/head OIDs for every PR.
2. Create one isolated worktree or jj workspace per PR at its snapshotted head before concurrent review or feedback work. Never share a mutable checkout between PR workers.
3. Dispatch independent reviewers in parallel with fresh minimal context. Use the latest frontier model and reasoning effort equal to the higher of current or `high`. Reviewers remain read-only.
4. Each reviewer sees only repository requirements, its PR's exact parent-relative diff, and relevant nearby code. It must inspect analogous untouched code and return evidence-backed findings without editing.
5. Buffer out-of-order results. A PR becomes eligible for fixes only when it and all base-side ancestors finish review.
6. Apply verified findings in the owning PR workspace. Parallelize fixes only across eligible PRs whose workspaces and branch mutations are independent; process dependent layers bottom-up. Use `superpowers:receiving-code-review`; use `github:gh-address-comments` for existing GitHub threads.
7. Workers may commit changes only to their assigned PR branch. Only the orchestrator may rebase, cascade, push, or relink shared branches, and it must first verify that all workers touching the affected ancestry have finished.
8. A lower-layer change invalidates all descendant review results. Rebase descendants, run tests, and obtain fresh independent reviews for every changed exact head.
9. Continue until no actionable non-trivial findings remain. Push fixes before replying to or resolving existing review threads.

## CI convergence

Read check state for each exact remote head. Cancelled or superseded runs are historical. Pending, queued, or missing required checks are not green.

For a current-head failure:

1. Use `github:gh-fix-ci` and inspect GitHub Actions logs.
2. Distinguish branch-caused failures from infrastructure/environment failures.
3. Fix the lowest responsible PR. If an earlier fix changed a contract used above it, decide from intended behavior whether the lower or upper layer is wrong.
4. Rebase descendants, rerun local validation, re-review changed heads when broad review was requested, and push with fresh exact leases.
5. Invalidate old CI records and wait for replacement current-head runs.

Do not waive green CI because of a deadline. Report infrastructure failures or unfinished reruns as unresolved.

## Ready, reviewers, and auto-merge

Only after the final history rewrite:

1. Validate every requested reviewer login and repository eligibility.
2. Mark every remaining selected PR ready when requested.
3. Add every requested reviewer to every PR without needlessly re-requesting an existing approval/review.
4. Wait for checks triggered by readiness or review requests and repeat CI convergence.
5. Enable auto-merge last with the exact final head OID:

   ```bash
   gh pr merge <pr> --auto --<method> --match-head-commit <oid>
   ```

Use a merge queue when repository rules require it. If multiple merge methods are permitted and no documented default exists, ask. If native child auto-merge could merge into an open parent and rewrite/invalidate another selected head, explain the effect and ask whether to accept sequential landing or use atomic `gh stack merge`. Never silently substitute immediate atomic merge for requested auto-merge.

## Final verification

Re-read every PR and record:

- exact final head and base OIDs;
- external root and stack position;
- expected parent-relative diff;
- mergeability and `needsRebase` state;
- review result tied to the final head;
- all exact-current-head checks;
- draft state and requested reviewers;
- auto-merge state and method.

Report exclusions and reasons. A lease conflict, pending check, infrastructure failure, reviewer delay, unsupported auto-merge configuration, or later root/head movement means the wave is not fully converged.
