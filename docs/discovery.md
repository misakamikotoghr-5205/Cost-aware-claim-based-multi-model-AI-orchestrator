# Optional client model discovery

Discovery runs on the Windows/Ubuntu client only when its operator enables the
extension. The unified desktop client must leave it disabled by default. A user
can use configured subscription or local model cards and the existing offline
scorer without installing Token Scout, Free Models Monitor (FMM), or Ollama.
Importing `src.discovery`, constructing `DiscoveryCache`, and reading cards make
no network requests and start no processes. A disabled extension must bypass its
file/command adapters entirely. This module does not install tools, schedule
checks, log in, notify third parties, invoke inference, or modify executor config.

`ModelCard` is a routing candidate, not permission to execute a task or proof of
account quota. The shared ledger and executor policy remain authoritative. The
existing scoring algorithm is unchanged.

## Data contract

`ModelCard` contains these fields and validates them on construction/import:

| Field | Meaning |
| --- | --- |
| `model_id`, `provider`, `executor` | Nonempty identifiers; `(provider, model_id, executor)` identifies a candidate. |
| `capabilities` | Mapping of dimension names to finite scores in `[0, 1]`. Unknown capabilities stay absent. |
| `billing_mode` | `local`, `free`, `subscription`, or `paid_api`; defaults to the conservative `paid_api`. |
| `available` | Boolean observation, default false. No implication of login or sufficient account quota. |
| `observed_at` | UTC epoch seconds when the source was actually observed. |
| `expires_at` | Optional UTC epoch deadline for a promotion; a passed deadline always excludes it. |
| `quota_remaining` | Optional **fraction** in `[0, 1]`; `None` means unknown. Units/absolute counters belong in the ledger. |
| `source`, `metadata` | Provenance and compatibility details; never API key values. |

`to_dict()`/`from_dict()` round-trip JSON. `is_eligible(now=...,
max_age_seconds=900, allow_paid=False)` rejects unavailable, future, stale,
expired, exhausted, and unapproved paid candidates. A model name ending in
`:free` is not price evidence. Missing prices never become zero prices.

Token Scout strengths become **declared feature indicators** with a neutral 0.5
score, not vendor benchmarks. Its compatibility profile stays in metadata;
`Claimed` tool support must not be presented as tested native support. Ollama
`/api/tags` and FMM have no assessed quality scores, so their capability maps are
empty. Configure or evaluate capabilities separately before assigning tasks.

## Verified upstream protocols

Inspected on 2026-10-24; these are source revisions researched for this adapter,
not dependencies installed or services successfully exercised on a user account.

