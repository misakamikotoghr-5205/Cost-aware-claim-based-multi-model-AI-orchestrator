# One client, optional local extensions

The user entry point is the Windows/Ubuntu desktop client:

```bash
python -m pip install -r requirements.txt
python -m src.desktop
```

The desktop and advanced CLI share `src.runtime_cli.Runtime`, one client JSON
configuration, executor adapters, routing policy, quota accounting and receipts.
The free-model discovery and local judging model are **optional extensions**.
Both are disabled by default. You can configure subscription executors or your
own local models without installing either extension. The original
`python -m src.cli` remains the deterministic offline planner.

## What runs where

The client owns model selection, optional Ollama judging, workspace validation,
execution and verification. The cloud service owns authenticated task delivery,
leases and one SQLite quota ledger. The cloud service never runs a judge model,
accepts arbitrary shell commands from a task, or turns subscription logins into
model APIs. Hermes and DeepSeek Harness run through their native CLIs, using
operator-installed tools and their own authentication.

This release supplies a single-client functional path. A Linux Tk 8.6 GUI
smoke test under a virtual X display created/switched all five tabs and exercised
backend add/remove plus disabled extension defaults without network calls.
That does not establish Windows/Ubuntu deployment acceptance, live subscription
login, or successful real desktop control. Hermes, Harness, CC Switch, Token Scout and Free Models Monitor are
external dependencies; they are not vendored or installed automatically.

## One local configuration

See [`examples/cloud-local/client.json`](../examples/cloud-local/client.json).
Edit the workspace and model names to match your machine. Paths are interpreted
from the launching process's directory; use absolute paths for deployed clients.
The sample uses local Ollama inference, an operator-declared model card and a
minimal nonempty-output verifier. Replace the verifier with a project-specific
check such as a test command before using the output as quality evidence.

A configuration contains:

| Field | Purpose |
| --- | --- |
| `workspace_root` | Root containing every permitted task working directory |
| `models` | Operator-declared model cards with provider, executor, billing mode and capabilities |
| `adapters` | Locally owned executor configuration; tasks cannot override it |
| `allowed_tools`, `max_actions` | Node grants and maximum task action budget |
| `verification.command` | Explicit local argv, run without a shell in the task directory |
| `routing` | Policy thresholds; `allow_paid_api` defaults to false |
| `model_accounts` | Exact model → account and reservation quantity mapping |
| `quota_db` | Standalone ledger; do not copy this file across clients as a shared ledger |
| `server` | Cloud origin, token environment variable name and optional private HTTP opt-in |
| `target` | This node's identity, bound to its server token |
| `extensions.discovery` | Optional `enabled`, `cache`, `ttl_seconds`, `capability_profiles`, `executor_overrides` |
| `extensions.judge` | Optional `enabled` and `config` for loopback Ollama |

`models` are explicit operator assertions, not live discovery observations.
Cards with an explicit `observed_at` retain that age and can become stale; cards
without one are treated as current declarations. Known offer expiry still
applies. Subscription execution always requires an independently fresh observed
quota snapshot, including units and capacity, from the ledger. A model being
configured does not prove the CLI is installed, logged in or has subscription
entitlement; the adapter checks the actual execution result.

The advanced CLI can initialize configuration and plan without effects:

```bash
python -m src.runtime_cli init --config client.json --workspace /absolute/workspace
python -m src.runtime_cli route --config client.json --task task.json
python -m src.runtime_cli run --config client.json --task task.json
# Execution requires explicit opt-in:
python -m src.runtime_cli run --config client.json --task task.json --execute
```

A task file is `{ "task_id": "unique-id", "payload": { ... } }`. Cloud payloads
require `client_id`, `target`, `task` text, absolute `workdir`, `tools` and positive
`max_actions`. `metadata.task_card` can supply the existing TaskCard's type,
requirements and value level. A task cannot supply commands, verifier commands,
server URLs, credentials or new local permissions.

### Executors

See [executor details](executors.md) for tested CLI contracts and upstream
compatibility limits. Adapter keys match a model card's `executor`:

