"""Client-side model discovery without inference, credentials or implicit network calls.

Adapters consume actual upstream JSON shapes. Catalog availability and account
quota are deliberately separate; upstream static free-tier lists are candidates,
not proof of current zero pricing. See docs/discovery.md for provenance.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit


class DiscoveryError(ValueError):
    """Invalid or untrustworthy discovery input; preserve the previous cache."""


def _number(value: Any, name: str, *, minimum: float = 0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DiscoveryError(f"{name} must be a finite number")
    if not math.isfinite(value) or value < minimum:
        raise DiscoveryError(f"{name} must be finite and >= {minimum}")
    return float(value)


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
        raise DiscoveryError(f"{name} must be a nonempty string without control characters")
    return value


def _mapping(value: Any, name: str) -> dict:
    if not isinstance(value, dict):
        raise DiscoveryError(f"{name} must be an object")
    return value


def _timestamp(value: Any, name: str) -> float:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("timezone required")
            return _number(parsed.timestamp(), name)
        except ValueError as exc:
            raise DiscoveryError(f"{name} requires a UTC epoch or timezone-qualified ISO timestamp") from exc
    return _number(value, name)


def _now(value: float | None) -> float:
    return _number(time.time() if value is None else value, "now")


@dataclass(frozen=True)
class ModelCard:
    model_id: str
    provider: str
    executor: str
    capabilities: dict[str, float] = field(default_factory=dict)
    billing_mode: str = "paid_api"
    available: bool = False
    expires_at: float | None = None
    observed_at: float = 0
    quota_remaining: float | None = None
    source: str = "manual"
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("model_id", "provider", "executor", "source"):
            _text(getattr(self, name), name)
        if not isinstance(self.billing_mode, str) or self.billing_mode not in {"local", "free", "subscription", "paid_api"}:
            raise DiscoveryError("invalid billing_mode")
        if not isinstance(self.available, bool):
            raise DiscoveryError("available must be boolean")
        _number(self.observed_at, "observed_at")
        if self.expires_at is not None:
            _number(self.expires_at, "expires_at")
        if self.quota_remaining is not None:
            if _number(self.quota_remaining, "quota_remaining") > 1:
                raise DiscoveryError("quota_remaining must be a fraction from 0 to 1")
        for name, score in _mapping(self.capabilities, "capabilities").items():
            _text(name, "capability name")
            if _number(score, "capability score") > 1:
                raise DiscoveryError("capability score must be from 0 to 1")
        _mapping(self.metadata, "metadata")
        try:
            json.dumps(self.metadata, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise DiscoveryError("metadata must contain finite JSON data") from exc

    @property
    def key(self) -> tuple[str, str, str]:
        return self.provider, self.model_id, self.executor

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ModelCard":
        if not isinstance(payload, Mapping):
            raise DiscoveryError("model card must be an object")
        try:
            return cls(**dict(payload))
        except TypeError as exc:
            raise DiscoveryError("model card has missing or unsupported fields") from exc

    def is_eligible(self, now: float | None = None, max_age_seconds: float | None = None,
                    allow_paid: bool = False) -> bool:
        current = _now(now)
        if not self.available or self.observed_at > current:
            return False
        if self.expires_at is not None and self.expires_at <= current:
            return False
        if max_age_seconds is not None:
            age = _number(max_age_seconds, "max_age_seconds")
            if current - self.observed_at >= age:
                return False
        if self.quota_remaining is not None and self.quota_remaining <= 0:
            return False
        return self.billing_mode != "paid_api" or allow_paid


@dataclass(frozen=True)
class DiscoveryEvent:
    kind: str
    source: str
    model_id: str
    provider: str
    observed_at: float
    previous: dict | None = None
    current: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _decode_json(data: bytes) -> Any:
    try:
        def reject_constant(_: str) -> None:
            raise ValueError("non-finite JSON")
        def unique_pairs(pairs: list) -> dict:
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate JSON key")
                result[key] = value
            return result
        return json.loads(data, parse_constant=reject_constant, object_pairs_hook=unique_pairs)
    except (UnicodeError, ValueError) as exc:
        raise DiscoveryError("invalid JSON discovery data") from exc


def read_json_file(path: str | Path, *, max_bytes: int = 4 * 1024 * 1024) -> Any:
    """Read a caller-selected UTF-8 JSON file; never execute catalog contents."""
    with Path(path).open("rb") as stream:
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise DiscoveryError("discovery JSON exceeds size limit")
    return _decode_json(data)


def run_json_command(argv: Sequence[str], *, input_payload: dict | None = None,
                     timeout_seconds: float = 30, max_bytes: int = 4 * 1024 * 1024,
                     accepted_returncodes: tuple[int, ...] = (0,)) -> Any:
    """Run explicit operator-configured argv, never a shell or catalog command.

    Token Scout Rust accepts JSON-RPC on stdin. FMM exit 2 means a successful
    change report; callers may explicitly accept (0, 2), but not exit 1 errors.
    Child output is not echoed because stderr could contain credentials.
    """
    if isinstance(argv, (str, bytes)) or not argv or any(not isinstance(x, str) or not x for x in argv):
        raise DiscoveryError("command must be a nonempty argv list, not a shell string")
    _number(timeout_seconds, "timeout_seconds", minimum=0.001)
    request = None if input_payload is None else json.dumps(input_payload, allow_nan=False).encode()
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        try:
            result = subprocess.run(list(argv), input=request, stdout=output, stderr=errors,
                                    shell=False, timeout=timeout_seconds, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DiscoveryError("discovery command failed or timed out") from exc
        if result.returncode not in accepted_returncodes:
            raise DiscoveryError(f"discovery command returned {result.returncode}")
        output.seek(0)
        data = output.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise DiscoveryError("discovery command JSON exceeds size limit")
    return _decode_json(data)


def _endpoint(value: Any) -> str:
    if not value:
        return ""
    if not isinstance(value, str):
        raise DiscoveryError("endpoint must be a string")
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("invalid endpoint")
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        netloc = host if parsed.port is None else f"{host}:{parsed.port}"
        return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
    except ValueError as exc:
        raise DiscoveryError("invalid HTTP endpoint") from exc


def _unwrap(payload: Any) -> dict:
    data = _mapping(payload, "discovery response")
    if data.get("isError") is True:
        raise DiscoveryError("MCP discovery returned an error")
    if "jsonrpc" in data:
        if data.get("jsonrpc") != "2.0" or "error" in data or "result" not in data:
            raise DiscoveryError("invalid or failed JSON-RPC discovery response")
        data = _mapping(data["result"], "JSON-RPC result")
    # MCP implementations may wrap the exact result in text content.
    if "content" in data and "matches" not in data:
        blocks = data["content"]
        if not isinstance(blocks, list) or len(blocks) != 1 or not isinstance(blocks[0], dict):
            raise DiscoveryError("expected one JSON MCP content block")
        if blocks[0].get("type") != "text" or not isinstance(blocks[0].get("text"), str):
            raise DiscoveryError("expected JSON MCP text")
        data = _mapping(_decode_json(blocks[0]["text"].encode()), "MCP discovery result")
    return data


def _capabilities(strengths: Any, compat: dict | None = None) -> dict[str, float]:
    if not isinstance(strengths, list) or any(not isinstance(item, str) for item in strengths):
        raise DiscoveryError("strengths must be a string list")
    # These are declared feature indicators, not measured quality benchmarks.
    aliases = {"code": "code_generation", "reasoning": "reasoning", "chat": "final_writing",
               "deep-context": "long_context", "vision": "multimodal", "writing": "final_writing"}
    result = {aliases[item]: 0.5 for item in strengths if item in aliases}
    if compat and "image" in str(compat.get("modality", "")):
        result["multimodal"] = 0.5
    return result


def _price(value: Any, name: str) -> float:
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError as exc:
            raise DiscoveryError(f"{name} is not a valid price") from exc
    return _number(value, name)


def _deduplicate(cards: list[ModelCard]) -> list[ModelCard]:
    keys = [card.key for card in cards]
    if len(set(keys)) != len(keys):
        raise DiscoveryError("duplicate model identities in a source")
    return cards


def parse_token_scout(payload: Any, *, observed_at: float | None = None,
                      executor: str = "local_agent") -> list[ModelCard]:
    """Import Rust ScoutResult matches or its JSON-RPC/MCP wrapper.

    Upstream static fallback fills zero prices, and results omit provenance.
    Therefore cloud results are unverified candidates (available=False); use a
    fresh FMM/OpenRouter observation to establish zero pricing. Ollama-style
    results establish a local inventory only. No upstream quota is authoritative.
    """
    data = _unwrap(payload)
    rows = data.get("matches")
    if not isinstance(rows, list):
        raise DiscoveryError("Token Scout query must contain matches; status-only output lacks prices")
    observed = _now(observed_at)
    cards = []
    for entry in rows:
        row = _mapping(entry, "Token Scout match")
        model = _text(row.get("model"), "Token Scout model")
        provider = _text(row.get("provider"), "Token Scout provider").lower()
        compat = _mapping(row.get("compat", {}), "compat")
        strengths = row.get("strengths", [])
        # Missing prices are unknown, never equivalent to free.
        prices = None
        if "prompt_cost" in row and "completion_cost" in row:
            prices = [_price(row["prompt_cost"], "prompt_cost"),
                      _price(row["completion_cost"], "completion_cost")]
        local = row.get("api_style") == "ollama" and not _remote_ollama(model, row)
        billing = "local" if local else "free" if prices == [0, 0] else "paid_api"
        metadata = {"endpoint": _endpoint(row.get("endpoint")), "compat": compat,
                    "pricing_evidence": "local_inventory" if local else "unverified_scout_report",
                    "quota_evidence": "upstream_process_estimate_not_account_quota"}
        if prices is not None:
            metadata.update(prompt_cost=prices[0], completion_cost=prices[1])
        cards.append(ModelCard(model, provider, "ollama" if local else executor,
                               capabilities=_capabilities(strengths, compat), billing_mode=billing,
                               available=local, observed_at=observed, source="token-scout",
                               expires_at=None if row.get("expires_at") is None else _timestamp(row["expires_at"], "expires_at"),
                               metadata=metadata))
    return _deduplicate(cards)


def parse_free_models_monitor(snapshot: Any, report: Any, *, observed_at: float | None = None,
                              executor: str = "local_agent") -> list[ModelCard]:
    """Read FMM snapshot.json together with the same run's --format json report.

    The report contains diffs, not a full catalog. Cached/failed/manual providers
    never become fresh merely because their snapshot was imported again.
    """
    inventory = _mapping(snapshot, "FMM snapshot")
    report = _mapping(report, "FMM report")
    statuses = _mapping(report.get("provider_status"), "provider_status")
    counts = _mapping(report.get("provider_counts"), "provider_counts")
    if not isinstance(report.get("complete"), bool) or not isinstance(report.get("errors"), list):
        raise DiscoveryError("FMM report requires complete and errors")
    if report["complete"] and report["errors"]:
        raise DiscoveryError("FMM report has contradictory completion status")
    allowed_statuses = {"fresh", "static_unverified", "cached", "unavailable"}
    for provider, status in statuses.items():
        _text(provider, "FMM provider")
        if status not in allowed_statuses:
            raise DiscoveryError("unknown FMM provider status")
        if status == "fresh" and provider not in counts:
            raise DiscoveryError("fresh FMM provider needs an observed count")
    observed = _now(observed_at)
    cards = []
    actual_counts: dict[str, int] = {}
    for raw_id, entry in inventory.items():
        raw_id = _text(raw_id, "FMM model id")
        row = _mapping(entry, "FMM model")
        # Old FMM snapshots used raw OpenRouter IDs; new snapshots prefix provider.
        if raw_id.startswith(("openrouter/", "groq/")):
            provider, model = raw_id.split("/", 1)
        else:
            provider, model = "openrouter", raw_id
        _text(model, "FMM model id")
        context = row.get("context_length", 0)
        if not isinstance(context, int) or isinstance(context, bool) or context < 0:
            raise DiscoveryError("invalid FMM context_length")
        actual_counts[provider] = actual_counts.get(provider, 0) + 1
        status = statuses.get(provider, "unavailable")
        verified = provider == "openrouter" and status == "fresh" and row.get("free_tier_verified", True) is True
        cards.append(ModelCard(model, provider, executor, capabilities={},
                               billing_mode="free" if verified else "paid_api", available=verified,
                               observed_at=observed, source="free-models-monitor",
                               metadata={"provider_status": status, "context_length": context,
                                         "pricing_evidence": "fmm_zero_prompt_completion" if verified else "unverified",
                                         "quota_evidence": "unknown"}))
    for provider, count in counts.items():
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise DiscoveryError("invalid FMM provider count")
        if statuses.get(provider) == "fresh" and actual_counts.get(provider, 0) != count:
            raise DiscoveryError("FMM snapshot/report count mismatch; import files from the same run")
    return _deduplicate(cards)


def _remote_ollama(model: str, row: dict) -> bool:
    return bool(row.get("remote_host") or row.get("remote_model") or
                model.lower().endswith((":cloud", "-cloud")))


def parse_ollama(payload: Any, *, observed_at: float | None = None,
                 executor: str = "ollama") -> list[ModelCard]:
    """Import GET /api/tags JSON. This inventories installed models, not /show capabilities."""
    data = _mapping(payload, "Ollama tags")
    if not isinstance(data.get("models"), list):
        raise DiscoveryError("Ollama tags must contain a models list")
    observed = _now(observed_at)
    cards = []
    for entry in data["models"]:
        row = _mapping(entry, "Ollama model")
        model = _text(row.get("name", row.get("model")), "Ollama model name")
        digest = row.get("digest", "")
        if not isinstance(digest, str):
            raise DiscoveryError("Ollama digest must be a string")
        remote = _remote_ollama(model, row)
        cards.append(ModelCard(model, "ollama", executor, capabilities={},
                               billing_mode="paid_api" if remote else "local",
                               available=not remote, observed_at=observed, source="ollama",
                               metadata={"digest": digest, "capability_evidence": "unknown_tags_only",
                                         "pricing_evidence": "local_inventory", "quota_evidence": "local"}))
    return _deduplicate(cards)


class DiscoveryCache:
    """Single-client atomic snapshot cache; not a shared quota ledger.

    TTL never renews on read, and a looser TTL after reopening cannot extend a
    stored observation. Mutations replace a source only after full validation.
    """
    def __init__(self, path: str | Path, ttl_seconds: float = 900):
        self.path = Path(path)
        self.ttl_seconds = _number(ttl_seconds, "ttl_seconds", minimum=0.001)

    def _read(self) -> dict:
        if not self.path.exists():
            return {"schema_version": 1, "sources": {}}
        data = _mapping(read_json_file(self.path), "discovery cache")
        if data.get("schema_version") != 1 or isinstance(data["schema_version"], bool):
            raise DiscoveryError("unsupported discovery cache schema")
        for source, state in _mapping(data.get("sources"), "cache sources").items():
            _text(source, "source")
            state = _mapping(state, "source state")
            _number(state.get("valid_until"), "valid_until")
            _number(state.get("observed_at"), "observed_at")
            if not isinstance(state.get("cards"), list):
                raise DiscoveryError("cache cards must be a list")
            cards = [ModelCard.from_dict(item) for item in state["cards"]]
            if any(card.source != source for card in cards):
                raise DiscoveryError("cached card source mismatch")
            _deduplicate(cards)
            deadlines = _mapping(state.get("card_deadlines"), "card_deadlines")
            for card in cards:
                _number(deadlines.get(json.dumps(card.key)), "card deadline")
        return data

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix=f".{self.path.name}.", delete=False) as stream:
                temporary = stream.name
                json.dump(data, stream, ensure_ascii=False, allow_nan=False, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    def apply(self, source: str, cards: Sequence[ModelCard], *, now: float | None = None,
              complete: bool = True) -> list[DiscoveryEvent]:
        source = _text(source, "source")
        current = _now(now)
        if not isinstance(complete, bool):
            raise DiscoveryError("complete must be boolean")
        checked = []
        for card in cards:
            if not isinstance(card, ModelCard):
                raise DiscoveryError("cache input must contain ModelCard instances")
            card = ModelCard.from_dict(card.to_dict())
            if card.source != source or card.observed_at > current:
                raise DiscoveryError("card source mismatch or future observation")
            checked.append(card)
        _deduplicate(checked)
        data = self._read()
        previous_state = data["sources"].get(source, {})
        old = {card.key: card for card in (ModelCard.from_dict(item) for item in previous_state.get("cards", []))}
        new = {card.key: card for card in checked}
        if not complete:
            # Partial observations cannot certify absence; preserve old timestamps.
            new = {**old, **new}
        events = []
        for key in sorted(old.keys() | new.keys()):
            before, after = old.get(key), new.get(key)
            if before is None:
                kind = "added"
            elif after is None:
                kind = "removed"
            else:
                a, b = before.to_dict(), after.to_dict()
                a.pop("observed_at"); b.pop("observed_at")
                if a == b:
                    continue
                kind = "updated"
            card = after or before
            events.append(DiscoveryEvent(kind, source, card.model_id, card.provider, current,
                                         before.to_dict() if before else None,
                                         after.to_dict() if after else None))
        # Per-card observed_at is also checked on load, including preserved partials.
        previous_deadlines = previous_state.get("card_deadlines", {})
        deadlines = {}
        for key, card in new.items():
            deadline = min(current, card.observed_at) + self.ttl_seconds
            if key in old and card.observed_at <= old[key].observed_at:
                deadline = min(deadline, previous_deadlines[json.dumps(key)])
            deadlines[json.dumps(key)] = deadline
        data["sources"][source] = {"observed_at": current, "valid_until": current + self.ttl_seconds,
                                   "card_deadlines": deadlines,
                                   "cards": [card.to_dict() for card in new.values()]}
        self._write(data)
        return events

    def load(self, *, now: float | None = None, include_stale: bool = False) -> list[ModelCard]:
        current = _now(now)
        result: dict[tuple[str, str, str], ModelCard] = {}
        for state in self._read()["sources"].values():
            for item in state["cards"]:
                card = ModelCard.from_dict(item)
                expired = card.expires_at is not None and card.expires_at <= current
                stale = (state["card_deadlines"][json.dumps(card.key)] <= current or
                         current - card.observed_at >= self.ttl_seconds)
                eligible = card.is_eligible(current, max_age_seconds=self.ttl_seconds, allow_paid=True) and not stale
                if not eligible:
                    if not include_stale:
                        continue
                    card = replace(card, available=False, metadata={**card.metadata,
                                   "discovery_status": "expired" if expired else "stale" if stale else "unavailable"})
                prior = result.get(card.key)
                if prior is None or (card.available, card.observed_at) > (prior.available, prior.observed_at):
                    result[card.key] = card
        return sorted(result.values(), key=lambda card: card.key)
