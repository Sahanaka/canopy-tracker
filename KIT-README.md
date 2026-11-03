# Claude Code kit — Highland Canopy

Drop these files into the root of your new repo (github.com/Sahanaka/<repo>), commit, then open Claude Code there.

| Path | What it is |
| --- | --- |
| `CLAUDE.md` | Standing project context: mission, constraints, stack, layout, science gotchas, workflow rules |
| `docs/ROADMAP.md` | Tasks M0–M3 with acceptance criteria; the agents work through it in order |
| `.claude/agents/` | `planner` (Opus), `implementer` (Sonnet), `verifier` (Sonnet), `science-reviewer` (Opus) |
| `.claude/commands/` | `/next-task`, `/review-science`, `/milestone-check` |
| `.claude/settings.json` | Permissions (push/PR ask first; force-push, merge, push to main denied) + ruff-format hook |
| `docs/prompts/` | Kickoff, run-a-milestone, unstick, after-fieldwork, update-the-3D-viewer prompts |
| `docs/decisions/` | ADR template, ADR 0001 (Planetary Computer), ADR 0002 (3D visualisation) |
| `.github/workflows/ci.yml` | Lint + offline tests on push and PR (expects `environment.yml`, created in M0.1) |

## Start

1. Create the repo, copy the kit in, commit.
2. `claude` in the repo root, paste `docs/prompts/00-kickoff.md`.
3. Answer its questions, then let it run `/next-task M0.1`.
4. Draw the AOI yourself (M0.3) when it asks.

## 3D

- **M1.3** adds a PyVista 3D view in the notebooks for checking terrain and drapes.
- **M1.8** builds the public MapLibre 3D viewer on GitHub Pages; M2.5 and M3.6 add layers to it.
- Turn on GitHub Pages for the repo (Settings → Pages → source: GitHub Actions) before M1.8.

You approve every plan and every push/PR; nothing merges without you.
