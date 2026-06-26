# Claim System

Agents are not handed work — they **claim** it. A claim is a self-nomination:

```yaml
agent: Codex
task_id: T006
claim_type: primary          # primary | assistant | verifier | researcher | summarizer | ...
confidence: 0.91             # 0..1
reason: This is a repository editing and implementation task.
estimated_cost: medium
risk: May need architecture review if the project grows.
needs_support_from: [Claude Code]
recommended_verifier: [GPT, Claude]
```

## Offline stand-in (v0.1)
`src/claim_engine.py` *synthesizes* claims deterministically from capability fit
and quota health:

```
confidence = 0.6 * capability_fit + 0.4 * quota_health
```

An agent only claims when confidence ≥ threshold, picks a role from its
`preferred_roles`, and recommends the two strongest *other* verifiers.

## Going live
Replace `synthesize_claims()` with real model calls that return `Claim` objects.
Nothing downstream changes — agreement check, voting and scoring all operate on
`Claim` instances regardless of where they came from.

## Agreement vs. disagreement
The orchestrator compares the **initial assignment** (pure scoring) with the
**top claim**. If they match (or are within `agreement_margin`), it executes
directly. Otherwise weighted voting decides — see `scoring.md`.
