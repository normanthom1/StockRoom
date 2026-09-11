---
name: finish-issue
description: How to finish a StockRoom GitHub issue - verify, commit, open a PR, merge to main and close the issue. Use whenever the work for an issue is complete, and at the start of an issue to set up the branch.
---

# Finishing an issue

CLAUDE.md gives standing permission to commit, push, merge to main and close issues once an issue is done. That permission only covers these steps, and only when everything passes.

## Starting
```bash
git checkout main && git pull
git checkout -b issue-<N>-<short-slug>
```
Read the issue and its "Depends on" list first. If a dependency is still open, stop and tell the user.

## Finishing
1. **Check.** Every item must pass:
   ```bash
   python manage.py test
   python manage.py makemigrations --check --dry-run
   python manage.py check
   ruff check .
   ```
   Go through the issue's "Done when" list. If anything fails or can't be met, stop and tell the user. Don't merge.
2. **Commit.** Review `git status` and stage the files deliberately. Write an imperative summary line, then a body explaining why. End the body with `Closes #<N>` and the co-author trailer the harness specifies.
3. **Open a PR.**
   ```bash
   git push -u origin HEAD
   gh pr create --fill --body "Closes #<N>. <one-line summary>"
   ```
4. **Wait for CI**, if checks exist: `gh pr checks --watch`. If they fail, fix the problem and push again. Never merge a red build.
5. **Merge:** `gh pr merge --squash --delete-branch`
6. **Confirm the issue closed:** `gh issue view <N> --json state`. If it's still open, run `gh issue close <N> --comment "Done in #<PR>"`.
7. **Sync:** `git checkout main && git pull`
8. **Report** the PR link, anything left over, and the next issue in the epic (#4) order.

## Issues that involve a human
- **Human-only issues:** don't do them. Tell the user what needs doing.
- **Claude + Human issues:** merge the Claude part, but leave the issue open. Add a comment listing the human steps that remain.
