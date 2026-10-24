# Execution adapters

`src.executors` adds an opt-in execution boundary to the existing scorer. Importing
it never starts a process, loads credentials, logs in, or calls a model. Tests use
fake protocol envelopes and small local Python executables; upstream services
were not installed, authenticated, or exercised against real models.

## Shared contract

```python
from src.executors import ExecutionRequest, ExecutionResult, Adapter

request = ExecutionRequest(
    task_id="T1", prompt="Explain the repository", model="operator-pinned-model",
    workdir="/srv/node/work/project", timeout=120, tools=[], max_actions=20,
    metadata={},
)
# result = adapter.execute(request)
```

An `Adapter` has `execute(request) -> ExecutionResult`. Results carry `status`
(`succeeded`, `failed`, `unknown`), `output`, `error`, `usage`, `events`, and
`retryable`. Success means the backend reported a completed execution; acceptance
of its answer still belongs to the verifier. Missing terminal events, empty model
answers, malformed streams, and timeouts never count as success. `unknown` always
has `retryable=False`: a timed-out tool may already have changed a remote system.
Only failures known to precede invocation (for example, a missing executable)
are marked retryable for CLI adapters. Any scheduler retry also needs its own
explicit task retry policy.

All adapters resolve the requested working directory under an operator-owned
`workspace_root`, reject symlink escapes, and require a positive timeout bounded
by `max_timeout` (default 600 seconds). They do not accept an executable, shell
fragment, endpoint, provider, or privilege grant from task metadata. A working
directory check is **not an OS sandbox**: run agent CLIs in a dedicated container
or restricted OS account with independently configured filesystem/network/tool
permissions. CLI hooks, Skills, plugin code, and provider fallbacks remain part
of that trusted operator configuration.

## Hermes

