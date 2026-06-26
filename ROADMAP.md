# Roadmap

The honest version → vision path. v0.1 is a **deterministic, offline planner**:
it decides without calling models (so deciding never burns quota). Later
versions progressively add live model interaction and real execution.

## v0.1 — Planner MVP ✅ (current)
- Task cards + value levels (`src/task_card.py`)
- Capability / price / quota configs (`configs/*.yaml`)
- Cost- & quota-aware scoring with transparent breakdown (`scoring_engine.py`)
- Quota Shadow Price (`quota_manager.py`)
- Claim synthesis + agreement check (offline stand-in) (`claim_engine.py`)
- Weighted-voting tie-break (offline stand-in) (`voting_engine.py`)
- Role assignment + decision loop (`orchestrator.py`)
- Failure diagnosis + reroute/stop rules (`rerouter.py`)
- CLI + examples + 9 passing tests

## v0.2 — Docs & SKILL polish
- `docs/*.md` deep dives (architecture, task-market, claim-system, scoring, quota)
- Drop-in agent `skills/SKILL.md`
- More example task cards (image/video pipelines)

## v0.3 — Live capability discovery (`--discover`)
- Use Grok (X/dev feedback) + Gemini (web/benchmarks) to refresh per-dimension
  model rankings and write back `dynamic_strength` into capability cards.
- Cross-verify the two sources; cache results with TTL to avoid re-querying.

## v0.4 — Real quota & price feeds
- Pluggable `quota_status` providers (read your own meters / dashboards).
- `model_prices.yaml` updater from public price pages.

## v0.5 — Live voting (`--live-vote`)
- Optionally poll a small set of models for genuine ballots on high-value tasks
  only (cost-gated, because voting itself costs quota).

## v0.6 — Executor adapters
- `integrations/`: run the chosen plan via Hermes providers, Claude Code, Codex,
  Antigravity `agy`. `hermes_pipeline_router.py` ships as a working reference.

## v0.7 — Memory & learning
- Persist run outcomes; update `historical_success` per agent/task-type.
- Feed real success rates back into scoring.

## v0.8 — Quality verification loop
- Automated verifier prompts (`CHECK=AGREE/DISAGREE/UNSURE`) + audit gate for
  critical tasks.

## v0.9 — Semi-automated subscription orchestration
- Optional web-session drivers for membership-based tools (carefully, opt-in).

## v1.0 — Usable end-to-end
- Plan → execute → verify → reroute → deliver, with logs and cost accounting.

---

## Design constraints (won't change)
- **No secrets in the repo.** Backends own their auth.
- **Deciding stays cheap.** Live model interaction is always opt-in and
  cost-gated — the default planner never spends quota to make a decision.
- **Every decision is explainable.** Scores carry their breakdown.
