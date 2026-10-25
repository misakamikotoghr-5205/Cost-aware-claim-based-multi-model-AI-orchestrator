# Native Hermes client extensions

Use the upstream Hermes Desktop for conversations, streaming, interactive
questions, approvals, terminal, Skills, MCP, profiles and provider sign-in.
`orchestrator-core` extends the official `DelegationProvider.route_task` seam;
it selects routes for **delegated child tasks**. It does not replace Hermes'
turn loop or intercept the primary chat. Ordinary local OAuth/API conversations
work without a cloud owner, fleet ledger, judge, or discovery package.

The native host remains pinned to the revision in
`integrations/hermes-client/upstream.json`. Upstream MIT copyright notices and
third-party notices must remain in redistributed builds.

## Three separately installed packages

| Package | Contents | Installation behavior |
| --- | --- | --- |
| `orchestrator-core` | Native delegation provider, shared deterministic scorer, authenticated REST settings, optional fleet controls and native Desktop settings page | Does not contain optional extensions, inference weights, or their network refresh entry points. |
| `local-judge` | Independent Python plugin, local Ollama adapter, setup/pull-job API and native Desktop settings page | Requires core; optional installation and native enablement. |
| `free-models` | Independent Python plugin, explicit catalog/inventory refresh, provenance-aware imports, persistent TTL cache and native Desktop settings page | Requires core; optional installation and native enablement. |

The existing optional `smart-pointer` package is also listed by core inventory,
but does not participate in model routing. Installation, native enablement and
activation for delegated routing are distinct states. Core verifies the actual
installed `plugin.yaml` identity and current profile's native
`plugins.enabled`/`plugins.disabled` state before loading an optional routing
backend. A configured flag cannot replace an installed package or override a
native disable. Uninstalling removes that package's functionality; core does
not recreate it or import a repository fallback.

The package installer verifies the archive, compatibility, hashes and paths,
and copies only that package into the selected Hermes home. Installation does
not silently enable Python plugins or start a worker. Enable through the native
plugin controls. On first installation or first Agent enablement, **restart the
corresponding local native backend and reconnect to that local profile**: the
running gateway mounts plugin REST routes at startup, so enabling Desktop alone
does not make newly installed `/settings` or catalog `/models` endpoints live.
The extension pages identify missing static entry routes and ask the user to
enable **Agent** on the native Plugins page, restart the corresponding local
backend and reconnect. A missing task, session or other dynamic resource retains
its original error and is not presented as a restart issue. No extension
automatically kills or restarts the backend; wait for active tasks to finish
before restarting it. Already running downloads/worker polls receive cancellation
at unload; an operation already executing may finish before shutdown. No background model
scan or worker starts on package import, page load, or installation.

## Local state and authentication

Every operation resolves the **current bound Hermes profile**. Settings and
caches live under its `plugin-data/<package>/`; workers and pull jobs are keyed
by the owning profile. Nothing caches the process launch home as the user home.
Native REST routes inherit Hermes' authentication, enablement and profile gate.
The dashboard manifests expose hidden REST backends; presentation lives in the
native Desktop SDK modules, not an embedded browser dashboard.

Settings reject provider `api_key`, `auth`, passwords, cookies and access/refresh
tokens. Provider credentials remain with the corresponding local or remote
Hermes profile. Scored routes contain an explicit provider/model, plus
optional nonsecret endpoint/label fields; plugin configuration never copies a
provider token into its own settings or fleet payload.

A fleet owner's token is referenced by a **profile-scoped secret variable name**
(`server.token_env`) and read through Hermes' `agent.secret_scope.get_secret`.
A paired node's separate transport grant is saved in an owner-only private file
under core state. Pair redemption returns only the node descriptor to the UI;
its token is never returned in settings or pairing responses. These transport
grants are not model-provider OAuth/API credentials.

Node controls must target the **local** native connection/profile through the
Desktop private-ticket API. Switching the foreground chat to a remote gateway
does not move local workspace permissions to that server. Worker status reports
`execution_location: this_backend_host` and its platform, so callers can identify
which host would execute work. Workspace roots stay local; only aliases and
allowed capability names are advertised.

## Configure deterministic delegation

