"""Scoring engine: FinalScore(agent, task) with transparent breakdown.

This is deterministic and local — no model calls. That is by design: scoring
every candidate by calling 6 models would itself burn the quota this project
is meant to conserve. Live cross-checking is an *optional* upgrade
(see voting_engine + the orchestrator's --live-vote path / ROADMAP).
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .quota_manager import QuotaManager
from .registry import Registry
from .task_card import Claim, ScoreBreakdown, TaskCard

# Map a task's `requires` tokens / type to capability dimensions.
REQUIRE_TO_DIMENSION = {
    "repository_editing": "code_generation",
    "cli_design": "code_generation",
    "config_parsing": "code_generation",
    "error_handling": "code_generation",
    "implementation": "code_generation",
    "architecture": "code_architecture",
    "multi_file": "code_architecture",
    "refactor": "code_architecture",
    "web_search": "web_search",
    "source_verification": "verification",
    "recency_check": "web_search",
    "visual_reasoning": "multimodal",
    "video_structure": "video_generation",
    "marketing_copy": "final_writing",
    "long_context": "long_context",
    "document_analysis": "long_context",
}

TYPE_TO_DIMENSIONS = {
    "code_generation": ["code_generation", "code_architecture"],
    "research": ["web_search", "verification"],
    "creative_planning": ["multimodal", "final_writing"],
    "writing": ["final_writing", "reasoning"],
    "multimodal": ["multimodal", "long_context"],
    "image": ["image_generation"],
    "video": ["video_generation", "multimodal"],
    "general": ["reasoning"],
}

EXPENSIVE_TIERS = {"expensive"}

# Value-level multipliers: how much capability vs. cost matters by task value.
# Higher-value tasks reward capability_fit and discount raw cost; low-value
# tasks do the opposite (cheap-first). This encodes the routing_rules policies.
VALUE_MULTIPLIERS = {
    "trivial":              {"capability_fit": 0.6, "cost_efficiency": 1.6, "quota_health": 1.2},
    "normal":               {"capability_fit": 0.9, "cost_efficiency": 1.2, "quota_health": 1.1},
    "important":            {"capability_fit": 1.6, "cost_efficiency": 0.7, "quota_health": 0.9},
    "critical":             {"capability_fit": 2.0, "cost_efficiency": 0.4, "quota_health": 0.8},
    "expensive_generation": {"capability_fit": 1.3, "cost_efficiency": 1.1, "quota_health": 1.0},
}


class ScoringEngine:
    def __init__(self, registry: Registry, quota: QuotaManager,
                 history: Optional[Dict[str, float]] = None):
        self.reg = registry
        self.quota = quota
        # history: agent -> rolling success rate 0..1 (defaults to 0.7 prior)
        self.history = history or {}

    # ------------------------------------------------------------------ #
    def required_dimensions(self, task: TaskCard) -> List[str]:
        dims: List[str] = []
        for token in task.requires:
            d = REQUIRE_TO_DIMENSION.get(token)
            if d and d not in dims:
                dims.append(d)
        for d in TYPE_TO_DIMENSIONS.get(task.type, []):
            if d not in dims:
                dims.append(d)
        return dims or ["reasoning"]

    def capability_fit(self, agent: str, task: TaskCard) -> float:
        dims = self.required_dimensions(task)
        if not dims:
            return 0.0
        # Lead-weighted average: the first (primary) dimension counts double, so
        # a task's headline skill (e.g. code_generation) dominates over secondary
        # ones. This keeps specialist models (Codex/Claude for code, Grok/Gemini
        # for search) ahead of generalists on their home turf.
        weights = [2.0] + [1.0] * (len(dims) - 1)
        vals = [self.reg.capability(agent, d) for d in dims]
        return sum(v * w for v, w in zip(vals, weights)) / sum(weights)

    def cost_efficiency(self, agent: str, fit: float) -> float:
        """Reward quality-per-unit-cost. Cheaper for equal fit scores higher."""
        price = max(0.01, self.reg.unit_cost(agent))
        return max(0.0, min(1.0, fit * (1.0 - price)))

    def is_specialist(self, agent: str, task: TaskCard) -> bool:
        """True if the task's lead dimension is among this agent's top skills."""
        return self.specialist_strength(agent, task) > 0.0

    def specialist_strength(self, agent: str, task: TaskCard) -> float:
        """The agent's capability in the task's lead dimension, but only if that
        dimension is among the agent's own top-3 skills (else 0).

        This keeps specialists as primary on their home turf (Codex/Claude for
        code, Grok/Gemini for search) and steers generalists like GPT toward
        verifier/summarizer roles. Scaling by actual strength means the *better*
        specialist wins, not merely the cheapest one that can do the job.
        """
        dims = self.required_dimensions(task)
        if not dims:
            return 0.0
        lead = dims[0]
        caps = self.reg.capabilities.get("agents", {}).get(agent, {}).get("capabilities", {})
        if not caps:
            return 0.0
        ranked = sorted(caps.items(), key=lambda kv: kv[1], reverse=True)
        top3 = {name for name, _ in ranked[:3]}
        return float(caps.get(lead, 0.0)) if lead in top3 else 0.0

    # ------------------------------------------------------------------ #
    def score(self, agent: str, task: TaskCard,
              claims: Optional[List[Claim]] = None,
              peer_vote_score: float = 0.0,
              planner_recommended: bool = False) -> ScoreBreakdown:
        w = self.reg.scoring.get("scoring", {})
        pen_w = self.reg.scoring.get("penalties", {})

        fit = self.capability_fit(agent, task)
        claim_conf = 0.0
        for c in (claims or []):
            if c.agent == agent:
                claim_conf = max(claim_conf, c.confidence)
        hist = self.history.get(agent, 0.70)
        qhealth = self.quota.health(agent)
        ceff = self.cost_efficiency(agent, fit)
        planner = 1.0 if planner_recommended else 0.0

        parts = {
            "capability_fit": w.get("capability_fit", 0) * fit,
            "self_claim_confidence": w.get("self_claim_confidence", 0) * claim_conf,
            "planner_recommendation": w.get("planner_recommendation", 0) * planner,
            "peer_vote_score": w.get("peer_vote_score", 0) * peer_vote_score,
            "historical_success": w.get("historical_success", 0) * hist,
            "quota_health": w.get("quota_health", 0) * qhealth,
            "cost_efficiency": w.get("cost_efficiency", 0) * ceff,
        }

        # Apply value-level multipliers so high-value tasks reward capability
        # and low-value tasks reward cheapness.
        mult = VALUE_MULTIPLIERS.get(task.value_level, {})
        for key, factor in mult.items():
            if key in parts:
                parts[key] *= factor

        # Specialist bonus: agents whose top skills include the task's lead
        # dimension are the natural primary; scaled by their actual strength so
        # the strongest specialist (not merely the cheapest) wins primary.
        spec = self.specialist_strength(agent, task)
        if spec > 0.0:
            parts["specialist_bonus"] = 0.16 * spec

        penalties: Dict[str, float] = {}
        # quota scarcity
        if self.quota.is_scarce(agent):
            penalties["quota_scarcity"] = pen_w.get("quota_scarcity", 0)
        # recent failure
        if self.history.get(agent, 0.70) < 0.4:
            penalties["recent_failure"] = pen_w.get("recent_failure", 0)
        # overkill: expensive tier on a trivial/normal task
        if self.reg.tier(agent) in EXPENSIVE_TIERS and task.value_level in ("trivial", "normal"):
            penalties["overkill_penalty"] = pen_w.get("overkill_penalty", 0)
        # high price for low value
        if task.value_level == "trivial" and self.reg.unit_cost(agent) >= 0.6:
            penalties["high_price_for_low_value_task"] = pen_w.get("high_price_for_low_value_task", 0)
        # hallucination risk on critical research without verification strength
        if task.value_level == "critical" and self.reg.capability(agent, "verification") < 0.6:
            penalties["hallucination_risk"] = pen_w.get("hallucination_risk", 0)

        # Round parts/penalties first, then derive total from the rounded values
        # so the displayed breakdown always reconciles with `total` exactly.
        parts = {k: round(v, 4) for k, v in parts.items()}
        penalties = {k: round(v, 4) for k, v in penalties.items()}
        total = round(sum(parts.values()) - sum(penalties.values()), 4)
        return ScoreBreakdown(agent=agent, total=total, parts=parts, penalties=penalties)

    def rank(self, task: TaskCard, agents: Optional[List[str]] = None,
             claims: Optional[List[Claim]] = None,
             peer_votes: Optional[Dict[str, float]] = None,
             planner_recos: Optional[List[str]] = None) -> List[ScoreBreakdown]:
        agents = agents or self.reg.agents
        peer_votes = peer_votes or {}
        planner_recos = planner_recos or []
        out = [
            self.score(a, task, claims=claims,
                       peer_vote_score=peer_votes.get(a, 0.0),
                       planner_recommended=(a in planner_recos))
            for a in agents
        ]
        out.sort(key=lambda s: s.total, reverse=True)
        return out
