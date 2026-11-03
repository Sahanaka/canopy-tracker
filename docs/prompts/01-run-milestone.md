# 01 — Drive a milestone

Use when you want Claude Code to work through a milestone task by task, still pausing at every plan.

```
Work through milestone <M1> in docs/ROADMAP.md using /next-task for each task in order.

- Stop at every plan for my approval, as /next-task requires.
- When you reach a [human] task, stop, tell me exactly what I need to do,
  and what you've prepared for it (files, maps, QField layers).
- After each PR, give me a 5-line status: PR link, outputs, ADRs to read,
  reviewer verdict, next task.
- If two tasks in a row end with exhausted fix loops, stop the run and
  summarise what's blocking.
```
