@~/.config/AGENTS.md

Prefer using `Glob` tool over `find` or `ls [-R]`. But if you must use `find`,
use `fd` instead (be aware of it using Rust's regex engine and ignoring
gitignored/hidden paths by default).

Prefer using `Grep` tool over `grep`/`git grep`. But if you must use `grep`/`git
grep`, use `rg` instead (be aware of it using Rust's regex engine and ignoring
gitignored paths by default).

Prefer using `Read` tool over `cat` or `sed` or `awk`, but if you need to use
the coreutil binary to pipe to `head`/`tail`/etc. for whatever reason, prefer
using `head`/`tail`/etc.'s `file` argument instead of piping to its stdin.

Prefer using `Edit`/`Write` tool over shell redirection with coreutils/`echo`.

Prefer using `Monitor` tool over `Bash` tool when running tests or commands that
take a while to run, expecting intermediatery output lines. Make sure output of
such commands is unbuffered.

Prefer using `WebFetch` tool to download web page content over `curl`, even
if you intend to pass the content to another binary's stdin.

If calling a tool/binary that traverses the workspace root with unlimited or >=3
depth including gitignored paths (e.g. `grep -r`, `find`), explicitly exclude
`.claude/worktrees` from the search via the appropriate flag (e.g. `grep -r
--exclude`, `find -path '*/.claude/worktrees' -prune -o ...`; but you might as
well use `rg` and `fd` instead)
