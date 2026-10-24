"""Offline admission, selection and replay-safety tests for the client router."""
from dataclasses import replace
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.client_router import ClientRouter, OllamaLocalJudge, RoutingPolicy
from src.discovery import ModelCard
from src.executors import ExecutionRequest, ExecutionResult
from src.registry import Registry
from src.task_card import ScoreBreakdown, TaskCard


NOW = 2_000_000_000.0


def card(model_id="local-a", **changes):
    values = dict(model_id=model_id, provider="ollama", executor=model_id,
                  billing_mode="local", available=True, observed_at=NOW,
                  capabilities={"reasoning": 0.8, "code_generation": 0.8})
    values.update(changes)
    return ModelCard(**values)


def snapshot(**changes):
    values = dict(account="account-a", model="subscription-a", unit="requests", available=80,
                  capacity=100, fresh=True, status="ready", observed_at=NOW,
                  max_age_seconds=300, reset_at=NOW + 3600)
    values.update(changes)
    return values


class FakeAdapter:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def execute(self, request):
        self.calls.append(request)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class ClientRouterTests(unittest.TestCase):
    def setUp(self):
        self.task = TaskCard("task-1", "routine transformation", "general")
        self.router = ClientRouter()

    def route(self, cards, **kwargs):
        return self.router.route(self.task, cards, now=NOW, **kwargs)

    def subscription(self, **changes):
        values = dict(billing_mode="subscription", provider="vendor",
                      metadata={"account": "account-a"}, quota_remaining=1)
        values.update(changes)
        return card("subscription-a", **values)

    def test_local_default_uses_existing_scorer_without_network_or_judge(self):
        with patch("src.client_router.build_opener", side_effect=AssertionError("network forbidden")):
            with patch("src.client_router.ScoringEngine.rank", return_value=[
                    ScoreBreakdown("local-b", 0.65), ScoreBreakdown("local-a", 0.6)]) as rank:
                result = self.route([card(), card("local-b")])
        self.assertEqual(result.selected.model_id, "local-b")
        rank.assert_called_once()
        self.assertEqual(result.to_dict()["selected"]["billing_mode"], "local")

    def test_static_registry_and_sample_quota_are_not_runtime_evidence(self):
        registry = Registry.load()
        before = repr(registry)
        result = ClientRouter(registry).route(self.task, [self.subscription(provider="codex")], now=NOW)
        self.assertIsNone(result.selected)
        self.assertIn("subscription_live_quota_required", result.rejected["subscription-a"])
        self.assertEqual(repr(registry), before)

    def test_paid_api_defaults_disabled_even_with_known_quota(self):
        result = self.route([card("paid", billing_mode="paid_api", quota_remaining=1)])
        self.assertIn("paid_api_disabled", result.rejected["paid"])
        opted = ClientRouter(policy=RoutingPolicy(allow_paid_api=True)).route(
            self.task, [card("paid", billing_mode="paid_api", quota_remaining=1)], now=NOW)
        self.assertEqual(opted.selected.model_id, "paid")

    def test_unknown_free_quota_stale_quota_and_expired_offers_are_rejected(self):
        result = self.route([
            card("unknown", billing_mode="free"),
            card("stale", billing_mode="free", quota_remaining=1, observed_at=NOW - 301),
            card("expired", billing_mode="free", quota_remaining=1, expires_at=NOW),
            card("offline", available=False),
        ])
        self.assertIsNone(result.selected)
        self.assertIn("unknown_or_invalid_quota", result.rejected["unknown"])
        self.assertIn("stale_quota", result.rejected["stale"])
        self.assertIn("expired_offer", result.rejected["expired"])
        self.assertIn("unavailable", result.rejected["offline"])

    def test_future_and_stale_card_observations_are_rejected(self):
        result = self.route([card("future", observed_at=NOW + 1), card("old", observed_at=NOW - 901)])
        self.assertIsNone(result.selected)
        self.assertIn("stale_model_card", result.rejected["future"])
        self.assertIn("stale_model_card", result.rejected["old"])

    def test_tools_must_be_available_on_this_client(self):
        c = card(metadata={"required_tools": ["mcp.files"]})
        result = self.route([c], required_tools=["browser"], available_tools=["browser"])
        self.assertIsNone(result.selected)
        self.assertIn("tools_unavailable:mcp.files", result.rejected[c.model_id])
        self.assertIsNotNone(self.route([c], required_tools=["browser"],
                                       available_tools=["browser", "mcp.files"]).selected)

    def test_tool_generators_apply_to_every_candidate(self):
        result = self.route([card("a"), card("b")], required_tools=iter(["computer.act"]))
        self.assertIsNone(result.selected)
        self.assertEqual(set(result.rejected), {"a", "b"})

    def test_ambiguous_duplicate_identity_cannot_dispatch(self):
        result = self.route([card(), card(provider="different")])
        self.assertIsNone(result.selected)
        self.assertIn("duplicate_model_id", result.rejected["local-a"])

    def test_subscription_requires_fresh_exact_identity_and_known_capacity(self):
        for bad in [snapshot(account="other"), snapshot(model="other"),
                    snapshot(fresh=False), snapshot(observed_at=NOW - 301),
                    snapshot(status="blocked"), snapshot(blocked=True),
                    snapshot(capacity=None), snapshot(reset_at=NOW)]:
            with self.subTest(snapshot=bad):
                self.assertIsNone(self.route([self.subscription()], quota_states={"subscription-a": bad}).selected)
        self.assertIsNotNone(self.route([self.subscription()],
                                       quota_states={"subscription-a": snapshot()}).selected)

    def test_shared_pool_is_only_used_when_explicitly_mapped(self):
        shared = snapshot(model="*")
        self.assertIsNone(self.route([self.subscription()], quota_states={"subscription-a": shared}).selected)
        configured = self.subscription(metadata={"account": "account-a", "quota_pool_model": "*"})
        self.assertIsNotNone(self.route([configured], quota_states={"subscription-a": shared}).selected)

    def test_reserve_applies_after_reservation_quantity_and_uses_explicit_unit(self):
        candidate = self.subscription()
        result = self.route([candidate], quota_states={candidate.model_id: snapshot(available=25)},
                            quota_costs={candidate.model_id: 5})
        self.assertIsNone(result.selected)
        self.assertIn("quota_at_reserve", result.rejected[candidate.model_id])
        fractional = snapshot(unit="fraction", available=0.8, capacity=None)
        self.assertIsNotNone(self.route([candidate], quota_states={candidate.model_id: fractional}).selected)

    def test_strong_model_reserve_keeps_headroom_for_important_tasks(self):
        candidate = self.subscription(capabilities={"reasoning": 0.95})
        quota = {candidate.model_id: snapshot(available=25)}
        self.assertIsNone(self.route([candidate], quota_states=quota).selected)
        important = replace(self.task, value_level="important")
        self.assertIsNotNone(self.router.route(important, [candidate], quota_states=quota, now=NOW).selected)

    def test_expiry_priority_is_bounded_by_existing_score(self):
        free = card("free-expiring", billing_mode="free", quota_remaining=1, expires_at=NOW + 60)
        local = card()
        with patch("src.client_router.ScoringEngine.rank", return_value=[
                ScoreBreakdown(local.model_id, 0.7), ScoreBreakdown(free.model_id, 0.6)]):
            self.assertEqual(self.route([local, free]).selected.model_id, free.model_id)
        with patch("src.client_router.ScoringEngine.rank", return_value=[
                ScoreBreakdown(local.model_id, 0.9), ScoreBreakdown(free.model_id, 0.3)]):
            self.assertEqual(self.route([local, free]).selected.model_id, local.model_id)

    def test_subscription_reset_time_also_prioritizes_expiring_quota(self):
        subscription = self.subscription()
        with patch("src.client_router.ScoringEngine.rank", return_value=[
                ScoreBreakdown("local-a", 0.7), ScoreBreakdown("subscription-a", 0.6)]):
            result = self.route([card(), subscription], quota_states={"subscription-a": snapshot()})
        self.assertEqual(result.selected.model_id, "subscription-a")

    def execute(self, adapters, *, safe=False, **kwargs):
        request = ExecutionRequest(self.task.task_id, "do work", "untrusted-request-model", ".",
                                   metadata={"safe_to_retry": safe})
        return self.router.execute(self.task, [card("a"), card("b"), card("c")], request,
                                   adapters, now=NOW, **kwargs)

    def test_retryable_failed_safe_work_can_fail_over_with_bound(self):
        a = FakeAdapter(ExecutionResult("failed", retryable=True))
        b = FakeAdapter(ExecutionResult("succeeded", output="done", usage={"tokens": 2}))
        c = FakeAdapter(ExecutionResult("succeeded"))
        receipt = self.execute({"a": a, "b": b, "c": c}, safe=True)
        self.assertEqual(receipt.status, "succeeded")
        self.assertEqual([attempt.model_id for attempt in receipt.attempts], ["a", "b"])
        self.assertEqual(a.calls[0].model, "a")
        self.assertEqual(b.calls[0].metadata["billing_mode"], "local")
        self.assertFalse(c.calls)
        self.assertEqual(receipt.to_dict()["usage"], {"tokens": 2})

    def test_unknown_exception_and_unsafe_failures_never_replay(self):
        for result in [ExecutionResult("unknown"), RuntimeError("possibly ran"),
                       ExecutionResult("failed", retryable=True), ExecutionResult("failed")]:
            with self.subTest(result=result):
                a, b = FakeAdapter(result), FakeAdapter(ExecutionResult("succeeded"))
                receipt = self.execute({"a": a, "b": b})
                self.assertEqual(len(receipt.attempts), 1)
                self.assertFalse(b.calls)
        a = FakeAdapter(ExecutionResult("unknown"))
        self.assertEqual(len(self.execute({"a": a}, safe=True).attempts), 1)

    def test_attempt_bound_and_task_identity_are_enforced(self):
        adapters = {model: FakeAdapter(ExecutionResult("failed", retryable=True)) for model in "abc"}
        self.assertEqual(len(self.execute(adapters, safe=True, max_attempts=2).attempts), 2)
        with self.assertRaises(ValueError):
            self.execute(adapters, max_attempts=11)
        with self.assertRaises(ValueError):
            self.router.execute(self.task, [card()], ExecutionRequest("other", "p", "m", "."), {})

    def test_execute_checks_request_tools_against_client_availability(self):
        request = ExecutionRequest(self.task.task_id, "do work", "m", ".", tools=["computer.act"])
        adapter = FakeAdapter(ExecutionResult("succeeded"))
        receipt = self.router.execute(self.task, [card()], request, {"local-a": adapter}, now=NOW)
        self.assertEqual(receipt.status, "failed")
        self.assertFalse(adapter.calls)

    def test_nonlocal_execute_cannot_bypass_runtime_quota_reservation(self):
        request = ExecutionRequest(self.task.task_id, "work", "m", ".")
        for candidate in [card("free", billing_mode="free", quota_remaining=1), self.subscription()]:
            with self.subTest(mode=candidate.billing_mode):
                adapter = FakeAdapter(ExecutionResult("succeeded"))
                receipt = self.router.execute(self.task, [candidate], request,
                                              {candidate.executor: adapter}, now=NOW,
                                              quota_states={"subscription-a": snapshot()})
                self.assertEqual(receipt.status, "failed")
                self.assertIn("atomic quota reservation", receipt.error)
                self.assertFalse(adapter.calls)


