# Commands

Put `--connection NAME` and optional `--profile NAME` before the command.
All output is JSON. `ok:false` is a failure or unavailable state, not success.

| Command | Behavior |
| --- | --- |
| `status` | Ensure the selected native runtime and detect the enabled core |
| `quota` | Read the profile's cached provider observations and separate cloud ledger section |
| `quota-refresh` | Start the existing bounded native refresh; read `quota` again for completion |
| `extensions` | Actual installed/enabled/routing state; missing optional packages are unavailable |
| `route-preview --request task.json` | Preview the native delegation route; no task submission |
| `nodes-list` | List owner-authorized paired nodes, with the existing agent exposure gate |
| `nodes-select --request filters.json` | Select an advertised online node matching explicit requirements |
| `task-submit --request task.json` | Submit one bounded, stable-ID task through the common native agent gate |
| `task-status --task-id ID` | Read the same task's receipt |
| `task-cancel --task-id ID` | Cancel that task through the existing owner/node authorization |
| `worker-status` | Read worker state; does not start or stop it |
| `models` | Read the installed free-models extension's cached inventory |
| `models-refresh` | Explicitly refresh its configured public/local discovery sources; no inference |
| `judge-test` | Test the installed local judge's saved connection/weights; no download or judgement inference |
| `intent-create --request intent.json` | Create candidates from user-supplied context metadata and keywords |
| `intent-refine --session-id ID --request refine.json` | Refine that intent session using its current revision |
| `intent-commit --session-id ID --request choice.json` | Return a selected draft; does not send chat or execute tools |

Route input accepts `task_id`, `goal` or `title`, `type`/`task_type`, `requires`,
`value_level`, and `toolsets`. A disabled router preserves the native route.

Node filters accept `tools`, `skills`, `executors`, `models`, `platform`,
`max_actions`, and `workspace_alias`. Task submission uses the following shape:

```json
{
  "task_id": "review-20261025-001",
  "target": "node-id-from-nodes-list",
  "task": "Review the approved document using the configured local tools",
  "workspace_alias": "documents",
  "relative_dir": ".",
  "tools": ["read_document"],
  "max_actions": 2,
  "task_card": {"type": "writing", "requires": [], "value_level": "normal"}
}
```

For explicit MCP plans, `tool_calls` is a list of `{name, arguments}` objects.
Every name must be granted by both the owner and target node, and each argument
is revalidated on the target. A natural-language task is interpreted only by the
locally configured native executor. Do not send credentials, absolute workdirs,
environment variables, HTTP headers, owner authority objects or a shell command
field. Requests are limited to 64 KiB and all operations map to fixed API paths.

`outcome_unknown` means the response did not establish whether a mutation
committed. Keep the original `task_id`, inspect `task-status`, and reconcile
the local effect. The CLI never automatically retries the task under a new ID.

Core absent/disabled or missing native API routes is unavailable. The optional
judge, free model discovery and pointer are installation packages of the desktop;
their absence does not cause installation, provider login or a hosted fallback.

Intent creation accepts `{"context":{"kind":"document","app":"Word"},"keywords":"润色"}`.
These are user-provided hints, not proof that Word is open. Refine accepts the
current `revision`, appended `keywords` and optional rejected action IDs. Commit
accepts exactly `revision` and an offered `action_id`; retain the returned draft
for host/user review. No intent command captures a screen or submits a chat turn.
