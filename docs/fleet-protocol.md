# One cloud, multiple local nodes

The cloud stores task leases, node registrations, pairing hashes, and shared
quota. Each local node retains its own files, model authentication, tools,
workspaces, and execution receipts. This protocol does not accept provider API
keys, OAuth refresh tokens, or desktop credentials. A fleet bearer token grants
task transport access; it is never a model credential.

## Identities and provisioning

Start the existing cloud server with an owner credential in its token environment
JSON. Keep the actual bearer secret outside the repository:

```json
{
  "<at-least-24-character-random-owner-secret>": {
    "role": "owner",
    "owner_id": "personal",
    "accounts": ["subscription"]
  }
}
```

`make_server(..., registry=None)` defaults to a durable `NodeRegistry` in the
queue's SQLite file. A separately constructed registry can be supplied using
`registry=NodeRegistry(path)`. Owner credentials are provisioned by the server
operator. Node credentials can only be minted by redeeming an owner-issued code.
Changing a token configuration requires restarting this small server; paired
node revocation takes effect immediately without a restart.

Old string credentials and `{client_id,target,accounts}` credentials retain their
previous `legacy` behavior. They do not gain fleet administration privileges.
Legacy client IDs must not overlap fleet owner IDs; startup rejects such ambiguous
configuration. `cloud_tasks.client_id` remains the ownership field and contains
the owner ID for fleet tasks; `target` contains the exact node ID.

## Pairing and node API

All JSON responses have `Cache-Control: no-store`. All routes require a bearer
token except the exact pairing redemption route. Use HTTPS or a protected private
transport; never expose node/model control ports directly.

| Method and path | Role | JSON request | JSON response |
| --- | --- | --- | --- |
| `POST /v1/pairings` | owner | Optional `label`, `accounts`, `ttl_seconds` | `{pairing:{pairing_id,code,expires_at}}` |
| `POST /v1/pairings/redeem` | pairing code | `{code,label,capabilities}` | `{node,token}` |
| `GET /v1/nodes` | owner | none | `{nodes:[node,...]}` |
| `GET /v1/nodes/{node_id}` | owner | none | `{node}` |
| `POST /v1/nodes/{node_id}/revoke` | owner | `{}` | `{node}` |
| `POST /v1/nodes/self/heartbeat` | node | Optional `capabilities` | `{node}` |
| `POST /v1/nodes/select` | owner | Optional requirements below | `{node:node-or-null}` |

Codes have 256 bits of randomness, a maximum/default lifetime of 600 seconds,
and exactly one successful redemption. The default account grant is empty;
`accounts` must be a subset of the issuing owner token's configured accounts.
The registry stores SHA-256 hashes of codes and node tokens, never their plaintext.
The node token is returned only by successful redemption. A lost redemption
response cannot be replayed to recover that secret: list/revoke the orphaned node
and issue a new code. Invalid, consumed, and expired codes share the same HTTP
403 `pairing_refused` response. Labels are display names, not unique identities.

Every node response has `owner_id`, `node_id`, `label`, `capabilities`, `accounts`,
`created_at`, `last_seen_at`, `revoked_at`, and `online`. Times are UTC epoch
seconds. IDs are generated independently of names. No token hash is returned.
The node's account access is also intersected with the owner's currently
configured accounts, so removing an owner account grant takes effect after
configuration reload. Removing the owner credential disables its paired nodes.

Capabilities require exactly these fields:

```json
{
  "platform": "windows",
  "tools": ["git", "browser"],
  "skills": ["review"],
  "executors": ["hermes"],
  "models": ["qwen3:4b"],
  "workspace_aliases": ["default"],
  "max_actions": 20
}
```

Platform is `windows`, `linux`, `macos`, or `other`. List fields contain bounded,
unique identifiers. Workspace aliases permit only letters/digits/dot/hyphen/
underscore and must start with a letter or digit. Absolute paths and drive
prefixes are rejected. Extra fields, including credentials and actual workspace
paths, are rejected. A node can update only its own advertisement.

`nodes/select` accepts `tools`, `skills`, `executors`, `models` (required subsets),
`platform`, `workspace_alias`, and `max_actions` (required available capacity).
It deterministically chooses the lexicographically first node ID that belongs
to the owner, is online, and satisfies all requirements. No model is called.
Advertisements are scheduling hints; the local tool and workspace allowlists
remain authoritative and must be checked again before execution.

## Targeted tasks

The existing task endpoints and lease receipt format stay compatible. An owner
can submit to any of its online, non-revoked nodes using `POST /v1/tasks`:

