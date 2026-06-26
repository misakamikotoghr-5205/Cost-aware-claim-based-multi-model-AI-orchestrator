# Architecture

Three layers, matching the lifecycle of a task.

## Layer 1 — Capability Discovery
Knows *what each model is currently good at*. In v0.1 this is the static
`configs/capability_cards.yaml`. In v0.3+ an optional `--discover` step uses
Grok (real-time X / developer feedback) and Gemini (web benchmarks / docs) to
refresh per-dimension rankings and cross-verify them.

Rank by **dimension**, never a single "overall best":
code, reasoning, search, long-context, image, video, cost-efficiency,
verification.

## Layer 2 — Task Market
Turns a request into work and decides who does it:
1. Decompose into **task cards** (`src/task_card.py`).
2. Agents **claim** cards they fit (`claim_engine.py`).
3. The orchestrator proposes an **initial assignment** from scoring.
4. If the top claim agrees → execute. If not → **weighted vote**
   (`voting_engine.py`) breaks the tie.
5. **Roles** assigned: primary / assistant / verifier / fallback / summarizer.

## Layer 3 — Execution & Verification
Runs the plan, records cost, verifies quality, and on failure **diagnoses then
reroutes or stops** (`rerouter.py`). v0.1 plans only; executor adapters land in
`integrations/` (a real Hermes router ships as reference).

```
request
  │  (L1) capability discovery — what's strong right now
  ▼
task cards ──► claims ──► initial assignment
  │                          │ agree? ── yes ─► execute
  │                          └ no  ─► weighted vote ─► execute
  ▼ (L2 task market)
roles: primary / verifier / fallback / summarizer
  ▼ (L3 execution & verification)
run → verify → (fail) diagnose → reroute / stop → deliver
```
