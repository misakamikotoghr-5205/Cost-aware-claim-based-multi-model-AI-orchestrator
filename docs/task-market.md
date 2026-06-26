# Task Market & Task Cards

A task card is the unit of work published to the market.

```yaml
task_id: T006
title: Build local routing CLI
type: code_generation        # drives required capability dimensions
requires:                    # fine-grained capability tokens
  - repository_editing
  - cli_design
  - config_parsing
  - error_handling
priority: high               # low | normal | high
value_level: important       # trivial | normal | important | critical | expensive_generation
budget: medium               # low | medium | high (relative)
risk: medium
expected_output: runnable CLI prototype
```

## type → dimensions
`type` and each `requires` token map to capability **dimensions** (see
`src/scoring_engine.py`: `TYPE_TO_DIMENSIONS`, `REQUIRE_TO_DIMENSION`). The
**first** dimension is the *lead* — it counts double in capability_fit and
drives the specialist bonus, so a task's headline skill dominates.

## value_level → policy
| level | policy |
|---|---|
| trivial | cheapest available; expensive models penalized |
| normal | cheap-first, strong-verify if needed |
| important | mid model executes, strong verifies |
| critical | strong executes, cross-verified |
| expensive_generation | cheap draft first, then upgrade |

Value level also sets scoring **multipliers** (capability vs. cost emphasis).

## Batch
Run many cards at once with a top-level `tasks:` list — see `examples/batch.yaml`.