- `hermes`: `provider`, explicit `allowed_toolsets`, `command` argv and
  `billing_mode`. Native Hermes toolsets must be named explicitly.
- `deepseek`: `configured_model`, `configured_tools`, `profile`, `command` argv,
  optional `patch_file` and `billing_mode`. The configured native profile owns
  model selection; arbitrary model switching is rejected.
- `ollama`: loopback `base_url`; inference only, without implicit tools.
- `mcp`: a restricted loopback JSON-RPC `tools/call` bridge with operator-defined
  tools, `allowed_arguments`, `required_arguments`, `path_arguments` and optional
  `constant_arguments`. This bridge is not a general MCP session client.

Each MCP call must fit the task grant, local tool configuration and action
budget. Path arguments resolve beneath the task's working directory, including
symlink resolution. Unknown argument keys are rejected. The tool server must
also enforce its own permissions. This is **not an operating-system sandbox**:
native CLIs and verifier subprocesses run with the user's OS permissions. Use a
separate account/container and narrowly configured upstream tools for untrusted
work. A task's action budget is hard-enforced for explicit MCP plans; CLI tools
have the upstream limitations described in the adapter documentation.

## Cloud connection and accounting

Run the service on the machine that owns both SQLite files. Use a private
transport such as a VPN/TLS reverse proxy. Loopback is the default bind address;
non-loopback plain HTTP clients require explicit `server.allow_http: true`.
Tokens belong in environment variables, never source-controlled JSON files.

Set `ORCHESTRATOR_TOKENS` to a JSON token map through your local secret manager.
Each token must be at least 24 characters. A token record binds `client_id`,
`target` and an `accounts` allowlist, for example
`{"<long-random-token>":{"client_id":"ubuntu-node","target":"ubuntu-node","accounts":["my-subscription"]}}`.
The placeholder is not a usable credential.

```bash
python -m src.runtime_cli server --db queue.sqlite --quota-db quota.sqlite
```

Configure the client's `server` as
`{"url":"https://private-cloud.example","token_env":"ORCHESTRATOR_NODE_TOKEN"}`
and `target` to the token-bound target. Set that token in the named environment
variable on the client. Submission, status and node execution use the same config:

```bash
python -m src.runtime_cli submit --config client.json --task task.json
python -m src.runtime_cli status --config client.json --task-id unique-id
python -m src.runtime_cli node --config client.json --once --execute
# Without --once, poll for work continuously.
```

Before nonlocal execution, import a real observed quota on the ledger host with
`python -m src.runtime_cli quota-set --db quota.sqlite --input observed-quota.json`.
The JSON uses `QuotaLedger.set_quota` parameters: `account`, `remaining`, `unit`,
`model`, optional `capacity`, `observed_at`, `reset_at`, `max_age_seconds` and
`source`. CC Switch/normalized import APIs are documented in
[quota-ledger.md](quota-ledger.md). No unavailable provider-specific balance is
invented; unauditable/stale balances pause execution.

`model_accounts[model_id]` specifies `account` and `reservation_amount` in the
ledger's unit. Map `usage_key` to a trusted measured executor usage field and explicitly set
`usage_unit` equal to the ledger unit. An estimate is never settled as measured
consumption. Native Ollama requests and explicit MCP tool calls can be counted
in `requests` units; this does not count hidden provider inference calls inside
an agent. The explicit request plan must fit its reserved bound. Without trusted
same-unit consumption data, or if measured usage exceeds the bound, the result
is `unknown` and the entire pool freezes pending reconciliation. Models sharing one
subscription must declare `metadata.quota_pool_model: "*"` (or one other explicit
shared pool name) and reserve that same pool, rather than copying the balance into
independent model rows. Routing snapshots are admission hints; the ledger's
transactional reservation is the execution gate.

## Receipts, reconnects and failures

The full path is claim → local route → quota reserve → execute → independent
verifier → durable receipt → authenticated completion. `done` requires a passed
verifier and nonempty evidence. Missing/failed verification is a failed delivery.
A successful tool call alone does not count as verified task completion.

