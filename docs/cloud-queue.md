# Cloud queue and local nodes

The cloud service stores and dispatches task envelopes and optionally hosts the
shared quota ledger. It does not load a judgment model, run a planner, execute a
shell, operate a desktop, or turn subscription logins into a model API. Local
nodes perform routing and enforce their configured work directory, tool and
action limits before invoking approved adapters. `tools` describes permitted
protocol capabilities; it does not grant an operating system permission.

## Deployment

Use Python's standard library HTTP server on loopback behind a private TLS
transport, such as a configured VPN/reverse proxy. The default bind is
`127.0.0.1`; plain HTTP must not carry bearer tokens over an untrusted network.
Provision a different random bearer token for each node (at least 24 printable
characters), and give each token only its own node and quota accounts. The
server never discovers login cookies or prints bearer tokens. Keep the token
mapping in a caller-provisioned environment variable, not in source control:

```json
{
  "REPLACE_WITH_A_RANDOM_SECRET_AT_LEAST_24_CHARS": {
    "client_id": "windows-node",
    "target": "windows-node",
    "accounts": ["codex-subscription"]
  }
}
```

The variable name is `ORCHESTRATOR_CLOUD_TOKENS`. A string identity instead of
an object binds client and target to that string and permits no quota accounts.
An administrator provisions authoritative observations through the ledger
importer; nodes cannot inflate their own quota through this HTTP API.

```python
from src.cloud_queue import CloudQueue
from src.cloud_server import make_server, tokens_from_environment
from src.quota_ledger import QuotaLedger

queue = CloudQueue("/private/state/tasks.sqlite")
ledger = QuotaLedger("/private/state/quota.sqlite")
server = make_server("127.0.0.1", 8765, queue, tokens_from_environment(), ledger)
try:
    server.serve_forever()
finally:
    server.server_close()
```

Create the state directory with access restricted to the service user. Protect
and back up both SQLite files. SQLite coordinates processes on the same host;
do not copy the live ledger to clients or put it on a network share. The stdlib
HTTP transport is a minimal service intended for a trusted private deployment;
its socket timeout and body limits do not replace a reverse proxy's connection
and rate limits.

## Task protocol

All requests use `Authorization: Bearer <node-token>`. POST requests require
`Content-Type: application/json` and a single `Content-Length`, with at most
1 MiB body. A task envelope is limited to 512 KiB. Unknown fields, nonfinite
JSON numbers, parent path traversal and malformed constraints are rejected.
Tokens are bound to both `client_id` and `target`.

`POST /v1/tasks`:

```json
{
  "task_id": "review-42",
  "idempotency_key": "review-42",
  "payload": {
    "client_id": "windows-node",
    "target": "windows-node",
    "task": "Inspect the checked-out changes and run the configured verifier",
    "workdir": "C:\\work\\project",
    "tools": ["git", "browser"],
    "max_actions": 20,
    "skills": ["github-review"],
    "require_approval_for": ["merge", "delete", "payment"],
    "metadata": {"task_card": {"type": "code_review"}}
  }
}
```

Required payload fields are `client_id`, `target`, `task`, `workdir`, `tools`,
and `max_actions` (integer 1–1000). Optional fields are `executor`, `skills`,
`escalate_to`, `require_approval_for` and `metadata`. `executor` is an adapter
identifier, never an argv or command. Work directories must be absolute Windows
or POSIX paths; the node additionally restricts them to its local workspace.
The local tool allowlist and approval policy remain authoritative even if an
envelope asks for more permissions.

Repeating the same task ID, idempotency key and canonical payload returns the
original task. Reusing either identifier for different content conflicts. IDs
are globally unique; keys are scoped to a client.

