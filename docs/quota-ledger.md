# Shared quota ledger

`src/quota_ledger.py` uses SQLite `BEGIN IMMEDIATE` transactions to serialize
reservations, settlements, and observations across processes. Run it on the
cloud server with one durable database file; clients use the cloud API. Separate
database copies on clients do not coordinate quota. No model is called by this
module. No credits are purchased and no credentials are inspected.

```python
from src.quota_ledger import QuotaLedger

ledger = QuotaLedger("quota.sqlite")
ledger.set_quota("owner-codex", 10, unit="requests", capacity=10)
reservation = ledger.reserve("client-a:task-123", "owner-codex", 2,
                             unit="requests")
if not reservation["replayed"]:
    # Execute once with an enforced maximum of two requests.
    # On uncertain outcome: ledger.mark_unknown(reservation["reservation_id"])
    ledger.settle(reservation["reservation_id"], actual_amount=1)
```

Do not put that example's `requests` unit on an actual subscription unless its
provider really reports an enforceable request budget. A subscription percentage
does not imply a token count or a per-request bound. The adapter must have a
compatible, enforceable maximum; otherwise defer execution or preserve UNKNOWN.

## Contract and recovery

- `set_quota(account, remaining, unit='requests', *, model='*', capacity=None,
  observed_at=None, reset_at=None, max_age_seconds=300, source='manual')` imports
  a trusted observation. Times are UTC epoch seconds. Capacity is optional and in
  the same explicit unit. Units are immutable per pool and never converted.
- `reserve(reservation_id, account, amount, *, model='*', unit=None,
  minimum_remaining=0)` atomically checks freshness and
  `remaining - held - amount >= minimum_remaining`. The floor is in the same
  explicit pool unit, is persisted with the reservation, and is part of its
  idempotency parameters. Routing must pass the effective subscription/strong
  reserve floor to enforce policy across concurrent clients; a snapshot check
  alone is insufficient. Unknown pools, negative/nonfinite
  values, stale/reset observations, and overspending fail closed. Amounts use
  decimal arithmetic with up to nine decimal places, not binary float sums.
- A repeated ID with the same parameters returns the original state with
  `replayed=True`; different parameters raise `ReservationConflict`. A replay
  must never launch another external execution, including a replay of RESERVED.
  IDs stay consumed after settlement/cancellation. A durable task claim still
  needs to guard execution; reservation idempotency alone is not exactly-once
  tool execution.
- `settle(id, actual_amount)` debits actual consumption once and frees the unused
  part. A different repeated amount is a conflict. The normal maximum is the
  reserved amount. An actual overrun records UNKNOWN, holds at least the reported
  consumption. Every UNKNOWN reservation freezes its pool (`blocked=True`,
  `fresh=False`) until explicit reconciliation, since an uncertain execution may
  have exceeded an estimated hold. Importing a newer balance does not remove
  that block.
- `release(id)` is only for cancellation before a side effect. UNKNOWN holds
  persist across restart, observation import, and reset time; they do not expire.
  An operator can reconcile verified consumption using `settle`, or verified no
  consumption using `release(id, confirmed_unused=True)`. Never expose that flag
  as an automatic timeout/retry behavior. A known overrun needs provider-side
  investigation; a corrected verified amount within the reserved bound can be
  settled. Never claim unused without evidence.
- `mark_unknown(id)` preserves the hold and blocks the entire pool after a
  timeout, interrupted subprocess, lost acknowledgment, ambiguous tool response,
  or unavailable trustworthy same-unit usage measurement. Do not retry the external
  effect. `get_reservation(id)` returns its durable state for reconciliation.
- `snapshot(account, model='*')` returns account/model, unit, remaining, held,
  available, capacity, observed_at, reset_at, max_age_seconds, source, fresh,
  blocked, and status (`ready`, `stale`, `expired`, `blocked`). Amounts are explicit
  numbers, not normalized health scores. `available` floors at zero.

Pool keys are exact `(account, model)` pairs. Different accounts cannot spend
each other's quota. For a subscription shared across models, use a **single**
`model='*'` pool and explicitly map all those model cards to it, for example
`metadata.quota_pool_model='*'`. Never clone a shared subscription's full balance
into several model-specific pools. Model-specific pools are for genuinely
independent allowances; there is no implicit wildcard fallback. Reservations
against multiple independent restrictive windows in a single transaction are not
implemented; do not represent a multi-window subscription as independently
spendable balances. A trusted upstream policy must supply one conservative,
enforceable pool or defer execution.