class JudgeTests(unittest.TestCase):
    def test_remote_redirectable_and_cloud_endpoints_are_not_allowed(self):
        for url in ["http://example.com", "https://127.0.0.1/path", "http://user@localhost:11434", "http://localhost?target=remote"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                OllamaLocalJudge(base_url=url)
        for model in ["gpt:cloud", "qwen-cloud", ""]:
            with self.assertRaises(ValueError):
                OllamaLocalJudge(model=model)

    def test_judge_json_can_only_select_candidates(self):
        judge = OllamaLocalJudge()
        task = TaskCard("t", "task", "general")
        opener = MagicMock()
        judge._opener = opener
        opener.open.return_value.__enter__.return_value = io.BytesIO(
            json.dumps({"response": '{"model_id":"local-a"}'}).encode())
        self.assertEqual(judge.choose(task, [card()], []), "local-a")
        for body in ['{"model_id":"paid"}', '{"model_id":"local-a","command":"run"}',
                     '{"model_id":"other","model_id":"local-a"}', 'text']:
            opener.open.return_value.__enter__.return_value = io.BytesIO(json.dumps({"response": body}).encode())
            with self.assertRaises(ValueError):
                judge.choose(task, [card()], [])

    def test_invalid_or_timed_out_judge_keeps_deterministic_choice(self):
        judge = OllamaLocalJudge()
        router = ClientRouter(judge=judge)
        task = TaskCard("t", "task", "general")
        for effect in [TimeoutError("timeout"), "paid-not-admitted"]:
            with patch.object(judge, "choose", side_effect=effect if isinstance(effect, Exception) else None,
                              return_value=effect):
                receipt = router.route(task, [card()], now=NOW)
            self.assertEqual(receipt.selected.model_id, "local-a")
            self.assertTrue(any("deterministic selection retained" in note for note in receipt.notes))


if __name__ == "__main__":
    unittest.main()
