---
description: Run the full plan → approve → implement → verify → review → PR loop for the next roadmap task
argument-hint: "[task-id, e.g. M1.2] (optional)"
---

Drive one task from `docs/ROADMAP.md` to an open PR.

1. **Pick the task.** If `$ARGUMENTS` names a task id, use it. Otherwise take the first unchecked task that is not marked **[human]** and whose earlier tasks are done. If the next task is **[human]**, stop and tell me what I need to do and what the agents have prepared for it.
2. **Plan.** Delegate to the `planner` subagent with the task id. When it returns, show me a 5–10 line summary of `docs/plans/<task-id>.md`, list its "Decisions needed", and **stop and wait for my approval**. Apply any changes I request to the plan before continuing.
3. **Implement.** After I approve, delegate to the `implementer` subagent with the task id and the plan path.
4. **Verify.** Delegate to the `verifier` subagent. If it reports FAIL, send the failing checks back to the `implementer` and verify again. Maximum two fix loops.
5. **Review.** Delegate to the `science-reviewer` subagent. Blockers and should-fix findings go back to the `implementer`, then re-verify. Maximum two review loops in total.
6. **Close out.** When verify passes and review approves (or loops are exhausted):
   - Tick the task's box in `docs/ROADMAP.md` only if every acceptance criterion is met.
   - Push the task branch and open a PR with `gh pr create`: what changed, how it was verified (verifier table), reviewer verdict, open questions, ADRs added.
   - If loops were exhausted, do not tick the box; open the PR as a draft and summarise the blocker.
7. Tell me in under 10 lines: PR link, outputs produced, decisions I should look at, and the next task.

Never merge PRs, never push to `main`.
