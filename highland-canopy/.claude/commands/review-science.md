---
description: Independent remote-sensing correctness review of a path, branch or output
argument-hint: "[path or branch] (optional; defaults to current branch)"
---

Delegate to the `science-reviewer` subagent. Scope: `$ARGUMENTS` if given, otherwise the current branch's diff against `main` plus its outputs.

Relay its findings table and verdict to me unchanged. Do not fix anything unless I ask.