Select the native delegation backend `orchestrator-core` and configure its
settings through the installed native page. Its `enabled` setting defaults to
false, retaining the inherited native route. With routing enabled, declare
`models`, trusted `routes` keyed by model ID, and actual quota observations in
`quota_states`. The core reuses the existing `ClientRouter` and `ScoringEngine`;
unknown quota, stale discovery and unapproved paid API candidates remain
ineligible. A discovered model cannot authorize a new execution route by itself:
the operator must configure its corresponding native route. Selected routes
explicitly set `fallback_providers: []` so an inherited paid fallback cannot
bypass admission. Scored profile routes are currently refused until their
internal fallback policy can be audited. When the optional router is inactive,
the provider preserves the original native delegation configuration (including
its model/provider pins), using the upstream default provider's semantics.

Optional routing activation is under
`extensions: {"local-judge": false, "free-models": false}`. These settings only
apply after installing and enabling the independent package. Disabling optional
routing leaves deterministic scoring available. Shared atomic quota reservation
belongs to the explicit fleet execution path; native chat is not made dependent
on a synthetic/shared ledger.

## Local judge setup and model installation

The complete local-judge extension includes connection checking, actual
candidate judgement, deterministic fallback, model download progress and
cancellation. **Ollama and model weights are not bundled.** When Ollama is absent,
the page provides the official `https://ollama.com/download` installation entry;
Ollama supports Windows, macOS and Linux. Install the OS-appropriate runtime,
start its loopback service, then choose an exact local model compatible with the
machine's memory and compute. No particular hardware performance is promised.

The setup check inventories `GET /api/tags` and reports whether the selected
model is installed. Only an explicit **Install selected local judge model**
action submits `POST /api/pull` to the configured loopback service. It runs as a
profile-bound background job, streams progress and exposes a job ID. Duplicate
button presses reuse the active job. No pull starts automatically, and this
repository's tests do not download model weights.

A pull is complete only after Ollama emits its final `success` record. A broken
or truncated stream stays failed/unknown. Cancellation stops local tracking;
Ollama may still complete server-side, so the response explicitly reports
unknown download outcome and asks the user to check installed models before
retrying. It never falsely claims that remote work was rolled back. After a
restart, inspect the installed inventory; jobs are not silently restarted.

Judgement calls only loopback Ollama `/api/generate` with the admitted candidate
shortlist. The optional judge only sees a small routing view: by default the
current task title (up to 512 characters), up to eight short requirement labels,
and the first three candidates in the existing score/expiry order. Only up to
four task-related capability dimensions and the existing deterministic totals
are included. Long titles keep both the beginning and end with explicit omission
markers; this is a partial summary, not a full task review. Dialogue history,
attachments, screenshots, expected outputs and candidate metadata are excluded.
The extension settings expose `max_task_chars` (128–2048) and `max_candidates`
(1–8). The UTF-8 prompt has an 8192-byte hard limit; Ollama is given a 4096-token
context window and at most 128 generated tokens. Bytes and characters are input
size limits, not a tokenizer-independent token count. An oversized summary keeps
the deterministic route rather than silently dropping further task constraints.
Full admission, quota checks, deterministic scoring and expiry policy still run
before this smaller view is constructed. If only one candidate remains in the
admitted score/expiry shortlist, routing skips judge inference and records that
fact in the receipt. Optional judging is reserved for a choice among candidates.
Setting `max_candidates` to 1 also skips inference and preserves the existing
deterministic score/expiry preference, even when the full shortlist is larger.

Generation uses an explicit JSON Schema whose `model_id` enum contains only the
visible candidates, and disables optional thinking for this short selection.
The response must also report completed generation with no runtime error or
length stop; the client independently checks the returned single-key JSON. A
schema constraint is not treated as a substitute for response validation.

Hosted/cloud model names and remote judge origins are rejected. A
fresh `/api/tags` weight-size check and `/api/show` local architecture check run
before **every** judgement; a renamed cloud alias is rejected before generation.
A malformed response, missing model, timeout or runtime failure retains the
existing deterministic choice. It does not use a cloud paid judge as fallback.

## Explicit free model discovery

The independent free-models extension refreshes only after an explicit action:

- Public `GET https://openrouter.ai/api/v1/models` requires no account key for
  catalog browsing. Only explicit zero input/output and other listed fee fields
  qualify. Dynamic `-1` pricing is unknown; paid or conditional pricing overrides
  are excluded. Date-only expiration fields use a conservative UTC cutoff.
- Loopback Ollama `GET /api/tags` inventories installed local models. Recognized
  remote/cloud tags are unavailable; no generation or weight download occurs.
- Token Scout Rust JSON query results, FMM paired snapshot/report, and Ollama JSON
  can also be imported with their actual observation timestamp. The old importer
  rules in [discovery.md](discovery.md) still apply: Scout cloud reports lack
  live/static provenance, FMM static/cached candidates are not current proof, and
  unknown prices never become free.

