"""Orchestrator: the task-market loop tying the engines together.

Pipeline for one task:
  1. synthesize claims (claim-based self-nomination)
  2. propose an initial assignment from pure scoring
  3. if the top claim agrees with the initial assignment -> execute
  4. if they disagree -> run weighted voting, re-score with peer votes, decide
  5. assign roles (primary / verifier / fallback / summarizer)

This MVP DECIDES (plans). It does not call the models itself; an executor
adapter (see integrations/) runs the chosen plan. Decisions are explainable:
every score carries its breakdown.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .claim_engine import ClaimEngine
from .quota_manager import QuotaManager
from .registry import Registry
from .scoring_engine import ScoringEngine
from .task_card import Claim, ScoreBreakdown, TaskCard
from .voting_engine import VotingEngine


@dataclass
class Decision:
    task_id: str
    title: str
    value_level: str
    roles: Dict[str, str] = field(default_factory=dict)
    method: str = ""                 # "agreement" | "weighted_vote"
    ranking: List[ScoreBreakdown] = field(default_factory=list)
    claims: List[Claim] = field(default_factory=list)
    peer_votes: Dict[str, float] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "value_level": self.value_level,
            "method": self.method,
            "roles": self.roles,
            "ranking": [
                {"agent": s.agent, "total": s.total, "parts": s.parts, "penalties": s.penalties}
                for s in self.ranking
            ],
            "peer_votes": self.peer_votes,
            "notes": self.notes,
        }


class Orchestrator:
    def __init__(self, registry: Registry, quota_status: dict | None = None,
                 history: dict | None = None):
        self.reg = registry
        self.quota = QuotaManager(registry, quota_status)
        self.scorer = ScoringEngine(registry, self.quota, history)
        self.claims = ClaimEngine(registry, self.quota, self.scorer)
        self.voting = VotingEngine(registry, self.quota, self.scorer)

    def decide(self, task: TaskCard) -> Decision:
        th = self.reg.scoring.get("thresholds", {})
        margin = th.get("agreement_margin", 0.08)

        claims = self.claims.synthesize_claims(task)
        base_rank = self.scorer.rank(task, claims=claims)
        initial_primary = base_rank[0].agent if base_rank else None
        top_claim = claims[0].agent if claims else None

        decision = Decision(
            task_id=task.task_id, title=task.title, value_level=task.value_level,
            claims=claims, ranking=base_rank,
        )

        # Step 3: agreement check between initial assignment and the strongest claim.
        agree = (
            initial_primary is not None
            and top_claim is not None
            and (
                initial_primary == top_claim
                or self._within_margin(base_rank, initial_primary, top_claim, margin)
            )
        )

        if agree:
            decision.method = "agreement"
            decision.notes.append(
                f"initial assignment ({initial_primary}) agrees with top claim ({top_claim})"
            )
            final_rank = base_rank
        else:
            # Step 4: weighted voting breaks the tie.
            decision.method = "weighted_vote"
            votes = self.voting.synthesize_votes(task)
            peer = self.voting.tally(task, votes)
            decision.peer_votes = peer
            final_rank = self.scorer.rank(task, claims=claims, peer_votes=peer)
            decision.ranking = final_rank
            decision.notes.append(
                f"disagreement: assignment={initial_primary}, top_claim={top_claim}; "
                f"resolved by weighted vote"
            )

        decision.roles = self._assign_roles(task, final_rank)

        # Guard: low top score ⇒ flag for escalation/replan.
        if final_rank and final_rank[0].total < th.get("auto_execute_min_score", 0.55):
            decision.notes.append(
                f"top score {final_rank[0].total:.3f} below auto-execute threshold "
                f"({th.get('auto_execute_min_score', 0.55)}) — consider re-planning or human review"
            )
        return decision

    # ------------------------------------------------------------------ #
    def _within_margin(self, rank: List[ScoreBreakdown], a: str, b: str, margin: float) -> bool:
        scores = {s.agent: s.total for s in rank}
        if a not in scores or b not in scores:
            return False
        return abs(scores[a] - scores[b]) <= margin

    def _assign_roles(self, task: TaskCard, rank: List[ScoreBreakdown]) -> Dict[str, str]:
        if not rank:
            return {}
        roles: Dict[str, str] = {"primary": rank[0].agent}
        others = [s.agent for s in rank[1:]]

        # verifier: strongest verification capability that isn't primary
        verifier = max(
            (a for a in others), default=None,
            key=lambda a: self.reg.capability(a, "verification"),
        )
        if verifier:
            roles["verifier"] = verifier

        # fallback: next-best primary candidate in the same capability family
        fallback = next((a for a in others if a != roles.get("verifier")), None)
        if fallback:
            roles["fallback"] = fallback

        # summarizer for important/critical tasks: best final_writing
        if task.value_level in ("important", "critical", "expensive_generation"):
            summarizer = max(
                self.reg.agents, default=None,
                key=lambda a: self.reg.capability(a, "final_writing"),
            )
            if summarizer and summarizer != roles["primary"]:
                roles["summarizer"] = summarizer
        return roles
