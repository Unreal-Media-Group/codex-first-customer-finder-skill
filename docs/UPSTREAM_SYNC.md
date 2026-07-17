# Safe upstream synchronization

## Branch strategy

- `main` is an exact fast-forward-only mirror of `upstream/main`.
- `unreal` contains Unreal-specific governance, skills, shared utilities, fixtures, tests, and docs.
- `origin` is the fork. `upstream` must normalize to `https://github.com/Kappaemme-git/codex-first-customer-finder-skill.git`.

## Every session

Run:

```bash
bash scripts/check-upstream.sh
```

Before editing, run:

```bash
bash scripts/sync-upstream.sh
```

The check validates the repository and remotes, fetches upstream, and reports branch, cleanliness, main divergence, Unreal ancestry, and whether synchronization is needed. It never switches, merges, commits, pushes, or changes remotes.

The sync script refuses dirty worktrees, interrupted Git operations, unexpected branches, divergent local `main`, and unsafe remotes. From `unreal` it fast-forwards `main`, returns to `unreal`, and merges `main` only when needed. It never rebases, resets, forces, cleans, stashes, pushes, or changes remotes. A conflict is aborted and reported for manual review.

Each successful run appends a scrubbed entry to the ignored repository-local `logs/execution/PROSPECTING_SKILLS_SYNC_LOG.local.md`. Keeping machine-local sync events out of tracked state makes repeated no-op synchronization idempotent.

`--maintenance` is intentionally narrow: it may be used only when starting on `main`, and performs the mirror update without merging into `unreal`.

Temporary-repository tests patch only their copied check script to approve a temporary local upstream. Production checks reject Git URL rewriting that changes the effective approved destination. Tests never target this working copy.
