# GitHub workflow

**Purpose:** the exact git commands each teammate runs, every day, so our work joins up without merge conflicts.
**Owner:** Winston · **Last updated:** 2026-10-10

---

## 1. The idea

- **`main`** is the shared, working version of the app. Nobody works on it directly.
- **Your branch** (`winston`, `darren` or `jayanth`) is your own copy. You do all your work there, and you keep the same branch for the whole hackathon (D-002).
- A **pull request (PR)** is how your branch's work gets into `main`. Winston merges it.
- After any PR is merged, everyone **merges `main` back into their own branch**, so they have the others' latest code.

```
 winston ──●──●──┐            ┌──●──●──┐
                 ▼  PR        │ merge  ▼  PR
 main    ────────●────────────●────────●──────
                 │ merge      ▲  PR
 darren  ──●──●──┴──●──●──●───┘
```

Conflicts stay rare because each person owns different folders (`docs/ROLES.md`). Git only reports a conflict when two people change the same lines of the same file.

| Rule | Why |
|---|---|
| Work only on your own branch | `main` always runs |
| Open a PR into `main` **at least once a day** | Small merges are easy; one big merge on Tuesday is not |
| After any merge, run `git merge origin/main` on your branch | You build on the others' latest code |
| Merge PRs with **"Create a merge commit"**, never "Squash" or "Rebase" | Squash rewrites commits, so a branch you keep using hits the same conflicts again and again |
| Never delete your branch after a merge | You keep using it |
| Only edit files in your own area | Two people editing one file is how conflicts happen |

## 2. One-time setup

Run once on your laptop. Replace `darren` with your own branch name everywhere in this file.

```bash
git config --global user.name "Your Name"
git config --global user.email "you@example.com"
git config --global pull.rebase false        # "git pull" merges, never rebases

git clone https://github.com/winstony29/tencenthackathon.git dcta
cd dcta
git checkout darren                          # switch to your branch; it tracks origin/darren
git branch --show-current                    # must print your name, not "main"
```

Then set up Python as in `README.md` ("Run locally").

## 3. Every work session (the template)

### 3.1 Start: get up to date

```bash
git checkout darren
git pull                      # your own branch from GitHub (in case you pushed from another laptop)
git fetch origin
git merge origin/main         # everyone else's merged work
```

If `git merge` reports a conflict, go to §5.

### 3.2 Work and commit

Commit small and often, with one task per commit. Message format: `area: what changed` (areas: `intent`, `validation`, `bank`, `web`, `shared`, `docs`, `test`).

```bash
git status                    # see what you changed; check nothing unexpected is listed
git add web/                  # add only your own files or folders
git commit -m "web: add mic button and text fallback"
```

### 3.3 Push: back up to GitHub

```bash
git push
```

Push at least every few hours. A pushed commit is safe even if your laptop dies.

## 4. Getting your work into `main` (pull request)

Do this at least once a day, whenever your branch runs and the tests pass.

1. Merge the latest `main` into your branch and check it still works:
   ```bash
   git fetch origin
   git merge origin/main
   pytest
   ruff check . && ruff format --check .
   git push
   ```
2. Open the PR. Either use the GitHub website (**Pull requests → New pull request**, base: `main`, compare: `darren`), or run:
   ```bash
   gh pr create --base main --head darren --title "web: confirmation cards" --body "Task D-04. Tested on phone-sized screen."
   ```
   Put the checklist task ID (e.g. `D-04`) in the description.
3. Tell the group chat. Winston reviews and merges with **"Create a merge commit"**. Do **not** click "Delete branch" afterwards.
4. Everyone, once it is merged:
   ```bash
   git fetch origin
   git merge origin/main
   git push
   ```

## 5. Fixing a merge conflict

A conflict means you and someone else changed the same lines. Git stops and marks the file:

```text
<<<<<<< HEAD
your version
=======
the version from main
>>>>>>> origin/main
```

1. Run `git status`. Files under "both modified" have conflicts.
2. Open each one. In VS Code, click **Accept Current** (yours), **Accept Incoming** (main's) or **Accept Both**, or edit by hand. Remove every `<<<<<<<`, `=======` and `>>>>>>>` line.
3. Make sure the app still runs and `pytest` passes.
4. Finish the merge:
   ```bash
   git add path/to/fixed_file
   git commit                    # git writes the message for you; save and close
   git push
   ```

Stuck or unsure? Run `git merge --abort` to go back to how things were before the merge, then ask the file's owner (`docs/ROLES.md`) in the group chat.

## 6. Never do these

| Don't | Do instead |
|---|---|
| Commit or push to `main` | Commit to your branch; open a PR |
| `git push --force`, `git rebase` | `git merge origin/main` |
| Create new branches | Use your one branch |
| Delete your branch on GitHub | Keep it |
| Squash or rebase merge a PR | "Create a merge commit" |
| Commit `.env`, `var/`, `web/node_modules/` | `.gitignore` already skips them; check `git status` before committing |
| Edit someone else's folder | Ask them, or ask Winston for shared files |

## 7. When something goes wrong

| Problem | Fix |
|---|---|
| `git push` says "rejected … fetch first" | `git pull`, then `git push` |
| `git merge` says "your local changes would be overwritten" | Commit your changes first (§3.2), then merge again |
| You committed on `main` by mistake and **have not pushed** | `git checkout darren`, `git merge main` (moves the work over), `git checkout main`, `git reset --hard origin/main` (cleans local `main`; only safe because your work is now on your branch) |
| You pushed a secret (API key) | Tell Winston at once and replace the key with the provider. Deleting the commit does not help; the key is already public. |
| Not sure which branch you are on | `git branch --show-current` |
| Want to see what changed | `git status`, `git diff`, `git log --oneline -10` |

## 8. Cheat sheet

| When | Command |
|---|---|
| Start of session | `git checkout darren && git pull && git fetch origin && git merge origin/main` |
| Save work | `git add <your files> && git commit -m "area: what changed"` |
| Back up | `git push` |
| Ship to `main` | merge `origin/main`, test, push, open PR (§4) |
| After someone's PR is merged | `git fetch origin && git merge origin/main && git push` |
| Undo a merge in progress | `git merge --abort` |

## 9. For Winston: repo settings (one time)

| Setting | Where on GitHub |
|---|---|
| Invite Darren and Jayanth with write access | Settings → Collaborators |
| Allow only "merge commits"; turn off squash and rebase merging | Settings → General → Pull Requests |
| Protect `main`: require a pull request before merging | Settings → Branches → Add rule for `main` |
