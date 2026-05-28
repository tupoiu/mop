# jj (Jujutsu) Research Notes

Findings from setting up jj with GitHub for this project.

---

## Colocating with Git

If the repo was initialized with `jj git init` (git backend, no `.git`), the git store lives at `.jj/repo/store/git`. To expose it as a colocated `.git`:

```bash
mv .jj/repo/store/git .git
echo "../../../.git" > .jj/repo/store/git_target
```

If there's no `.jj` at all, just:

```bash
jj git init --colocate
```

---

## Pushing to GitHub

jj needs a named **bookmark** (equivalent of a git branch) to push. Remote tracking refs are `name@origin`; local bookmarks are `name`.

```bash
jj git remote add origin git@github.com:user/repo.git
jj bookmark create main -r @      # create bookmark at current commit
jj git push --bookmark main
jj bookmark track main@origin     # link local bookmark to remote tracking ref
```

After the first push, `jj git push` keeps the bookmark updated as you move it forward.

---

## `jj log` display

- `~` at the bottom means history is **elided** (already pushed or out of default range) — not missing.
- Default log only shows a limited window. To see everything: `jj log -r 'all()'`
- The `◆` symbol marks immutable commits (already pushed to a remote).

---

## Author vs Committer

jj commits have both an **Author** and a **Committer** field (same as git). If either is blank, `jj git push` refuses with:

```
Error: Won't push commit <hash> since it has no author and/or committer set
```

This is a **jj-side check**, not GitHub's. Commits created before user config was set can have a blank committer. To inspect:

```bash
jj show --stat -r <rev>   # shows both Author and Committer fields
```

To fix author: `jj metaedit --update-author -r <rev>`  
To fix committer: jj has no direct command — `jj rebase -s <rev> -d <rev->` should rewrite it, but in practice jj skips the rewrite if it detects nothing changed (same tree, same parent). Workaround: push directly from the git store, bypassing jj's check:

```bash
GIT_DIR=.jj/repo/store/git git push <url> remotes/origin/main:refs/heads/main
```

---

## `.jj` directory contents

`.jj/` contains only internal state — commit extras, index, op log, working copy tracking. **No user config lives here.** User config is at `~/.config/jj/config.toml`.

Safe to delete and re-init if corrupted:

```bash
rm -rf .jj
jj git init --colocate   # re-initializes from existing .git
```

After re-init, re-add the remote and fetch:

```bash
jj git remote add origin git@github.com:user/repo.git
jj git fetch
jj debug reindex          # needed if index is stale after re-init
jj bookmark track main@origin
```

---

## Recovering history after re-init

If `jj log` only shows one commit after re-init, the git history is still in `.git` but jj has no bookmark pointing to it. Find the tip:

```bash
GIT_DIR=.git git log --oneline
```

Then create a bookmark pointing to it:

```bash
jj bookmark set main -r <hash>
```

---

## Hooks

**jj has no native hook system** (no pre-commit, post-commit, etc.).

Git hooks (`.git/hooks/pre-commit`) do **not** fire on `jj commit` or `jj new` — jj writes directly to the git object store without invoking `git commit`.

Options for enforcing formatters:
1. **`jj fix`** — built-in formatter integration, runs on `jj fix`. Configure in `.jj/config.toml` or user config:
   ```toml
   [fix.tools.ruff]
   command = ["ruff", "format", "$path"]
   patterns = ["glob:'**/*.py'"]
   ```
   Runs file-by-file on changed files only. Can't run whole-project tools like `poe format`.
2. **Shell wrapper function** — wrap `jj` in a fish/bash function that intercepts `commit` and `new` and runs the formatter first. Lives in `~/.config/fish/functions/jj.fish`. Not shared with collaborators.
3. **Neither** — run the formatter manually before committing.

---

## Operations log

Every jj operation is recorded. Undo and restore:

```bash
jj op log                    # see all operations with IDs
jj undo                      # undo last operation
jj op restore <op-id>        # restore to a specific operation
```

---

## Immutable commits

Commits reachable from a remote bookmark (`main@origin`) are marked immutable (`◆`). You cannot edit them directly:

```bash
jj edit <immutable-rev>      # Error: Commit is immutable
```

To work on top of an immutable commit: `jj new <rev>`

---

## Miscellaneous

- `jj rebase -r <rev> -d <parent>` — rebase single commit only (descendants rebased separately, can cause conflicts mid-stack)
- `jj rebase -s <rev> -d <parent>` — rebase commit + all descendants together (safer for mid-stack rewrites)
- `jj abandon <rev>` — discard a commit
- `jj bookmark set <name> -r <rev>` — move a bookmark to a specific revision
- `@` = current working copy commit, `@-` = its parent
- Revset `::@` = all ancestors of current commit including itself (excludes root)
- `jj git fetch` requires git >= 2.41.0
