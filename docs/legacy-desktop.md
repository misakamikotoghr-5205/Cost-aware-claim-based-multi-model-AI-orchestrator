# Unified desktop client (Windows / Ubuntu)

The desktop window is the product entry point. Hermes, DeepSeek Harness, local
Ollama and the cloud connection are configured in the same application. The
existing deterministic scorer stays built in. **Local judgment and free-model
discovery are independent optional extensions, both disabled by default.** No
extension, backend process or network request starts merely by opening the app.

## Start

Install Python 3.11+ and the repository's requirements, then run:

```bash
python -m pip install -r requirements.txt
python -m src.desktop
```

Windows Python setup must include Tcl/Tk. Ubuntu needs `python3-tk`,
`fonts-noto-cjk` for Chinese labels, and an active
graphical desktop session. The cloud host can remain headless. `launch-desktop.cmd`
and `launch-desktop.sh` are convenience launchers from a source checkout; these
are not standalone packaged executables. A Windows installer is not included.

Use `--config /path/to/client.json` to open a particular configuration. The
default configuration is under `~/.config/ai-orchestrator/client.json`. Backends
must already be installed and authenticated through their own official tools.
The client does not copy upstream projects into its source tree, install models,
collect account passwords, or turn subscriptions into model API credentials.

## Setup in the window

1. In **客户端与云端**, choose an existing workspace and an independent verifier
   argv, such as `["python", "-m", "pytest", "-q"]`. It runs in the task directory.
   A model's answer alone is never proof that a task is done.
2. In **执行器与额度**, add an exact model name and its backend. Hermes requires
   explicit toolsets; Harness uses a separately pinned headless model/profile.
   Ollama is a local text backend and does not itself operate desktop tools.
   Backend-specific limits and tested protocol revisions are in [executors.md](executors.md).
3. Subscription models require a named quota account and a current observation
   in the account's real unit. The basic form uses model `"*"` as the account-wide
   pool so multiple configured models share one subscription balance.
   **导入额度 JSON** accepts the sanitized schema in
   [quota-ledger.md](quota-ledger.md). It is not a raw CC Switch database importer
   or a claim that CC Switch has a portable export command. Unknown/stale quota
   pauses work. Unmeasured consumption remains unknown until reconciled.
4. In **任务**, enter a task and its directory inside the workspace. Tools are
   explicit per task; adding/selecting a model fills its configured toolsets.
   **预览路由** does not execute the task; **在本机执行** starts the configured
   backend. Keep the same task ID when checking a timeout or lost response.
   **新任务** generates a new ID for genuinely different work.
5. **结果回执** shows the selected model, attempts, quota accounting, verification
   evidence and final status. `unknown` is not success and is not automatically
   retried. The window keeps responding during work and prevents a second launch.

Advanced JSON settings are editable inside the window and applied to the same
client, including MCP tool schemas, argument validators, execution deadlines and
capability estimates. The form's initial capability values are operator estimates,
not vendor benchmarks. Basic settings preserve additional advanced fields.

## Optional extensions

**判断模型** uses an installed local Ollama model through a loopback endpoint.
It may select only already eligible candidates. A malformed answer or unavailable
model falls back to deterministic scoring; it cannot authorize paid APIs, bypass
the account reserve, or grant tools. Disabling it makes no judgment model calls.

**免费模型发现** imports explicitly selected Token Scout, Free Models Monitor or
Ollama JSON into a TTL cache. The app does not launch background scans. A free
candidate is not an installed executor or a guaranteed account allowance; bind
an installed backend and a real quota observation before execution. Disabling
discovery leaves manually configured models fully usable and does not read its
cache. See [discovery.md](discovery.md) for upstream evidence limits.

## Cloud connection

Enable the cloud connection in **客户端与云端**. Set the URL, token **environment
variable name**, `client_id` and `target` matching the server's assigned identity.
Use HTTPS, or explicitly enable private HTTP for a trusted Tailscale connection.
The token's value is read from the process environment and is not saved to settings.

**提交到云端** enqueues a task for the configured target; **领取云端任务** executes
one eligible task with authenticated claim/heartbeat/completion. The cloud queue
and shared ledger remain central; model judgment stays on the client. Use the
same GUI on each machine with separate identity, workspace and tool permissions.
Shared quota observations must be imported on the server that owns the ledger.
Server setup is in [cloud-local.md](cloud-local.md) and [cloud-queue.md](cloud-queue.md).

## Validation boundaries

The test suite exercises desktop configuration/services and a real local HTTP
queue/tool bridge with subprocess verification, without paid calls. This is a
source-based desktop release. Real Windows graphical execution, actual account
balances, installed upstream agent versions, Tailscale deployment and unrestricted
Computer Use still require deployment validation on the user's machines. The
MCP bridge supports configured JSON-RPC tool calls; it is not a universal MCP
session manager or an operating-system sandbox.
