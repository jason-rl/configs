Do not write _temporary_ docs files or intermediate output for your reference in
root of current workspace -- use the workspace provided by your harness or
`/tmp`.

If I have previously manually approved a fetch of a page of a documentation
site, you have automatic permission to fetch other pages with that same domain
(does not apply to different subdomain).

The definition of term 'prefer' throughout the rest of this system prompt

If using `curl`, always include `-f` and `-L` (unless URL protocol is not
HTTP/HTTPS or you are investigating redirect responses) and `--retry 1`.

If doing work inside multiple repositories or worktrees (even of same
repository), always prefix your shell commands with `cd {absolute path}` unless
you are _not_ using parallel agent(s) nor doing tasks in parallel and can
guarantee sequential steps.

If using absolute paths in your tool/binary calls, make sure you are aware
of whether you are in a git worktree/jj workspace (if you are, do **not** use
the workspace root accidentally) or not.

Prefer using `jq` binary over `python3 -m json` or `python3 -c 'import sys,
json; json.load(sys.stdin)'` or `node -e
'JSON.parse(require('fs').readFileSync(0))'`. The `yq` binary is helpful for
YAML content.

Prefer using `difft` binary over `diff` (set `GIT_EXTERNAL_DIFF=difft` env var
if using `git diff` in patch format) unless you think the diff will be very
large or it is too slow.

If having to pass multi-line string literals to command (such as `python3 -c` or
`node -e`), prefer using heredocs to pass such to stdin of `python3 -` or `node
-`, or `"$(cat <<DELIM)"` if command has no stdin alternative. Executing such
complex scripts inline necessitates helpful code comments.

Be aware of coreutils such as `mv`, `cp`, `rm` that interactively prompt by
default for overwrites, causing it to hang (prefix the command with '\'). `git
mv` and `git rm` variants are prefered if making commits is later in the plan
and not using `jj`.

`moreutils` is installed, which may be better alternatives to complicated
Bash pipelines/compound commands/redirections/process substitutions.

If a tool/binary you want to execute repeatedly cannot understand your syntax,
resort to viewing `--help`/`man` pages then search the web instead of many
trial & errors.

Any new feature additions/changes/removals or big code changes preceding the
creation or update of a PR necessitate a round of code review (preferably guided
by appropriate skills and must be done by an unbiased subagent of the most
frontier model variant with minimal context, at least high effort and at least
medium thinking (if applicable)). Subsequent implementations of suggested
feedback of said code reviews must still undergo this requirement recursively
until user is satisfied or only trivial "nits" are left to address. Code reviews
should also compare the code/files touched by the branch/PR diff with other
similar relevant files untouched by the diff -- it is best to checkout the
branch and understand the overall codebase directory layout to find such other
similar files and read their contents.

Any new source code files should not set copyright ownership/authorship--this
tends to happen when copying another file (containing copyright
ownership/authorship) as a template.

## Dependency Management

If exploring registries (e.g. npmjs/PyPi/Maven Central Repository/Bazel Central
Registry/Cargo/DockerHub) for 3rd party dependencies to consider for inclusion
in current project, compare version/update history, record of CVE reports and
handling, # of open issues/PRs and rate of issues/PRs created over time if
project homepage is a repository (of GitHub, GitLab, etc.).

If needing to fetch info of a third-party package, prefer using the
corresponding tool's search capability if possible (e.g. `npm view`, `cargo
info`, `docker search`). If I have previously manually approved a fetch of a
page of a package of a registry site (e.g. Pulumi Registry, PyPi registry, NPM
registry, crates.io, DockerHub), you have automatic permission to fetch other
pages of that same package (does not apply to other packages of same author).
they could be added to git staging area.

## Git/GitHub

If creating git branches for the purpose of creating pull requests to GitHub,
always use prefix `jason/` in the branch name.

Prefer using GitHub MCP server and/or plugin tools over `gh` CLI unless you
need functionality absent in former but present in latter. Never fetch
api.github.com or github.com URLs directly. If you must inspect multiple files
of a GitHub repository, consider cloning to a temporary directory with depth
1 (or bigger number if necessary) and branch/tag of the repository's latest
stable release (or specific version if necessary). Don't forget to search issues
and/or PRs of the repo (even closed ones) if you suspect an upstream bug.

If editing a PR branch, prompt to make atomic commits with detailed messages or
amend/fixup with improved commit message, then prompt to push
(`--force-with-lease` if necessary). You must use file paths that are not
directories so that you do not include unwanted untracked files or irrelevant
dirty/staged files when using `git add` (which should always be used instead of
`git commit -a` or `git add -A`. If the changes are to only fix failing
lint/built/test/etc. on a certain commit, amending/fixup the problematic commit
is preferred over new commit. You must check current branch name before creating
commits or amending/fixup to be safe. Push commands must include the remote
branch name always. If push request is approved, you have permission to amend
the PR description for changed features, to stay in sync with changeset, etc.

Anytime you amend PR description, you must always fetch the current PR
description to stay synchronized and prevent accidental information loss. PR
descriptions should always follow GitHub repo's `pull_request_template.md` (not
inserting anything before the 1st header). The description of the changes should
be about the end result we want to acheive, so implementation details aren't
helpful unless they deliver quantifiable improvements. Never include
implementation changes only present between two commits of the PR but not on
target branch--if you feel you have to b/c the latter commit is a proposed
alternative/improvement over the former, that's a sign that the latter could be
its own PR (stacked or cherry-picked).

If creating a GitHub PR, draft mode should always be true. Then if you have
already at least a round of code review where the latest one showed just only a
few trivial issues or possibly any non-trivial issues brought over from an older
round that I have explicitly asked you to dismiss or you find contradictory to
my intent/plan (document these non-trivial ones in PR description!), prompt for
list of GitHub usernames to add as reviewers (which I may decline), then mark PR
as ready and tag those reviewers as an atomic transaction.

