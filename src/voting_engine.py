"""Voting engine: weighted ballots when assignment and claims disagree.

Vote weight = product of per-voter factors (model strength on this task,
quota health, historical accuracy, cost awareness). Each voter casts a ballot
for who should be `primary`; weights are summed per candidate.

Offline MVP synthesizes ballots from the capability/quota state. Swap in real
model votes later without changing the aggregation.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List

from .quota_manager import QuotaManager
from .registry import Registry
from .scoring_engine import ScoringEngine
from .task_card import TaskCard, Vote


class VotingEngine:
    def __init__(self, registry: Registry, quota: QuotaManager, scorer: ScoringEngine):
        self.reg = registry
        self.quota = quota
        self.scorer = scorer

    def voter_weight(self, voter: str, task: TaskCard) -> float:
        vw = self.reg.scoring.get("vote_weight", {})
        strength = self.scorer.capability_fit(voter, task)         # task-relevant strength
        relevance = strength
        hist = self.scorer.history.get(voter, 0.70)
        qhealth = self.quota.health(voter)
        cost_aware = 1.0 - self.reg.unit_cost(voter)
        return round(
            vw.get("model_strength", 0) * strength
            + vw.get("task_relevance", 0) * relevance
            + vw.get("historical_accuracy", 0) * hist
            + vw.get("quota_health", 0) * qhealth
            + vw.get("cost_awareness", 0) * cost_aware,
            4,
        )

    def synthesize_votes(self, task: TaskCard) -> List[Vote]:
        """Each agent votes for the top-fit candidate (excluding obviously
        overkill picks for low-value tasks)."""
        ranked = self.scorer.rank(task)
        if not ranked:
            return []
        top = ranked[0].agent
        votes: List[Vote] = []
        for voter in self.reg.agents:
            votes.append(Vote(
                voter=voter,
                assignment={"primary": top},
                confidence=round(self.scorer.capability_fit(voter, task), 3),
                reason=f"top-fit candidate for {task.type}",
            ))
        return votes

    def tally(self, task: TaskCard, votes: List[Vote]) -> Dict[str, float]:
        """Return candidate -> normalized weighted vote score (0..1)."""
        scores: Dict[str, float] = defaultdict(float)
        for v in votes:
            cand = v.assignment.get("primary")
            if not cand:
                continue
            scores[cand] += self.voter_weight(v.voter, task) * v.confidence
        if not scores:
            return {}
        hi = max(scores.values()) or 1.0
        return {k: round(val / hi, 4) for k, val in scores.items()}
