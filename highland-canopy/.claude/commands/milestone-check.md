---
description: Check whether a milestone's tasks and "done when" criteria are met
argument-hint: "M1 | M2 | M3"
---

For milestone `$ARGUMENTS` in `docs/ROADMAP.md`:

1. List each task with its box state, its PR (via `gh pr list --search <task-id> --state all`), and whether its outputs exist under `outputs/<task-id>/`.
2. Delegate to the `verifier` subagent to run lint and tests on `main`.
3. For **[human]** tasks, check `data/field/` for the field points they should have produced and count them by class.
4. Report as a table, then a one-line verdict: done / not done, and the single most useful next step.