| Route | Body | Behavior |
| --- | --- | --- |
| `POST /v1/claim` | `{"request_id":"poll-unique-id","lease_seconds":60}` | Atomically claim the next task for this token's node. |
| `GET /v1/tasks/{id}` | — | Read/reconnect to a task belonging to this client and target. |
| `POST /v1/tasks/{id}/heartbeat` | `{"lease_token":"...","lease_seconds":60}` | Extend an active lease, from 1 to 3600 seconds. |
| `POST /v1/tasks/{id}/complete` | `{"lease_token":"...","status":"done","result":{"verified":true,"evidence":["configured verifier exited 0"]}}` | Acknowledge an outcome under the current lease. |
| `POST /v1/tasks/{id}/cancel` | `{}` | Cancel queued work; mark a claimed task unknown. |

Successful task responses are `{"task":{...}}`, or `{"task":null}` when there
is no claimable task. A task includes `task_id`, `payload`, `status`,
`lease_token`, monotonically increasing `fence`, `lease_expires_at` (UTC epoch
seconds), and `result`. Treat the lease token as an execution capability and
do not log it. A claim replay with the same `request_id` returns the original
claim, including a terminal or unknown task. Only a task in `claimed` state
with a live lease can execute. Retrying an identical terminal acknowledgement
is safe; a changed terminal result conflicts.

The node must use a durable journal and a stable claim request ID to distinguish
resuming an existing claim from beginning new execution. GET/reconnect does not
authorize repeating side effects. `done` requires `verified: true` and nonempty
verification evidence from the configured local verifier. The cloud records the
node's authenticated receipt; it does not independently attest that arbitrary
evidence text is truthful. Adapter success without local verification must be
reported as `failed` or `unknown`.

## Failure and recovery

State transitions are deliberately conservative:

```text
queued -> claimed -> done | failed | unknown
queued -> cancelled
claimed -- expired lease or cancellation --> unknown
```

An expired claim is marked `unknown` on the next queue operation. It is never
requeued automatically because a disconnected node may already have changed
files, clicked a button, or consumed quota. A stale worker cannot complete or
renew that lease. Cancelling a running task does not promise to stop its local
process or undo its effects. Nodes must monitor cancellation/heartbeat failures
and stop future actions. Inspect the node journal and real external outcome
before manually creating a replacement task with a new ID. There is no HTTP
operation that silently resets unknown work.

For quota, reserve on the shared cloud ledger before external execution.
An execution that becomes uncertain retains its reservation and must call
`/v1/quota/unknown` when possible. A node killed before it can do so leaves a
`RESERVED` hold, which is also retained. Do not release such holds on a timer.
Reconcile them with an authoritative provider observation and operator evidence.

## Quota API

Quota responses are `{"quota":{...}}`. Each token has an explicit account
allowlist. Client reservation IDs are length-prefixed with their client ID in
the server, preventing another client from settling or releasing the same ID.
Send the original local ID on each request, not the returned internal ID.

| Route | Request |
| --- | --- |
| `GET /v1/quota?account=A&model=M` | Snapshot for an allowed account; model defaults to `*`. |
| `POST /v1/quota/reserve` | `{"reservation_id":"r1","account":"A","amount":1,"unit":"requests","model":"*"}` |
| `POST /v1/quota/settle` | `{"reservation_id":"r1","actual_amount":1}` |
| `POST /v1/quota/release` | `{"reservation_id":"r1"}` for known unused reservations. |
| `POST /v1/quota/unknown` | `{"reservation_id":"r1"}`; `mark_unknown` is an alias. |

A reservation replay is an accounting retry, never permission to execute twice.
Unknown holds cannot be released by this API; explicit reconciliation is an
administrator operation on the ledger. Freshness, amounts, units and atomic
availability are enforced by `QuotaLedger`. Missing/unsafe quota fails closed.
HTTP 400 means invalid input; 401 missing/invalid token; 403 wrong identity,
target or account; 404 an inaccessible task/route; 409 a lease/idempotency or
quota conflict; 503 an unavailable ledger/service. Error bodies use
`{"error":{"code":"...","message":"..."}}` and contain no task content or
credential values.

Validate locally with `python -m pytest -q tests/test_cloud_queue.py`. These tests
exercise persistence, concurrent claims, node isolation, request replay, stale
workers, cancellation, false completion prevention and authenticated HTTP routes.
They do not imply that a Windows desktop, external MCP server or subscription
provider has been deployed or verified.
