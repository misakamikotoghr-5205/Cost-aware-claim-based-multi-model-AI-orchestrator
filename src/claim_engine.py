"""Claim engine: agents nominate themselves for tasks they fit.

In the offline MVP, claims are *synthesized* from capability cards + quota
(a deterministic stand-in for asking each model "do you want this?"). When you
wire real backends, replace `synthesize_claims` with actual model calls that
return Claim objects — the rest of the pipeline is unchanged.
"""
from __future__ import annotations

from typing import Dict, List

from .quota_manager import QuotaManager
from .registry import Registry
from .scoring_engine import ScoringEngine
from .task_card import Claim, TaskCard

# Which role an agent tends to claim, by capability profile.
ROLE_HINTS = {
    "primary": ["code_generation", "web_search", "reasoning"],
    "verifier": ["verification"],
    "researcher": ["web_search"],
    "summarizer": ["final_writing"],
}


def _best_role(reg: Registry, agent: str, task: TaskCard) -> str:
    prefs = reg.capabilities.get("agents", {}).get(agent, {}).get("preferred_roles", [])
    if any("verif" in p for p in prefs) and reg.capability(agent, "verification") >= 0.8:
        return "verifier"
    if "researcher" in prefs and task.type == "research":
        return "researcher"
    if any("summar" in p for p in prefs) and reg.capability(agent, "final_writing") >= 0.85:
        return "summarizer"
    return "primary"


class ClaimEngine:
    def __init__(self, registry: Registry, quota: QuotaManager, scorer: ScoringEngine):
        self.reg = registry
        self.quota = quota
        self.scorer = scorer

    def synthesize_claims(self, task: TaskCard, min_confidence: float = 0.55) -> List[Claim]:
        """Offline stand-in: an agent 'claims' a task when its capability fit
        for the task is decent and it has quota headroom."""
        claims: List[Claim] = []
        for agent in self.reg.agents:
            fit = self.scorer.capability_fit(agent, task)
            qhealth = self.quota.health(agent)
            confidence = round(0.6 * fit + 0.4 * qhealth, 3)
            if confidence < min_confidence:
                continue
            role = _best_role(self.reg, agent, task)
            est_cost = self.reg.tier(agent)
            claims.append(Claim(
                agent=agent,
                task_id=task.task_id,
                claim_type=role,
                confidence=confidence,
                reason=f"fit={fit:.2f}, quota_health={qhealth:.2f}",
                estimated_cost=est_cost,
                recommended_verifier=self._suggest_verifiers(agent),
            ))
        claims.sort(key=lambda c: c.confidence, reverse=True)
        return claims

    def _suggest_verifiers(self, agent: str) -> List[str]:
        # An agent recommends strong verifiers other than itself.
        ranked = sorted(
            (a for a in self.reg.agents if a != agent),
            key=lambda a: self.reg.capability(a, "verification"),
            reverse=True,
        )
        return ranked[:2]
