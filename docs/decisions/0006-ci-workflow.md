# 0006 — CI workflow

- **Status:** accepted
- **Date:** 2026-11-01
- **Task:** M0.2

## Context

CI must run lint and offline tests from the pinned env on every PR and on the integration branch. `main` holds only
the initial commit and is never merged into; all PRs target `task/M0.0-kit-setup`. A run can restore caches only
from its own branch, the default branch or (for PRs) the base branch.

## Decision

`.github/workflows/ci.yml`, one job on `ubuntu-latest`, `permissions: contents: read`, concurrency per workflow and
ref with cancel-in-progress. `timeout-minutes: 20` is a ceiling against hangs, not a measured value.

- **D1 triggers and badge:** push on `[task/M0.0-kit-setup, main]`, `pull_request`, `workflow_dispatch`. The push run
  on the integration branch seeds the env cache PRs restore. The README badge uses an explicit
  `?branch=task%2FM0.0-kit-setup`. Changing the GitHub default branch to the integration branch (owner-only) would
  make this unnecessary. Revisit the badge if that branch is renamed or merged into `main`.
- **D2 pinning:** actions pinned to full commit SHAs with a version comment: actions/checkout v7.0.1,
  mamba-org/setup-micromamba v3.2.1, actions/cache v6.1.0 (SHAs verified with `git ls-remote`). Bumps are manual.
- **D3 lint:** a single `pre-commit run --all-files --show-diff-on-failure`, so CI and laptops share one config.
  Ruff and nbstripout come from the env's pinned binaries. The pre-commit hook repo is cached.
- **D4 install:** `pip install -e . --no-deps --no-build-isolation`, using the env's pinned hatchling.
- **D5 cache:** the env is cached (keyed by the action on the env file hash) and the micromamba version is
  unpinned. `micromamba list` is printed at the end of each run to tie failures to resolved versions.
- Tests: `pytest -m "not network and not viewer"` (ADR 0005).

## Consequences

Linux-64, Python 3.13 only (ADR 0003); macOS and Windows rely on the M0.1 dry-run solves. A cached env can hide
breakage from floating transitive deps until it is evicted (7 days unused) or `environment.yml` changes. The first
PR runs cold until a push to the integration branch seeds the cache. Lint depends on GitHub for fetching
`pre-commit-hooks`. A weekly uncached scheduled run could be added later.
