"""Rerouter: failure diagnosis + reroute/stop decisions.

Failures are not retried blindly. We classify the failure, then pick an action
from routing_rules.yaml. This module is pure logic over a small state object so
it is trivially testable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .registry import Registry

FAILURE_ACTIONS = {
    "insufficient_info": "re_search",
    "misunderstanding": "back_to_decomposition",
    "code_error": "switch_to_code_model",
    "architecture_error": "escalate_to_claude_code",
    "low_quality": "send_to_verifier_or_stronger_model",
    "format_error": "cheap_model_fix_format",
    "context_too_long": "switch_to_long_context_model",
    "quota_exhausted": "downgrade_or_reserve_model",
    "permission_error": "request_human_confirmation",
    "video_low_quality": "return_to_storyboard_stage",
    "image_low_quality": "improve_prompt_then_regenerate",
}


@dataclass
class TaskState:
    value_level: str = "normal"
    retry_count: int = 0
    same_error_count: int = 0
    quality_score: float = 1.0
    quota_low: bool = False
    complexity_escalated: bool = False
    no_improvement_rounds: int = 0
    estimated_cost: float = 0.0     # relative
    task_value: float = 1.0         # relative


class Rerouter:
    def __init__(self, registry: Registry):
        self.reg = registry

    def diagnose_action(self, failure_type: str) -> str:
        return FAILURE_ACTIONS.get(failure_type, "switch_to_fallback")

    def should_stop(self, st: TaskState) -> Optional[str]:
        """Return a stop reason string, or None to continue."""
        v = st.value_level
        if v == "trivial" and st.retry_count >= 1:
            return "trivial task already retried — stop (not worth more quota)"
        if v == "normal" and st.retry_count >= 2:
            return "normal task retried twice — stop or ask user"
        if v == "important" and st.retry_count >= 3:
            return "important task retried 3x — escalate once then stop"
        if st.no_improvement_rounds >= 2:
            return "no quality improvement after 2 rounds — stop"
        if st.estimated_cost > st.task_value:
            return "estimated cost exceeds task value — stop"
        return None

    def next_action(self, st: TaskState, failure_type: Optional[str] = None) -> str:
        """Decide the next action given current state (+ optional failure)."""
        stop = self.should_stop(st)
        if stop:
            return f"STOP: {stop}"
        if st.same_error_count >= 2:
            return "switch_to_fallback"
        if st.quality_score < self.reg.scoring.get("thresholds", {}).get("quality_floor", 0.7):
            return "send_to_verifier_or_stronger_model"
        if st.complexity_escalated:
            return "escalate_to_stronger_model"
        if st.quota_low:
            return "downgrade_or_reserve_model"
        if failure_type:
            return self.diagnose_action(failure_type)
        return "continue"
