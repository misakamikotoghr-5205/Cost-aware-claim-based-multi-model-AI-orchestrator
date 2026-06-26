"""Data models: tasks, claims, votes, capability cards.

These are intentionally plain dataclasses so the whole system stays readable
and unit-testable without any model calls.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


VALUE_LEVELS = ("trivial", "normal", "important", "critical", "expensive_generation")


@dataclass
class TaskCard:
    """A unit of work published to the task market."""

    task_id: str
    title: str
    type: str
    requires: List[str] = field(default_factory=list)
    priority: str = "normal"           # low | normal | high
    value_level: str = "normal"        # one of VALUE_LEVELS
    budget: str = "medium"             # low | medium | high (relative)
    risk: str = "medium"               # low | medium | high
    expected_output: str = ""

    def __post_init__(self) -> None:
        if self.value_level not in VALUE_LEVELS:
            raise ValueError(
                f"task {self.task_id}: value_level {self.value_level!r} "
                f"not in {VALUE_LEVELS}"
            )

    @classmethod
    def from_dict(cls, d: Dict) -> "TaskCard":
        return cls(
            task_id=str(d["task_id"]),
            title=str(d.get("title", "")),
            type=str(d.get("type", "general")),
            requires=list(d.get("requires", []) or []),
            priority=str(d.get("priority", "normal")),
            value_level=str(d.get("value_level", "normal")),
            budget=str(d.get("budget", "medium")),
            risk=str(d.get("risk", "medium")),
            expected_output=str(d.get("expected_output", "")),
        )


@dataclass
class Claim:
    """An agent's self-nomination for a task (the 'claim' in claim-based)."""

    agent: str
    task_id: str
    claim_type: str           # primary | assistant | verifier | researcher | ...
    confidence: float         # 0..1
    reason: str = ""
    estimated_cost: str = "medium"
    risk: str = "medium"
    needs_support_from: List[str] = field(default_factory=list)
    recommended_verifier: List[str] = field(default_factory=list)


@dataclass
class Vote:
    """A weighted ballot for who should fill which role on a task."""

    voter: str
    assignment: Dict[str, str]   # role -> agent
    confidence: float            # 0..1
    reason: str = ""


@dataclass
class ScoreBreakdown:
    """Transparent per-agent score so decisions are explainable."""

    agent: str
    total: float
    parts: Dict[str, float] = field(default_factory=dict)
    penalties: Dict[str, float] = field(default_factory=dict)

    def explain(self) -> str:
        plus = ", ".join(f"{k}=+{v:.3f}" for k, v in self.parts.items() if v)
        minus = ", ".join(f"{k}=-{v:.3f}" for k, v in self.penalties.items() if v)
        tail = f" | penalties: {minus}" if minus else ""
        return f"{self.agent}: total={self.total:.3f} | {plus}{tail}"