The client journals a claim request ID before network access. Reconnecting after
a lost claim response reuses that ID and cannot consume the next queued task.
Each execution ID also has an exclusively created local receipt before dispatch.
Repeated IDs return the existing receipt, and different content under one ID is
rejected. Keep the receipt directory private and durable; deleting it removes one
layer of local replay protection. Cloud tasks and quota reservations supply
additional durable identity checks. A kernel-held node lock allows one actively polling process per receipt
directory, preventing duplicate pollers from invalidating an in-flight claim.

A timeout, dropped tool response, crashed dispatch or expired lease becomes
`unknown`; it is never automatically rerun. Reserved quota stays held and the entire affected pool blocks pending
explicit reconciliation. A lost completion response can be reconciled by the
same claim ID without running the tool again. Heartbeats renew a lease during
execution; a lost heartbeat prevents the client from claiming a verified cloud
completion, but cannot undo a tool effect already in flight.

Fallback is bounded to at most three models and requires all of:
`allow_fallback: true` in local configuration, `metadata.safe_to_retry: true` on
the task, and a definite retryable adapter failure. Unknown results and failed
verification after successful execution never trigger fallback.

Routing snapshots are rechecked before execution; the client also passes the exact policy floor in ledger units into the atomic
reservation. Concurrent reservations cannot cross that floor. Broader
coordinated multi-client scheduling and comprehensive crash/OS sandbox hardening
remain stability work.

## Optional extensions

Free model discovery can be enabled separately from a judging model. Disabling
discovery means the client does not open its cache. Disabling the judge means it
does not create an Ollama judge or make any judge request.

For explicit import, use `discovery --source token-scout|free-models-monitor|ollama
--input FILE --cache FILE`; Free Models Monitor also requires `--report FILE`.
The default observation time is the input file's modification time (the older
snapshot/report time for FMM), so reimporting an old file does not renew it.
`--observed-at` is available for an independently known observation timestamp.
Use `--source show --cache FILE` to inspect admitted cached models. See
[discovery.md](discovery.md) for provenance, unavailable/static results and TTLs.
This command imports evidence; it does not silently install or run third-party
model discovery tools. Discovery sources do not prove capability rankings. Add
operator-owned `capability_profiles: {"model-id": {"implementation": 0.7}}`
and, where necessary, `executor_overrides: {"model-id": "your-adapter-key"}`
inside the discovery extension configuration. Empty/unknown capability cards
remain ineligible. These profiles are labeled operator assertions, not benchmarks.
Nonlocal discovered models also need explicit fresh quota and an account mapping.

To enable client-side judgement, set
`extensions.judge = {"enabled":true,"config":{"base_url":"http://127.0.0.1:11434","model":"qwen3:4b","timeout":5}}`.
The model must already be installed in local Ollama. The judge can only choose
among models admitted by billing, freshness, expiry, quota and tool policy.
Malformed output/timeouts retain deterministic scoring. Cloud model tags,
non-loopback origins and HTTP redirects are rejected. This extension never
runs a judging model in the cloud.

## Reproducible verification

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
python examples/cloud-local/offline_demo.py
```

The demo starts actual loopback cloud and tool servers, reserves a fixture quota,
uses a restricted tool to write an artifact, checks that artifact in a separate
Python process and returns a cloud receipt. It clearly reports `fixture_only`;
it is not evidence of a real subscription, real model quality or GUI control.
Use `--workspace /new/empty/directory` to retain its files. Reuse of a completed
workspace/ID is intentionally protected by receipts and reservations.

The integration tests additionally exercise lost claim/completion responses,
a lost response after a committed tool effect, quota holds, duplicate task IDs,
path escape rejection and disabled extensions. CI targets Python 3.11/3.12 on
Ubuntu and Windows. Actual Windows GUI operation, live upstream CLIs, CC Switch
account data, paid/free provider entitlements, and Computer Use through a real
paired desktop still require operator deployment acceptance.