Observations with an older timestamp are rejected. Replaying an identical
observation at the same timestamp never restores already settled consumption;
different data at that timestamp is rejected. Open holds survive refreshes.
Settling consumption that is already reflected in a newer provider observation
may conservatively count it again. Reconcile from provider evidence; availability
is intentionally conservative rather than risking duplicate spend. Reset times
invalidate observations; the ledger does not invent a replenished balance.

## CC Switch interface audit

Inspected public upstream source at commit
[`85ad4969b3399500baaea9d46b1e08f87aba80e1`](https://github.com/farion1231/cc-switch/tree/85ad4969b3399500baaea9d46b1e08f87aba80e1):

| Surface | What was verified | What this adapter reads |
| --- | --- | --- |
| Desktop application | [`main.rs`](https://github.com/farion1231/cc-switch/blob/85ad4969b3399500baaea9d46b1e08f87aba80e1/src-tauri/src/main.rs) launches `cc_switch_lib::run()` | Nothing; no fabricated `cc-switch quota --json` command |
| Subscription query | [`subscription.ts`](https://github.com/farion1231/cc-switch/blob/85ad4969b3399500baaea9d46b1e08f87aba80e1/src/lib/api/subscription.ts) uses Tauri `get_subscription_quota`, `get_codex_oauth_quota`, and provider-specific commands | No Tauri IPC or OAuth credentials |
| Provider usage | [`usage.ts`](https://github.com/farion1231/cc-switch/blob/85ad4969b3399500baaea9d46b1e08f87aba80e1/src/lib/api/usage.ts) invokes `queryProviderUsage` and custom script facilities | No usage scripts are executed |
| JSON result types | [`SubscriptionQuota`](https://github.com/farion1231/cc-switch/blob/85ad4969b3399500baaea9d46b1e08f87aba80e1/src/types/subscription.ts) has success, credentialStatus, queriedAt, tiers with utilization/reset times | Requires explicit upstream normalization; raw formats are not guessed |
| SQLite | [`schema.rs`](https://github.com/farion1231/cc-switch/blob/85ad4969b3399500baaea9d46b1e08f87aba80e1/src-tauri/src/database/schema.rs) includes provider `settings_config`/`meta` plus usage rollups | No user database is opened; historical request spend cannot establish remaining subscription quota |

The upstream [usage query manual](https://github.com/farion1231/cc-switch/blob/85ad4969b3399500baaea9d46b1e08f87aba80e1/docs/user-manual/en/2-providers/2.5-usage-query.md)
says official subscription queries must generally be enabled and use local CLI
OAuth credentials with official APIs. These are provider-specific desktop
capabilities, not proof of a portable headless export interface. A separate
installed CLI version must be audited before adding its commands here.

This section describes the original `CCSwitchQuotaSource` interchange adapter.
The integrated native Dashboard additionally reads supported official provider
usage through local Hermes credentials; see `docs/quota-dashboard.md`. It does
not read CC Switch's database, execute its usage scripts, or treat the internal
cloud ledger as a provider subscription allowance.

**Implemented in this interchange adapter:** a read-only importer for a deliberately supplied sanitized
JSON file. **Not implemented or live-validated:** automatic CC Switch extraction,
provider authentication, polling, or account entitlement checks. No real account
data was queried during implementation.

## Normalized JSON interchange

`CCSwitchQuotaSource(path).read()` validates an explicitly supplied file.
`.sync(ledger)` imports observations. This schema belongs to this repository;
it is **not** claimed to be CC Switch's native export format. A trusted helper
must obtain a successful authorized provider observation, choose an explicit
account/pool, retain its original observation time, and emit this minimal form:

```json
{
  "schema_version": 1,
  "source": "cc-switch-normalized",
  "observations": [
    {
      "account": "owner-codex",
      "model": "*",
      "remaining": 62,
      "unit": "percent",
      "capacity": 100,
      "observed_at": 1792861200,
      "reset_at": 1792879200,
      "max_age_seconds": 300
    }
  ]
}
```

Do not use the example's timestamps as fresh observations. A failed query,
unknown entitlement, missing units, or unavailable balance produces no usable
quota. Do not refresh observed_at merely because an old file was reread.

The importer rejects unknown fields, duplicate JSON keys, duplicate pool keys,
NaN/infinity, oversized files (>1 MiB), and malformed observations. Never pass
raw provider settings, tokens, or OAuth stores. It does not search default config
locations or print decoder excerpts. All records are validated before syncing;
individual ledger updates are transactional, but a multi-record import is not
one transaction. On a later conflict, fix the observation and replay the file:
unchanged observations are idempotent.

Run `python tests/test_quota_ledger.py` for the invariant and multi-process tests.