TTL and promotion deadlines are persistent. Reopening the application or reading
an export does not renew evidence. Updates/removals return events to the page;
malformed or failed catalogs do not erase the last valid baseline. Account
availability and real quota remain separate from zero public catalog pricing.
No discovery operation logs in, purchases credits, sends notifications, switches
native providers, or performs paid inference.

During development on 2026-10-24, a read-only public-catalog check returned 469
entries and 20 eligible zero-priced entries. This validates the feed adapter at
that time, not permanent availability, account quota or future pricing.

## REST endpoints

Paths below are relative to Hermes' authenticated `/api/plugins/<package>`;
Desktop SDK calls add the owning profile/connection. PUT settings replaces the
package settings object and performs validation before atomic persistence.

| Package | Endpoints |
| --- | --- |
| core | `GET/PUT /settings`, `POST /route`, `GET /extensions`, `POST /extensions/install`, `POST /extensions/{id}/remove` |
| core fleet | `GET /nodes`, `POST /nodes/select`, `POST /nodes/{id}/revoke`, `POST /pairing`, `POST /pairing/redeem`, `POST /tasks`, `GET /tasks/{id}` |
| core local node | `GET /node/status`, `POST /node/run_once`, `POST /worker/start`, `POST /worker/stop` |
| local-judge | `GET/PUT /settings`, `GET /setup`, `POST /test`, `POST /judge`, `POST /models/pull`, `GET /models/pull/{id}`, `POST /models/pull/{id}/cancel` |
| free-models | `GET/PUT /settings`, `POST /refresh`, `GET /models`, `POST /import` |

Pair creation takes `label`, an explicit `accounts` list (default empty), and
`ttl_seconds`. Redemption takes `code` and `label`; capabilities are generated
from the node's locally configured runtime/workspace aliases, never arbitrary
remote filesystem paths. Node execution requires explicit pairing, local runtime
configuration and a start/run action. A cloud outage does not replace the native
local conversation or reroute an uncertain task to another machine.

Node grants are bound to the normalized server URL and the exact owner/node
identity before any bearer is sent. Reconfiguration and re-pairing wait until
the previous worker/one-shot has stopped; stop is reported as pending while a
task is still running. Node subprocesses and their verifier inherit the owning
Hermes profile through an explicit environment scope, without mutating the
gateway's global process environment.

## Agent-to-node workflow bridge

The core registers the narrow `orchestrator_fleet` native toolset with operations
`list`, `select`, `submit`, `status` and `cancel`. It is off by default. Enable
`fleet_tools.enabled`, set its allowed tool names/action ceiling, configure the
owner transport secret selector, and enable the native toolset for the intended
session. Tool registration does not make a network request; each operation
proves owner access through the server's node listing before proceeding.

Submit requires an explicit target, stable task ID, advertised workspace alias,
relative directory and natural-language task. Requested tools/actions must fit
both the owner's saved policy and that node's advertised limits. No arbitrary
shell-command transport, absolute remote filesystem path, provider credential
or inferred tool permission is added. Node execution still requires its local
worker and runtime policy. Submit and cancel return native approval directives,
so Hermes' existing approval UI, allowlist and noninteractive fail-closed rules
remain authoritative. A lost response reports an unknown result, retaining the
same task ID for status checks rather than asking an agent to resubmit new work.

For a node using direct MCP tools, the cloud agent can supply an explicit
`tool_calls: [{name, arguments}]` plan. Its names must fit the requested tools,
both allowlists and the action budget; its finite JSON is limited to 64 KiB.
The node independently validates every call's arguments and workspace paths
before invoking any tool. Optional `task_card` metadata accepts only task type,
requirements and value level. This path needs **no local inference model**:
the cloud native agent supplies the plan, the paired node executes approved
local MCP calls, and the verifier returns the execution receipt. Natural tasks
without a plan remain available for nodes configured with their own Hermes
execution backend.

Validation: `python -m pytest -q tests/test_native_plugin.py` exercises installed
payload paths, native registration/REST sharing, real local HTTP judge calls,
model-pull stream simulation, source billing evidence, TTL, profile isolation and
secret rejection. Paid inference, subscription login and large model downloads
are not part of the automated tests.

The integration case `test_actual_fleet_tool_http_node_mcp_and_verified_receipt`
also runs against the real fleet transport: native tool schema → authenticated
HTTP queue → paired node → MCP HTTP tool writing a local file → independent
subprocess verification → `done` receipt read back by the agent tool. It skips
before the separate fleet-transport PR is present and runs after integration.
