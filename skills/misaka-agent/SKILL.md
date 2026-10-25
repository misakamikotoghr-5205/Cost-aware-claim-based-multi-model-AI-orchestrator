---
name: misaka-agent
description: Connect an agent to the user's explicitly selected MISAKA AGENT desktop runtime. Inspect account quota, optional extensions and worker state, preview routing, or dispatch bounded tasks to paired local nodes. Use when the user asks to use MISAKA AGENT, its cloud/local fleet, or its quota dashboard from another agent.
license: MIT
compatibility: Python 3.10+; a locally installed MISAKA AGENT runtime based on the pinned Hermes gateway, with orchestrator-core enabled. Host must permit local subprocess and loopback HTTP access.
metadata:
  version: "0.2.1"
---

Use the bundled stdlib CLI, `scripts/misaka.py`. This connects to the same native
runtime and profile as the desktop. It does not implement another agent loop.

1. Read `references/commands.md`. Use an existing explicit named connection, or
   configure one using installation paths supplied by the user or verified in
   their project. Never guess a launcher from PATH, choose a different account,
   read credential files, or print environment variables.
2. Run `status`, `extensions`, and `quota` before relying on capabilities or
   balances. Missing optional `local-judge`, `free-models`, or `smart-pointer`
   reports `unavailable`. Do not install or enable them implicitly. Unknown,
   stale, expired and blocked quota is not available quota. Percentages,
   currencies, tokens and request counts remain separate units.
3. For a fleet task, list/select a node, use an advertised workspace alias,
   and submit an explicit bounded task with a stable ID. Only use tools allowed
   by the owner and target node. The core's agent exposure setting must already
   permit fleet actions. The host agent's permission policy continues to apply.
4. Keep the same task ID after a timeout or `outcome_unknown`. Query `task-status`
   and reconcile effects. Never create another ID to replay uncertain work.
   `task-cancel` cancels queue admission; it does not prove running local effects
   were undone. The pointer produces drafts for the native desktop; this skill
   does not imitate screen capture or silently send a screenshot.

Use request JSON through `--request path.json` or `--request -` (stdin), not shell
interpolation. There is no arbitrary URL, remote shell, credential upload,
extension-install or worker-start command. Routing preview may use an explicitly
enabled local judge; it never submits the task.

Install locations and setup are in `README.md`. The runtime transport and its
profile/ticket boundary are documented in `references/transport.md`.
