---
name: cost-aware-ai-orchestration
description: "Route a task across multiple AI models (GPT/Claude/Gemini/Grok/Codex/DeepSeek) by cost, quota, capability, claim, and weighted vote — choosing best cost/performance, not always the strongest model."
version: 0.1.0
license: MIT
metadata:
  tags: [Orchestration, Multi-Model, Cost-Aware, Quota, Routing, TaskMarket]
---

# Cost-aware multi-model orchestration

Use this skill when deciding **which AI model/agent should do a task** given
many options with different strengths, prices, and remaining quota. The goal is
**best cost/performance**, not "always the strongest model".

## When to use
- You have several AI tools/subscriptions and must pick per task.
- You want to conserve scarce/expensive quota for high-value work.
- You need an explainable, repeatable routing decision (not a vibe).

## Core philosophy
> Cheap models explore. Strong models decide. Expensive models finalize.

## Procedure
1. **Classify the task**: `type` (code/research/creative/multimodal/general) and
   `value_level` (trivial/normal/important/critical/expensive_generation).
2. **Map to capability dimensions**; the lead dimension dominates.
3. **Score each candidate** = capability_fit + claim confidence + peer votes +
   history + quota_health + cost_efficiency + specialist_bonus − penalties
   (quota scarcity, overkill, high-price-for-low-value, hallucination risk).
4. **Apply value multipliers**: important/critical reward capability; trivial
   rewards cheapness.
5. **Quota shadow price** = (1 − health) × unit_cost — avoid spending an
   expensive, near-empty model on low-value tasks.
6. **Claim + agreement**: let candidates self-nominate; if the top claim agrees
   with the score-based assignment, execute; else **weighted-vote** to break it.
7. **Assign roles**: primary / verifier / fallback / summarizer.
8. **On failure**: diagnose the failure type, then reroute or **stop** (don't
   burn quota on a task that isn't improving or isn't worth it).

## Quick routing heuristics
- Code → Codex (primary), Claude Code for hard refactors, GPT verifies.
- Research → Grok + Gemini cross-check, GPT/DeepSeek summarize.
- Trivial/format → cheapest (DeepSeek); never an expensive model (overkill).
- Multimodal/long-context → Gemini first.
- Critical → strongest *available* specialist; if it's quota-scarce, route to
  the next specialist and reserve the scarce one.

## Reference implementation
The repo this skill ships with (`cost-aware-ai-orchestrator`) implements all of
the above deterministically: `python -m src.cli plan-file examples/code_task.yaml`.
Tune `configs/*.yaml`; no secrets live in the project.

## Pitfalls
- Don't poll every model to *decide* — that burns the quota you're conserving.
  Decide locally; reserve live model calls for execution or high-value voting.
- Keep capability cards updated; model strengths drift fast.
- Always reserve a fraction of expensive models for the tasks only they can do.