```json
{
  "task_id": "globally-unique-uuid",
  "idempotency_key": "globally-unique-uuid",
  "payload": {
    "client_id": "personal",
    "target": "node-returned-by-pairing",
    "task": "Run the configured project checks",
    "workspace_alias": "default",
    "relative_dir": ".",
    "tools": ["git"],
    "max_actions": 10
  }
}
```

The new alias form resolves only on the target node. `relative_dir` is a relative
POSIX path with no parent components, backslashes, drive syntax, or control
characters. Do not send absolute local roots to the cloud. Existing absolute
`workdir` tasks remain transport-compatible for legacy clients; `workdir` cannot
be mixed with aliases. Native fleet clients should accept only aliases and map
them to locally configured roots before invoking the existing runtime.

Task IDs remain a global primary key for backward compatibility: generate UUIDs
for independent tasks. Reusing a task ID across owners causes a conflict, never
access to another owner's task. Payloads and idempotency keys cannot change on
replay. Claim request IDs are additionally scoped to the node internally, so two
nodes may use the same local reconnect ID without colliding.

An owner may get or cancel any of its tasks. Nodes may only claim, get,
heartbeat, complete, or cancel tasks assigned to their exact node ID. Owner
tokens cannot claim or complete tasks. Node tokens cannot submit tasks or manage
other nodes. A different owner's node/task lookup returns 404. Targeted dispatch
checks advertised tools, skills, workspace alias, executor (when provided), and
action limit; unavailable/incompatible targets return 409. A capability change
is rechecked at claim time. The final local execution checks remain mandatory.

## Presence, revocation, and recovery

Send a node presence heartbeat more frequently than every 90 seconds (the default
online TTL), including while executing a long task. Task lease heartbeats are a
separate endpoint and do not replace presence heartbeats. The registry accepts
a configurable `online_seconds` when constructed explicitly.

An offline node is excluded from selection and dispatch; it must send a presence
heartbeat before other node operations resume. Observing an offline node fences
its running tasks to UNKNOWN and invalidates its unfinished quota reservations.
Queued tasks remain assigned to that node. Reconnection reuses the durable claim
request ID and receives the original terminal/UNKNOWN record; it never launches
the task a second time. Lease expiration follows the same conservative rule.

Revocation immediately rejects the node's future cloud calls, cancels its queued
tasks, fences running tasks to UNKNOWN, and freezes pools with its unfinished
reservations. Local effects already running cannot be remotely undone by this
transport. The UI must not report remote revocation as proof that a subprocess,
mouse action, or external request has stopped.

Offline cleanup runs on cloud API activity, not on a separate background timer.
An idle server computes presence from timestamps when next queried. The HTTP
server serializes authorization with revocation in its process; run one server
process owning these database files. Do not deploy independent copies of the
queue/registry/ledger and expect shared coordination.

## Shared quota ownership

Fleet HTTP account names are logical names such as `subscription`. Physical
ledger keys use `quota_account(owner_id, logical_account)` from
`src.node_registry`; import trusted observations on the cloud host under that
key. Two owners with the same logical account name have independent pools.
The HTTP result restores the logical account name for clients.

```python
from src.node_registry import quota_account
from src.quota_ledger import QuotaLedger

ledger = QuotaLedger("cloud-quota.sqlite")
ledger.set_quota(quota_account("personal", "subscription"), 10,
                 unit="requests", capacity=10, model="*")
```

Use `requests` only when the upstream provider actually reports that unit. Never
convert subscription percentages into token allowances. See
[quota-ledger.md](quota-ledger.md) for observation freshness and safe accounting.

Reservation namespaces distinguish owner, node, and local reservation ID. The
namespace uses a bounded SHA-256 digest of the unambiguous JSON pair
`[owner_id,node_id]`; the local reservation ID is appended. An owner execution
uses `[owner_id,null]`. Node IDs and owner IDs containing delimiters cannot alias
another actor. Legacy reservation namespaces remain unchanged.

Node grants are the subset selected by the owner during pairing; supplying a
different account name in the request cannot expand them. UNKNOWN consumption
freezes the whole pool until explicit reconciliation. Revocation/presence loss
uses `QuotaLedger.invalidate_reservations(prefix)` to mark only unfinished holds
for that actor UNKNOWN; SETTLED consumption stays settled and idempotent. An
administrator reconciles revoked-node reservations directly on the ledger using
provider evidence. No cloud request may assert `confirmed_unused` to bypass this.
