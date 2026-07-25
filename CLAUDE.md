# Store Command Center — working agreement for AI sessions

## Branch & version — READ THIS FIRST
- **Claude works on `master`; local models work on `dev`.** Claude Code sessions (you) develop directly on `master` and may commit — and push — there (with the owner's OK). The local-model dev swarm ("The Engineers") works ONLY on the `dev` branch (in `~/projects/store-dev`); its work reaches `master` only through the review → promote pipeline. If you are a Claude session, your branch is `master` — always, no question.
- **Do NOT attach version numbers to internal work.** No `vX.Y` in commit messages, no bumping any version for a normal change. Those labels are noise and have been causing cross-session version confusion — stop using them. (History went `v0.1.0` straight to `v2.x`; there was never a real v1.x or v2.x.)
- Human collaborators → `collab` branch (off dev) → merged to `dev` → main via the promote pipeline. Add collaborators in the GitHub tab.
- **The release version lives in `/VERSION` and changes ONLY when publishing to the public repo** (the retail scrub/publish step in `app/routers/github/jobs.py` promote flow). Internal commits to `master` never touch `/VERSION`. Public baseline today: `0.1.0` (the version before the `v2.x` labels started).

## Committing — the master worktree is shared by several sessions at once
- Commit with **explicit paths only** (`git add <path> ...`). **Never `git add -A` / `git commit -am`** — you will sweep other sessions' uncommitted work into your commit.
- Use conventional messages (`feat(scope): ...`, `fix(scope): ...`) — not version labels.
- **Do not push** without the owner's explicit say-so. **Never** push to the public repo except through the retail publish flow.

## Layout
- `~/projects/platform_dev/store` = `master` = live line (served on **8787**).
- `~/projects/store-dev` = the dev swarm's build worktree (`REPO_DEV`) **and** the on-demand dev-test store (**8788**, its own `.env`/DB).
- `~/projects/store-retail` = scrubbed public mirror (retail branch).
- `store-command-center-public` (GitHub) = the public release; a **fork** is what public installs make of it.
