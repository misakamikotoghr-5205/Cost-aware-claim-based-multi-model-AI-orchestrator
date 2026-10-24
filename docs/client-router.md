# Client routing

The Windows/Ubuntu desktop client can route from operator-configured model cards
without any discovery service or judge. Free-model discovery and the local judge
are optional extensions: disabling them makes no discovery or judge requests.
Cloud services distribute tasks and account state; model selection runs on the
client using the existing `ScoringEngine`. Scoring makes zero model calls.

`ClientRouter` takes a static `Registry`, a `RoutingPolicy`, and optionally an
`OllamaLocalJudge`. It builds a temporary registry from the runtime cards and
passes those cards to the existing scorer. Static capability/quota sample files
are never treated as live account evidence. The static registry is unchanged.

```python
from src.client_router import ClientRouter
from src.discovery import ModelCard
from src.task_card import TaskCard
import time

task = TaskCard("task-1", "Explain the local test result", "general")
cards = [ModelCard(
    model_id="qwen3:4b", provider="ollama", executor="ollama",
    capabilities={"reasoning": 0.7, "final_writing": 0.7},
    billing_mode="local", available=True, observed_at=time.time(),
)]
receipt = ClientRouter().route(task, cards)
assert receipt.selected.model_id == "qwen3:4b"
print(receipt.to_dict())
```

## Admission policy

Every card must be available, recently observed (default 15 minutes), and within
its free-offer deadline. Paid API calls are disabled by default. Routing opt-in
with `RoutingPolicy(allow_paid_api=True)` does not authorize an executor: its own
operator configuration must independently allow that billing mode. Unknown
billing modes cannot run.

Non-local cards need known quota. Free cards may carry a normalized
`quota_remaining` fraction, observed within the quota freshness window (default
5 minutes); an explicit live snapshot overrides that estimate. Subscription
cards always require a live snapshot and an explicit `metadata.account`.
Local cards do not consume a provider quota.

`route(task, cards, *, available_tools=(), required_tools=(), quota_states=None,
quota_costs=None, now=None)` checks the task's explicit `required_tools` and each
card's `metadata.required_tools` against the client's allowed tools. TaskCard's
capability requirements remain scoring dimensions; they do not grant tools.
`execute()` additionally checks every `ExecutionRequest.tools` entry. Ambiguous
duplicate model IDs are rejected; a caller must choose a unique execution-model
identity before routing across multiple providers.

`quota_states` maps exact execution model IDs to ledger snapshots:

```json
{
  "vendor-code-model": {
    "account": "my-subscription",
    "model": "*",
    "unit": "requests",
    "available": 80,
    "capacity": 100,
    "observed_at": 2000000000,
    "reset_at": 2000003600,
    "max_age_seconds": 300,
    "fresh": true,
    "status": "ready"
  }
}
```

The matching card has `metadata={"account": "my-subscription",
"quota_pool_model": "*"}`. The explicit pool mapping is useful when several
models share one subscription. The ledger must reserve from this same account
and pool, rather than copying the account balance into separate model pools.
Without `quota_pool_model`, snapshot.model must equal the card's model_id.
There is no automatic account or wildcard fallback.

Snapshot `available` is net of existing holds. `quota_costs[model_id]` is the
requested reservation quantity in exactly `snapshot.unit`. The router subtracts
that cost before checking the reserve. It normalizes using an explicit positive
`capacity`, or a `fraction` unit with capacity 1. Unknown capacity, missing quota,
account mismatches, stale/blocked state, future observation times, and expired
reset times all pause subscription routing. Requests and tokens are never
silently treated as normalized fractions.

Subscriptions retain at least 20% headroom by default, or the larger reserve in
the existing registry. Strong models (a capability at least 0.85) retain at least
30% for trivial/normal tasks; important and critical tasks may use that additional
headroom while retaining the subscription minimum. These values are configurable
in `RoutingPolicy`.

Among models within 0.15 of the top existing score, the router prefers the first
quota to expire or reset within 24 hours. It does not send an unsuitable low-score
model ahead merely because a free offer is ending. Receipts retain the scorer's
original score breakdown alongside the ordered execution candidates and rejection
reasons.

## Optional local judge

```python
from src.client_router import ClientRouter, OllamaLocalJudge

router = ClientRouter(judge=OllamaLocalJudge(
    base_url="http://127.0.0.1:11434", model="qwen3:4b", timeout=5,
))
```

Enable this only in local operator configuration. The model must already be
available in Ollama. The judge accepts only loopback origins, rejects cloud model
tags, disables HTTP proxies and redirects, limits the response size, and requests
JSON. It receives task metadata and admitted candidates, without credentials or
files. It may select only a candidate within the score shortlist; when expiring
quota is prioritized it may choose only within that expiring shortlist. An
unavailable server, timeout, malformed response or invented model leaves the
deterministic selection in place. The cloud does not invoke a judge.

## Execution and receipts

`execute(task, cards, request, adapters, *, max_attempts=2, **route_options)` is a
convenience runner for **local models only**. `adapters` maps card.executor to
an `Adapter`. The request's execution model is replaced with the selected model
ID. An adapter's own configured billing and tool restrictions still apply.

All non-local models, including hosted free quotas, must use `route()` followed by a successful atomic ledger
reservation, adapter execution, settlement and independent verification. Routing
is a snapshot check and cannot make a distributed reservation. Use the integrated
Runtime node workflow for that sequence; convenience `execute()` refuses to
invoke a non-local adapter.

Failover is bounded to 1–10 attempts (default 2) and requires **all** of:

- The operator's request metadata explicitly sets `safe_to_retry` to `true`.
- The prior adapter reports `status="failed"` and `retryable=true`.
- Another model still passes runtime policy.

Unknown outcomes and adapter exceptions never replay automatically, because a
tool operation may already have taken effect. Ordinary failed results without
retry authorization stop. The runner never increases permissions or enables
paid APIs during fallback. Each attempt records the actual model, executor,
status, retryability, output, error and usage. Adapter success means execution
completed; verification remains a separate acceptance step.

Run the offline suite after installing test dependencies:

```sh
python -m pytest -q tests/test_client_router.py
```

These tests use fake adapters and fake judge responses; no model, subscription,
browser or computer action is invoked.