- Token Scout [`d1ddb986724d3fbcfa244c0e5068525a27c49769`](https://github.com/jackccrawford/token-scout/tree/d1ddb986724d3fbcfa244c0e5068525a27c49769):
  [`src/scout.rs`](https://github.com/jackccrawford/token-scout/blob/d1ddb986724d3fbcfa244c0e5068525a27c49769/src/scout.rs),
  [`src/registry.rs`](https://github.com/jackccrawford/token-scout/blob/d1ddb986724d3fbcfa244c0e5068525a27c49769/src/registry.rs),
  [`src/tracker.rs`](https://github.com/jackccrawford/token-scout/blob/d1ddb986724d3fbcfa244c0e5068525a27c49769/src/tracker.rs).
- FMM [`e7fb95e35294366356cf547d702de5702f057e12`](https://github.com/nikolasdehor/free-models-monitor/tree/e7fb95e35294366356cf547d702de5702f057e12):
  [`monitor.py`](https://github.com/nikolasdehor/free-models-monitor/blob/e7fb95e35294366356cf547d702de5702f057e12/free_models_monitor/monitor.py),
  [`providers.py`](https://github.com/nikolasdehor/free-models-monitor/blob/e7fb95e35294366356cf547d702de5702f057e12/free_models_monitor/providers.py),
  [catalog reliability](https://github.com/nikolasdehor/free-models-monitor/blob/e7fb95e35294366356cf547d702de5702f057e12/docs/catalog-reliability.md).
- Ollama imports the documented [`GET /api/tags`](https://docs.ollama.com/api/tags)
  response shape; this adapter does not download weights or call generation.

### Token Scout

The Rust executable accepts JSON-RPC on stdin:

```json
{"jsonrpc":"2.0","id":1,"method":"scout","params":{"query":"code"}}
```

`parse_token_scout(payload, observed_at=timestamp)` consumes either the result
object, its JSON-RPC envelope, or an MCP text block containing that JSON. Query
results contain `matches` with `model`, `provider`, `endpoint`, `api_style`,
`prompt_cost`, `completion_cost`, `strengths`, `compat`, and `quota`.
The empty-query status response contains `providers`, omits pricing, and is
rejected. The older Python MCP implementation renders human-readable text;
that text is **not a supported JSON feed**. Export the Rust JSON result instead.

There is a material upstream limitation: Token Scout synthesizes zero prices for
static fallback entries, and the exported results do not identify static versus
live provenance. Its quota tracker is an in-process estimate, not the provider's
account balance. Therefore this adapter imports cloud Scout matches as
**unavailable candidates**, retaining reported prices and compatibility only.
Corroborate current zero pricing with a fresh FMM OpenRouter observation or an
operator-verified model card. Do not automatically treat a free suffix, static
provider list, or reported quota counter as proof. Ollama-style Scout results
may establish local inventory; recognized cloud/remote entries remain disabled.

### Free Models Monitor

FMM `--format json` prints a **diff/report**, not a full model list. Import both
the same run's `snapshot.json` and JSON report:

```python
from src.discovery import parse_free_models_monitor, read_json_file

cards = parse_free_models_monitor(
    read_json_file("local-state/fmm/snapshot.json"),
    read_json_file("local-state/fmm-report.json"),
    observed_at=actual_check_timestamp,
)
```

For an operator-enabled check, upstream supports:

```text
free-models-monitor --providers openrouter --format json --notify none --state-dir local-state/fmm
```

Capture stdout as the report and retain the matching snapshot. Exit 0 is a
successful baseline/no change, 2 is successful change detection, and 1 is an
incomplete/error check. Do not interpret every nonzero exit as an empty catalog.
The command helper only accepts 0 unless the caller explicitly opts into `(0, 2)`.
A partial error can instead be inspected through explicit file import; call
`cache.apply(..., complete=False)` to preserve identities not freshly observed.

Only OpenRouter `provider_status: fresh`, a matching `provider_counts` value,
and its zero-prompt/zero-completion filtered snapshot establish a free candidate.
`cached`, `unavailable`, and `static_unverified` records are unavailable. Groq's
legacy manual list is not evidence of current free eligibility. Unknown provider
status, missing report fields, malformed model data, or mismatched counts reject
the import; a successful explicit zero count permits legitimate removals.

FMM's report does not supply per-model observed timestamps. A paired report and
snapshot must come from the same check. Count validation catches many accidental
mismatches but cannot prove pairing. Supply the real check time; for saved files,
use a conservative timestamp no newer than either file's mtime. Reading an old
JSON file again must not renew evidence. Partial/cached providers stay unavailable
regardless of the import timestamp. No complete live account quota is inferred.

### Ollama and limited promotions

`parse_ollama(tags_json, observed_at=timestamp)` inventories installed model names
and digests with `billing_mode=local`. `/api/tags` alone does not establish vision,
tool reliability, memory requirements, or quality. Known remote/cloud tags
(`:cloud`, `-cloud`, `remote_host`, `remote_model`) are disabled and classified
conservatively as `paid_api`; a local Ollama frontend does not prove local inference.
Operators must also exclude other hosted endpoints before treating a card as local.

For a promotion, import an operator-verified `ModelCard` with an explicit
`expires_at`; the source must establish the end date. The Token Scout adapter also
preserves an optional enriched `expires_at` epoch or timezone-qualified ISO date;
this is an import extension, **not a field currently supplied by upstream Scout**.
A stale promotion is excluded even if its provider remains listed. Unknown end
dates do not imply permanent free eligibility; the freshness TTL still applies.

## Persistent cache and events

```python
from src.discovery import DiscoveryCache

cache = DiscoveryCache("local-state/discovery.json", ttl_seconds=900)
events = cache.apply("free-models-monitor", cards, now=actual_check_timestamp)
usable = cache.load(now=actual_check_timestamp)
audit = cache.load(now=actual_check_timestamp, include_stale=True)
print([event.to_dict() for event in events])
```

The schema-versioned JSON cache uses same-directory atomic replacement and fsync;
a failed validation leaves the previous file intact. It is a **single-client
cache**, not a concurrent shared quota store. Serialize refreshes in the desktop
client. Use the shared ledger for cross-client reservations.

TTL deadlines persist across process restarts. Reads do not refresh timestamps,
reopening with a longer TTL cannot extend stored evidence, and partial refreshes
do not extend unobserved models' deadlines. Normal loads exclude unavailable,
expired, future, zero-quota, and stale cards. Audit loads retain them with
`available=false` and an explanatory `metadata.discovery_status`. Multiple sources
for the same candidate prefer an available observation, then the newer timestamp.
Paid cards still require the router's explicit policy: cache visibility is not
payment authorization.

A complete source refresh emits `added`, `updated`, and `removed` events with
previous/current cards. A timestamp-only refresh emits no change event. Refreshing
one source does not remove another source's records; partial checks do not infer
absence. Deadline passage is enforced on every read, rather than requiring a
background event loop. Events are returned to the caller, never sent externally.

`run_json_command` accepts an explicit argv list with `shell=False`, timeout,
stdin JSON, accepted exit codes, and a JSON input size limit. It does not expand
shell substitutions or print stderr. Configure argv only from trusted operator
settings, never from discovered cards, model output, or a remote task. It launches
only when explicitly called. No generic shell command strings are accepted.

Validation: `python -m pytest -q tests/test_discovery.py` uses simulated upstream
protocols, temporary files, and short local Python processes; no inference,
network, account credentials, or notification services are needed.
