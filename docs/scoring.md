# Scoring & Voting

## FinalScore(agent, task)
A transparent weighted sum (see `src/scoring_engine.py`). Positive parts reward
fit/confidence/track-record/quota/cost; penalties punish waste and risk.

```
FinalScore =
    w.capability_fit        * capability_fit          (lead dimension counts 2x)
  + w.self_claim_confidence * claim_confidence
  + w.planner_recommendation* planner_endorsement
  + w.peer_vote_score       * weighted_peer_votes
  + w.historical_success    * rolling_success_rate
  + w.quota_health          * quota_headroom
  + w.cost_efficiency       * (fit * (1 - unit_cost))
  + specialist_bonus        (0.16 * strength, if lead dim is in agent's top-3 skills)
  - penalties.quota_scarcity                 (near reserve)
  - penalties.recent_failure
  - penalties.overkill_penalty               (expensive tier on trivial/normal)
  - penalties.high_price_for_low_value_task
  - penalties.hallucination_risk             (critical task, weak verifier)
```

Weights live in `configs/scoring_weights.yaml`. `total` is always reconciled
with the rounded breakdown, so the printed explanation adds up exactly.

## Value-level multipliers
Higher value ⇒ capability matters more, cost matters less:

| level | capability_fit | cost_efficiency | quota_health |
|---|---|---|---|
| trivial | 0.6 | 1.6 | 1.2 |
| normal | 0.9 | 1.2 | 1.1 |
| important | 1.6 | 0.7 | 0.9 |
| critical | 2.0 | 0.4 | 0.8 |
| expensive_generation | 1.3 | 1.1 | 1.0 |

## Specialist bonus
If the task's lead dimension is among an agent's own top-3 skills, it gets
`0.16 × strength_in_that_dimension`. This keeps Codex/Claude primary on code and
Grok/Gemini primary on search, while generalists like GPT gravitate to
verifier/summarizer.

## Weighted voting (tie-break)
When the initial assignment and the top claim disagree, each agent casts a
ballot. Votes are weighted (not one-per-agent):

```
vote_weight = model_strength*0.30 + task_relevance*0.25
            + historical_accuracy*0.20 + quota_health*0.15 + cost_awareness*0.10
```

Tallies are normalized to 0..1 and fed back into scoring as `peer_vote_score`,
then the task is re-ranked. See `src/voting_engine.py`.

## Thresholds
- `auto_execute_min_score` (0.55): below this, flag for re-plan / human review.
- `agreement_margin` (0.08): assignment vs. top-claim within this ⇒ "agree".
- `quality_floor` (0.70): verification below this ⇒ reroute.
