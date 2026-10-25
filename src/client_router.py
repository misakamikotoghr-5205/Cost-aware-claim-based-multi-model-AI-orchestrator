"""Client-side runtime routing over discovery cards and the existing scorer.

This module never queries a hosted judge. Routing is deterministic unless an
operator explicitly installs the optional loopback-only Ollama judge. Quota
snapshots are admission hints; the shared ledger must still reserve atomically
before execution when more than one client shares an account.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
import ipaddress
import json
import math
import time
from typing import TYPE_CHECKING, Any, Iterable, Mapping
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .quota_manager import QuotaManager
from .registry import Registry
from .scoring_engine import REQUIRE_TO_DIMENSION, TYPE_TO_DIMENSIONS, ScoringEngine
from .task_card import ScoreBreakdown, TaskCard

if TYPE_CHECKING:
    from .discovery import ModelCard
    from .executors import Adapter, ExecutionRequest


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _json(value: str | bytes) -> Any:
    def unique_keys(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate judge JSON key")
            result[key] = item
        return result

    def reject_constant(value):
        raise ValueError("non-finite judge JSON value")

    return json.loads(value, object_pairs_hook=unique_keys, parse_constant=reject_constant)


def _tools(values: Iterable[str]) -> set[str]:
    if isinstance(values, (str, bytes)):
        raise ValueError("tools must be an iterable of names, not a string")
    result = set(values)
    if any(not isinstance(value, str) or not value for value in result):
        raise ValueError("tools must contain nonempty names")
    return result


@dataclass(frozen=True)
class RoutingPolicy:
    allow_paid_api: bool = False
    max_card_age_seconds: float = 900
    max_quota_age_seconds: float = 300
    subscription_reserve: float = 0.2
    strong_model_reserve: float = 0.3
    strong_threshold: float = 0.85
    expiry_window_seconds: float = 86400
    expiry_score_margin: float = 0.15

    def __post_init__(self) -> None:
        for name in ("max_card_age_seconds", "max_quota_age_seconds", "expiry_window_seconds"):
            value = _number(getattr(self, name))
            if value is None or value <= 0:
                raise ValueError(f"{name} must be a positive finite number")
        for name in ("subscription_reserve", "strong_model_reserve", "strong_threshold", "expiry_score_margin"):
            value = _number(getattr(self, name))
            if value is None or not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        if type(self.allow_paid_api) is not bool:
            raise ValueError("allow_paid_api must be boolean")


@dataclass
class RouteReceipt:
    task_id: str
    selected: ModelCard | None = None
    candidates: list[ModelCard] = field(default_factory=list)
    ranking: list[ScoreBreakdown] = field(default_factory=list)
    reserve_fractions: dict[str, float] = field(default_factory=dict)
    rejected: dict[str, list[str]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "selected": self.selected.to_dict() if self.selected else None,
            "candidates": [card.to_dict() for card in self.candidates],
            "ranking": [asdict(score) for score in self.ranking],
            "reserve_fractions": self.reserve_fractions,
            "rejected": self.rejected,
            "notes": self.notes,
        }


@dataclass
class AttemptReceipt:
    model_id: str
    executor: str
    status: str
    retryable: bool = False
    output: str = ""
    error: str = ""
    usage: dict = field(default_factory=dict)


@dataclass
class ExecutionReceipt:
    task_id: str
    status: str
    route: RouteReceipt
    output: str = ""
    error: str = ""
    usage: dict = field(default_factory=dict)
    attempts: list[AttemptReceipt] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id, "status": self.status,
            "output": self.output, "error": self.error, "usage": self.usage,
            "route": self.route.to_dict(), "attempts": [asdict(attempt) for attempt in self.attempts],
        }


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("local judge redirects are forbidden")


class OllamaLocalJudge:
    """Optional local judge over a small, explicitly abbreviated routing view.

    The router still performs full admission/scoring. This view is only for a
    bounded tie-break and never expands the judge's authority.
    """

    MAX_PROMPT_BYTES = 8192

    def __init__(self, base_url: str = "http://127.0.0.1:11434", model: str = "qwen3:4b",
                 timeout: float = 5.0, *, max_task_chars: int = 512, max_candidates: int = 3):
        parsed = urlsplit(base_url)
        try:
            loopback = ipaddress.ip_address(parsed.hostname or "").is_loopback
        except ValueError:
            loopback = parsed.hostname == "localhost"
        if (parsed.scheme not in {"http", "https"} or not loopback
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in {"", "/"}):
            raise ValueError("Ollama judge base_url must be a loopback origin")
        if not isinstance(model, str) or not model.strip() or "cloud" in model.lower():
            raise ValueError("judge model must name a local Ollama model")
        if _number(timeout) is None or not 0 < timeout <= 60:
            raise ValueError("judge timeout must be between 0 and 60 seconds")
        if type(max_task_chars) is not int or not 128 <= max_task_chars <= 2048:
            raise ValueError("judge max_task_chars must be an integer between 128 and 2048")
        if type(max_candidates) is not int or not 1 <= max_candidates <= 8:
            raise ValueError("judge max_candidates must be an integer between 1 and 8")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_task_chars = max_task_chars
        self.max_candidates = max_candidates
        # Loopback requests must not inherit an outbound HTTP proxy or redirect
        # to another origin. No external service is consulted for judging.
        self._opener = build_opener(ProxyHandler({}), _NoRedirect())

    def choose(self, task: TaskCard, cards: list[ModelCard],
               ranking: list[ScoreBreakdown]) -> str:
        # Input order comes from the existing score/expiry shortlist. Do not
        # rerank here or inspect candidate metadata, history, outputs or files.
        focused = cards[:self.max_candidates]
        allowed = {card.model_id for card in focused}
        if not allowed or any(len(model_id.encode("utf-8")) > 256 for model_id in allowed):
            raise ValueError("judge candidate identity exceeds the bounded routing view")
        title = str(task.title)
        shortened = len(title) > self.max_task_chars
        if shortened:
            # Keep both the initial request and final constraints, marking the
            # omission rather than presenting this excerpt as a full reading.
            head = (self.max_task_chars - 3) * 3 // 4
            title = title[:head] + " … " + title[-(self.max_task_chars - head - 3):]
        requires = [value for value in task.requires[:8] if isinstance(value, str)]
        dimensions = []
        for dimension in ([REQUIRE_TO_DIMENSION.get(token) for token in requires]
                          + TYPE_TO_DIMENSIONS.get(task.type, ["reasoning"])):
            if dimension and dimension not in dimensions:
                dimensions.append(dimension)
        dimensions = dimensions[:4] or ["reasoning"]
        prompt = json.dumps({
            "instruction": "Select one candidate using this partial routing summary, not a full task review. "
                           "Return only JSON with one model_id key.",
            "scope": {"title_abbreviated": shortened,
                      "requirements_omitted": max(0, len(task.requires) - len(requires)),
                      "requirement_text_abbreviated": any(len(value) > 64 for value in requires),
                      "candidates_omitted": len(cards) - len(focused)},
            "task": {"title": title, "type": str(task.type)[:64],
                     "requires": [value[:64] for value in requires],
                     "value_level": task.value_level},
            "candidates": [{"model_id": card.model_id,
                            "capabilities": {dimension: card.capabilities[dimension]
                                             for dimension in dimensions if dimension in card.capabilities},
                            "billing_mode": card.billing_mode} for card in focused],
            "scores": {score.agent: score.total for score in ranking if score.agent in allowed},
        }, ensure_ascii=False)
        if len(prompt.encode("utf-8")) > self.MAX_PROMPT_BYTES:
            raise ValueError("judge routing summary exceeds the input budget")
        schema = {"type": "object", "properties": {"model_id": {"type": "string", "enum": sorted(allowed)}},
                  "required": ["model_id"], "additionalProperties": False}
        payload = json.dumps({"model": self.model, "prompt": prompt, "format": schema,
                              "stream": False, "think": False,
                              "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 128}}).encode()
        request = Request(self.base_url + "/api/generate", data=payload,
                          headers={"Content-Type": "application/json"}, method="POST")
        with self._opener.open(request, timeout=self.timeout) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("judge response too large")
        envelope = _json(raw)
        if (not isinstance(envelope, dict) or not isinstance(envelope.get("response"), str)
                or envelope.get("done") is not True or envelope.get("error")
                or envelope.get("done_reason") not in {None, "stop"}):
            raise ValueError("invalid Ollama response envelope")
        result = _json(envelope["response"])
        if (not isinstance(result, dict) or set(result) != {"model_id"}
                or not isinstance(result["model_id"], str) or result["model_id"] not in allowed):
            raise ValueError("judge returned an unapproved model")
        return result["model_id"]


class ClientRouter:
    def __init__(self, registry: Registry | None = None, policy: RoutingPolicy | None = None,
                 judge: OllamaLocalJudge | None = None):
        self.registry = registry if registry is not None else Registry.load()
        self.policy = policy if policy is not None else RoutingPolicy()
        if judge is not None and not isinstance(judge, OllamaLocalJudge):
            raise TypeError("judge must be an explicitly configured OllamaLocalJudge")
        self.judge = judge

    def route(self, task: TaskCard, cards: Iterable[ModelCard], *,
              available_tools: Iterable[str] = (), required_tools: Iterable[str] = (),
              quota_states: Mapping[str, Mapping[str, Any]] | None = None,
              quota_costs: Mapping[str, float] | None = None,
              now: float | None = None) -> RouteReceipt:
        """Filter live cards, then reuse ScoringEngine to rank admitted models.

        Quota states/costs are keyed by exact execution model_id; snapshot.account
        must match metadata.account. snapshot.model matches metadata.quota_pool_model
        when explicitly configured, otherwise model_id. Costs use snapshot.unit.
        No sample YAML quota state is accepted as live evidence.
        """
        now = _number(time.time() if now is None else now)
        if now is None or now < 0:
            raise ValueError("now must be finite epoch seconds")
        receipt = RouteReceipt(task.task_id)
        supplied = list(cards)
        ids = [card.model_id for card in supplied]
        duplicates = {model_id for model_id in ids if ids.count(model_id) > 1}
        live: dict[str, tuple[ModelCard, float, float]] = {}
        tools = _tools(available_tools)
        required = _tools(required_tools)
        for card in supplied:
            reasons, fraction, reserve = self._admit(
                task, card, tools, required, quota_states or {}, quota_costs or {}, now)
            if card.model_id in duplicates:
                reasons.append("duplicate_model_id")
            if reasons:
                receipt.rejected[card.model_id] = sorted(set(reasons))
            else:
                live[card.model_id] = (card, fraction, reserve)
        if not live:
            receipt.notes.append("No model passed runtime policy; execution must pause.")
            return receipt

        dynamic = Registry(
            scoring=deepcopy(self.registry.scoring), routing=deepcopy(self.registry.routing),
            capabilities={"agents": {}}, prices={"prices": {}},
            quota={"policy": {}, "quota_status": {}},
        )
        for model_id, (card, fraction, reserve) in live.items():
            receipt.reserve_fractions[model_id] = reserve
            tier = "expensive" if card.billing_mode in {"subscription", "paid_api"} and self._strong(card) else "mid"
            dynamic.capabilities["agents"][model_id] = {"capabilities": dict(card.capabilities), "tier": tier}
            price = {"local": 0.0, "free": 0.0, "subscription": 0.3, "paid_api": 1.0}[card.billing_mode]
            dynamic.prices["prices"][model_id] = {"unit_cost": price}
            dynamic.quota["policy"][model_id] = {"minimum_reserve": reserve}
            dynamic.quota["quota_status"][model_id] = {"remaining": fraction}
        scorer = ScoringEngine(dynamic, QuotaManager(dynamic))
        receipt.ranking = scorer.rank(task, agents=sorted(live))
        scores = {score.agent: score.total for score in receipt.ranking}
        ordered = [live[score.agent][0] for score in receipt.ranking]
        top = receipt.ranking[0].total
        shortlist = [card for card in ordered if scores[card.model_id] >= top - self.policy.expiry_score_margin]
        expiries = {}
        for card in shortlist:
            deadlines = [value for value in (
                _number(card.expires_at),
                _number((quota_states or {}).get(card.model_id, {}).get("reset_at")),
            ) if value is not None and value > now]
            if deadlines:
                expiries[card.model_id] = min(deadlines)
        expiring = [card for card in shortlist if card.model_id in expiries
                    and expiries[card.model_id] - now <= self.policy.expiry_window_seconds]
        if expiring:
            expiring.sort(key=lambda card: (expiries[card.model_id], -scores[card.model_id], card.model_id))
            shortlist = expiring
            selected = expiring[0]
            receipt.notes.append("Prefer soon-expiring quota among models within the configured score margin.")
        else:
            selected = ordered[0]
        if self.judge is not None and len(shortlist) > 1 and self.judge.max_candidates > 1:
            try:
                chosen = self.judge.choose(task, shortlist, receipt.ranking)
                selected = next(card for card in shortlist if card.model_id == chosen)
                receipt.notes.append("Local Ollama judge selected an admitted candidate using a bounded routing summary.")
            except Exception as error:
                receipt.notes.append(f"Local judge unavailable or invalid ({type(error).__name__}); deterministic selection retained.")
        elif self.judge is not None:
            receipt.notes.append("Single admitted shortlist candidate; local judge skipped."
                                 if len(shortlist) == 1 else
                                 "Judge candidate limit is one; local judge skipped and deterministic shortlist preference retained.")
        receipt.selected = selected
        receipt.candidates = [selected] + [card for card in ordered if card.model_id != selected.model_id]
        receipt.notes.append("Scores use the existing local ScoringEngine; shared quota still requires an atomic reservation.")
        return receipt

    def _strong(self, card: ModelCard) -> bool:
        return any((_number(value) or 0) >= self.policy.strong_threshold for value in card.capabilities.values())

    def _admit(self, task, card, tools, required, states, costs, now):
        reasons = []
        policy = self.policy
        mode = card.billing_mode
        if not card.model_id or not card.executor or not card.provider:
            reasons.append("missing_model_identity")
        if card.available is not True:
            reasons.append("unavailable")
        if mode not in {"local", "free", "subscription", "paid_api"}:
            reasons.append("unknown_billing_mode")
        if mode == "paid_api" and not policy.allow_paid_api:
            reasons.append("paid_api_disabled")
        observed = _number(card.observed_at)
        if observed is None or observed > now or now - observed >= policy.max_card_age_seconds:
            reasons.append("stale_model_card")
        if card.expires_at is not None:
            expiry = _number(card.expires_at)
            if expiry is None or expiry <= now:
                reasons.append("expired_offer")
        card_tools = card.metadata.get("required_tools", [])
        if (not isinstance(card_tools, list)
                or any(not isinstance(tool, str) or not tool for tool in card_tools)):
            reasons.append("invalid_required_tools")
            card_tools = []
        needed = required | set(card_tools)
        if not needed <= tools:
            reasons.append("tools_unavailable:" + ",".join(sorted(needed - tools)))
        if not card.capabilities or any(_number(value) is None or not 0 <= float(value) <= 1
                                        for value in card.capabilities.values()):
            reasons.append("invalid_capabilities")

        reserve = 0.0
        fraction = 1.0 if mode == "local" else _number(card.quota_remaining)
        snapshot = states.get(card.model_id)
        if snapshot is not None and not isinstance(snapshot, Mapping):
            reasons.append("invalid_quota_snapshot")
            snapshot = None
            fraction = None
        if snapshot is not None and mode != "local":
            account = card.metadata.get("account")
            quota_model = card.metadata.get("quota_pool_model", card.model_id)
            if (snapshot.get("model") != quota_model or not account or not quota_model
                    or snapshot.get("account") != account):
                reasons.append("quota_identity_mismatch")
            observed = _number(snapshot.get("observed_at"))
            max_age = _number(snapshot.get("max_age_seconds", policy.max_quota_age_seconds))
            if (snapshot.get("fresh") is not True or snapshot.get("status") != "ready"
                    or snapshot.get("blocked") is True
                    or observed is None or observed > now or max_age is None or max_age <= 0
                    or now - observed >= min(max_age, policy.max_quota_age_seconds)):
                reasons.append("stale_quota")
            reset = _number(snapshot.get("reset_at"))
            if reset is not None and reset <= now:
                reasons.append("expired_quota_snapshot")
            available = _number(snapshot.get("available"))
            capacity = _number(snapshot.get("capacity"))
            unit = snapshot.get("unit")
            if unit == "fraction":
                capacity = 1.0
            if available is None or available < 0 or capacity is None or capacity <= 0:
                reasons.append("unknown_quota_capacity")
                fraction = None
            else:
                cost = _number(costs.get(card.model_id, 0))
                if cost is None or cost < 0:
                    reasons.append("invalid_quota_cost")
                    fraction = None
                else:
                    fraction = (available - cost) / capacity
        elif mode == "subscription":
            reasons.append("subscription_live_quota_required")
            fraction = None
        elif mode != "local" and observed is not None and now - observed >= policy.max_quota_age_seconds:
            reasons.append("stale_quota")
        if fraction is None or not 0 <= fraction <= 1:
            reasons.append("unknown_or_invalid_quota")
        else:
            if mode == "subscription":
                reserve = max(policy.subscription_reserve, self.registry.min_reserve(card.model_id),
                              self.registry.min_reserve(card.provider))
                if self._strong(card) and task.value_level in {"normal", "trivial"}:
                    reserve = max(reserve, policy.strong_model_reserve)
            if fraction <= reserve:
                reasons.append("quota_at_reserve")
        return reasons, fraction, reserve

    def execute(self, task: TaskCard, cards: Iterable[ModelCard], request: ExecutionRequest,
                adapters: Mapping[str, Adapter], *, max_attempts: int = 2, **route_options) -> ExecutionReceipt:
        """Run adapters with bounded failover. Never retry an unknown outcome.

        This convenience method executes only local models. Hosted/free or
        subscription callers must use the integrated Runtime's atomic ledger
        reservation and settlement flow, rather than bypassing account holds.
        Replay requires explicit request.metadata['safe_to_retry'] == True and a
        definitely failed, retryable adapter result. An exception is unknown.
        """
        if type(max_attempts) is not int or not 1 <= max_attempts <= 10:
            raise ValueError("max_attempts must be an integer between 1 and 10")
        if request.task_id != task.task_id:
            raise ValueError("request/task identity mismatch")
        route_options["required_tools"] = _tools(route_options.get("required_tools", ())) | _tools(request.tools)
        route = self.route(task, cards, **route_options)
        receipt = ExecutionReceipt(task.task_id, "failed", route, error="No eligible model")
        pending = list(route.candidates)
        for attempt_index in range(max_attempts):
            if not pending:
                break
            # Recheck freshness and policy before each actual attempt.
            current = route if attempt_index == 0 else self.route(task, pending, **route_options)
            if current.selected is None:
                receipt.error = "No eligible model at execution time"
                break
            card = current.selected
            pending = [candidate for candidate in pending if candidate.model_id != card.model_id]
            adapter = adapters.get(card.executor)
            metadata = dict(request.metadata)
            metadata.update({"model_card": card.to_dict(), "billing_mode": card.billing_mode})
            routed_request = replace(request, model=card.model_id, metadata=metadata)
            if card.billing_mode != "local":
                attempt = AttemptReceipt(card.model_id, card.executor, "failed", error=(
                    "Non-local execution requires the Runtime's atomic quota reservation workflow"))
            elif adapter is None:
                attempt = AttemptReceipt(card.model_id, card.executor, "failed", True, error="Executor not configured")
            else:
                try:
                    result = adapter.execute(routed_request)
                    status = result.status if result.status in {"succeeded", "failed", "unknown"} else "unknown"
                    attempt = AttemptReceipt(card.model_id, card.executor, status,
                                             result.retryable is True, result.output, result.error, dict(result.usage))
                except Exception as error:
                    attempt = AttemptReceipt(card.model_id, card.executor, "unknown",
                                             error=f"Executor outcome unknown ({type(error).__name__})")
            receipt.attempts.append(attempt)
            receipt.status, receipt.output, receipt.error = attempt.status, attempt.output, attempt.error
            receipt.usage = attempt.usage
            if not (attempt.status == "failed" and attempt.retryable
                    and request.metadata.get("safe_to_retry") is True):
                break
        return receipt