Source protocol inspected at
[`NousResearch/hermes-agent@4dcef215cfe41bd2d02c2d1dad6e257a18101216`](https://github.com/NousResearch/hermes-agent/tree/4dcef215cfe41bd2d02c2d1dad6e257a18101216),
particularly `hermes_cli/_parser.py` and `hermes_cli/stream_json.py`. The upstream
Python package reports version `0.0.0`, so pin the source revision rather than
assuming that version uniquely identifies this interface.

```python
from src.executors import HermesAdapter

adapter = HermesAdapter(
    "/srv/node/work", provider="operator-configured-provider",
    allowed_toolsets=["file"], billing_mode="subscription",
    command=("/opt/hermes/bin/hermes",),
)
# request.tools = ["file"]
```

The actual invocation is an argv list:

```text
hermes chat --query-file - --format stream-json --oneshot \
  --model MODEL --provider PROVIDER --toolsets file --max-turns 20
```

Prompt text goes through stdin, preserving quotes, backticks, and `$()` literally.
No shell is involved. Tool identifiers here are **Hermes toolsets**, not arbitrary
MCP tool names. Nonempty requested toolsets must be in `allowed_toolsets`;
wildcard grants and comma-composite grants are rejected. Empty requests fail
closed because upstream's absent/empty toolsets argument enables default tools.
The operator must audit tools that each configured toolset expands to.

The parser requires one final `type=result` record, `exit_code=0`, no error, and a
nonempty `text`. It maps the upstream `tokens` fields to `*_tokens` usage fields.
`--max-turns` limits tool-calling iterations, not the total number of parallel
tool calls. If the completed stream reports more calls than `max_actions`, the
outcome becomes `unknown`; that detects an overrun after execution. A strict
per-action gate requires the MCP adapter or an upstream sandbox/tool policy.

## DeepSeek Harness

Source protocol inspected at
[`deepseek-ai/deepseek-harness@22ddee3792402c3e70ec5f86a63c3e3e895a2f60`](https://github.com/deepseek-ai/deepseek-harness/tree/22ddee3792402c3e70ec5f86a63c3e3e895a2f60),
version `0.2.1-rc.3`. See `docs/cli-help.md`,
`packages/bundle/headless/src/json-stream.ts`, and its `index.ts`.
This preview interface can change. There is no assumed `--model`, `--max-turns`,
or subscription login flag on this inspected headless command.

```python
from src.executors import DeepSeekHarnessAdapter

adapter = DeepSeekHarnessAdapter(
    "/srv/node/work", configured_model="operator-pinned-model",
    configured_tools=[], profile="headless", patch_file="/etc/orchestrator/local-only.yml",
    billing_mode="local", command=("/opt/dsh/bin/dsh",),
)
```

```text
dsh --profile headless --patch /etc/orchestrator/local-only.yml \
  --json --approval-policy never -
```

The operator must configure the actual provider, model, tools, and sandbox in the
selected profile/patch. `configured_model` and `configured_tools` are assertions
about that audited composition; the adapter rejects requests that differ. They
do not rewrite upstream profiles or verify their runtime contents. For example,
the upstream patch entry `id: agent-default-model` owns `config.provider` and
`config.model`; consult the pinned configuration catalog for its provider setup.
Patches can contain executable configuration and must be operator owned.

The adapter never resumes a prior session implicitly or grants extra writable
directories. `--approval-policy never` denies unattended approval requests;
it does not enable a dangerous bypass. A final `type=final` event with
`reason.kind=completed` and nonempty `text` is required. Step usage is retained
as `usage.steps`; it is not converted to a fabricated subscription balance.

This upstream headless bundle disables interactive credential/account plugins
and uses launch-time provider credentials. A normal desktop subscription login
therefore cannot be assumed to work here. If only a paid API route is configured,
the adapter remains disabled unless paid API access is explicitly enabled.
There is no native action-count flag in the inspected headless CLI; use
operator tool restrictions plus the wall-clock deadline, or use MCP for strict
per-action limits. Reported action overruns become `unknown` as with Hermes.

## Billing and process lifecycle

CLI `billing_mode` defaults to `unknown`, which fails before process launch. An
operator may explicitly assert `local`, `free`, or `subscription` only after
checking the configured upstream route. `paid_api` additionally requires
`allow_paid_api=True` on the adapter constructor. Task metadata cannot override
these controls. A free/subscription assertion is not proof of a live entitlement:
upstream provider fallbacks must independently be disabled or constrained. These
bridges do not scrape tokens, wrap subscriptions as APIs, or purchase credits.

CLI stdout/stderr are spooled to temporary files and checked while the process
runs, avoiding unbounded `communicate()` pipe allocations. The combined capture
limit is 4 MiB, checked every 50 ms; captured memory is bounded, while very fast
writers can briefly exceed the disk threshold before termination. Exceeding it
returns `unknown`. POSIX deadlines terminate the owned process group, including
its descendants. On Windows the standard-library implementation kills the parent;
use a Job Object/container supervisor to ensure descendant cleanup. A CLI that
launches persistent background services requires independent OS supervision.

## Ollama

```python
from src.executors import OllamaAdapter

adapter = OllamaAdapter("/srv/node/work", base_url="http://127.0.0.1:11434")
```

Uses the native `POST /api/chat` shape (`model`, `messages`, `stream: false`),
requires `done: true` with a nonempty message, and preserves native usage fields
`prompt_eval_count`, `eval_count`, and `total_duration`. URLs must be loopback,
with no credentials or extra path; redirects are rejected. Models containing
`cloud` in their name are rejected. Configure the Ollama service with cloud
features disabled (`OLLAMA_NO_CLOUD=1`) and only local model weights; the adapter
cannot attest how a renamed model or a modified loopback service is implemented.

This adapter provides local inference. It rejects requested tools and never
pretends that text describing an action performed that action. Unexpected
returned tool calls are `unknown` until a separately authorized tool loop handles
them. An explicit tool plan uses the MCP adapter below.

## MCP direct plans

`MCPAdapter` accepts a connected transport bridge with the synchronous method
`call_tool(name, arguments, *, timeout) -> dict`, returning the standard MCP
`CallToolResult` wire dictionary. An application can bridge this method to the
official async MCP SDK. This module does not install servers, select endpoints,
perform pairing, or grant permissions automatically. The transport must enforce
the passed deadline, bound response memory, and avoid implicit write retries.

```python
from pathlib import Path
from src.executors import MCPAdapter

def validate_read(args: dict, workdir: Path) -> bool:
    if set(args) != {"path"} or not isinstance(args["path"], str):
        return False
    path = (workdir / args["path"]).resolve()
    return path.is_relative_to(workdir) and path.is_file()

adapter = MCPAdapter(
    "/srv/node/work", client=connected_mcp_bridge,
    tool_validators={"read_file": validate_read},
)
request.tools = ["read_file"]
request.metadata["tool_calls"] = [
    {"name": "read_file", "arguments": {"path": "README.md"}},
]
```

The adapter validates every call before executing the first. Tool names must be
in both the request and the local validator map; no missing validator implies
permission. Calls must fit `max_actions`, and each transport call receives the
remaining total deadline. Tool-specific validators must enforce argument/path
scope and approval policy; a `lambda *_: True` grants that tool unrestricted
arguments and should not be used for filesystem, shell, browser, or desktop
control. The MCP server still needs its own filesystem and action restrictions
to avoid symlink races or actions outside the client's scope.

MCP `isError=true` yields `failed` without automatic retry. Transport exceptions,
malformed results, responses exceeding 4 MiB, and late results yield `unknown`.
Text blocks are collected as output; non-text blocks need downstream handling.
A proper empty `content` list may acknowledge a completed tool operation, so MCP
success does not require prose output. Empty plans are rejected.

Run offline checks with `python -m pytest -q tests/test_executors.py`.
