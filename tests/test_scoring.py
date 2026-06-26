"""Tests for scoring, quota, claims, voting, and the orchestrator decision loop.

Run: python -m pytest -q   (or: python tests/test_scoring.py)
No model calls — everything is deterministic over the shipped configs.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.orchestrator import Orchestrator          # noqa: E402
from src.quota_manager import QuotaManager         # noqa: E402
from src.registry import Registry                  # noqa: E402
from src.rerouter import Rerouter, TaskState       # noqa: E402
from src.task_card import TaskCard                 # noqa: E402

REG = Registry.load()


def test_registry_loads_agents():
    assert set(REG.agents) >= {"deepseek", "grok", "gemini", "codex", "claude_code", "gpt"}


def test_code_task_prefers_code_models():
    task = TaskCard(task_id="T1", title="impl", type="code_generation",
                    requires=["repository_editing", "implementation"], value_level="important")
    orch = Orchestrator(REG)
    dec = orch.decide(task)
    # Top pick must be a strong coding agent.
    assert dec.roles["primary"] in {"codex", "claude_code"}
    assert "verifier" in dec.roles


def test_trivial_task_avoids_expensive_models():
    task = TaskCard(task_id="T2", title="rename file", type="general", value_level="trivial")
    orch = Orchestrator(REG)
    dec = orch.decide(task)
    # Expensive tier should not win a trivial task (overkill penalty).
    assert REG.tier(dec.roles["primary"]) != "expensive"


def test_research_task_prefers_search_models():
    task = TaskCard(task_id="T3", title="research", type="research",
                    requires=["web_search", "recency_check"], value_level="normal")
    orch = Orchestrator(REG)
    dec = orch.decide(task)
    assert dec.roles["primary"] in {"grok", "gemini"}


def test_quota_scarcity_penalizes_claude():
    # claude_code ships near its reserve in the sample quota_status.
    qm = QuotaManager(REG)
    assert qm.is_scarce("claude_code") is True
    assert qm.health("claude_code") <= 0.05
    # shadow price should be high for an expensive + scarce model
    assert qm.shadow_price("claude_code") >= qm.shadow_price("deepseek")


def test_scoring_breakdown_is_explainable():
    task = TaskCard(task_id="T4", title="x", type="code_generation",
                    requires=["implementation"], value_level="important")
    orch = Orchestrator(REG)
    dec = orch.decide(task)
    top = dec.ranking[0]
    assert top.total == round(sum(top.parts.values()) - sum(top.penalties.values()), 4)
    assert "capability_fit" in top.parts


def test_rerouter_stops_trivial_after_one_retry():
    rr = Rerouter(REG)
    st = TaskState(value_level="trivial", retry_count=1)
    assert rr.next_action(st).startswith("STOP")


def test_rerouter_cost_exceeds_value_stops():
    rr = Rerouter(REG)
    st = TaskState(value_level="critical", estimated_cost=0.9, task_value=0.3)
    assert rr.next_action(st).startswith("STOP")


def test_rerouter_diagnoses_code_error():
    rr = Rerouter(REG)
    st = TaskState(value_level="important", retry_count=0, quality_score=1.0)
    assert rr.next_action(st, failure_type="code_error") == "switch_to_code_model"


def _run_all():
    fns = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
