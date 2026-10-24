"""Explicit public catalog/local inventory refresh; never model inference or purchases."""
from pathlib import Path
import hashlib
import importlib.util
import ipaddress
import json
import sys
import time
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
from urllib.parse import urlsplit

_bridge = Path(__file__).with_name("bridge.py")
_name = "orchestrator_bridge_" + hashlib.sha256(str(_bridge).encode()).hexdigest()[:12]
if _name not in sys.modules:
    _spec = importlib.util.spec_from_file_location(_name, _bridge)
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[_name] = _mod
    _spec.loader.exec_module(_mod)
core = sys.modules[_name].core
ID = "free-models"
DEFAULTS = {"ollama_url": "http://127.0.0.1:11434", "openrouter": True, "ollama": True, "ttl_seconds": 900}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Discovery redirects are not followed")


def settings():
    return {**DEFAULTS, **core().read_state(ID, DEFAULTS)}


def save(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise ValueError("Unknown free model discovery setting")
    config = {**DEFAULTS, **value}
    core()._safe(config)
    if type(config["openrouter"]) is not bool or type(config["ollama"]) is not bool:
        raise ValueError("Discovery sources must be boolean")
    if type(config["ttl_seconds"]) not in (int, float) or not 1 <= config["ttl_seconds"] <= 86400:
        raise ValueError("Discovery TTL must be from 1 to 86400 seconds")
    _local_origin(config["ollama_url"])
    return core().write_state(ID, config)


def cache():
    return core().embedded("discovery").DiscoveryCache(core().state_path(ID, "models.json"), settings()["ttl_seconds"])


def models():
    return cache().load()


def _local_origin(value):
    parsed = urlsplit(value)
    try:
        local = ipaddress.ip_address(parsed.hostname or "").is_loopback
    except ValueError:
        local = parsed.hostname == "localhost"
    if (parsed.scheme not in {"http", "https"} or not local or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
        raise ValueError("Ollama discovery requires a loopback origin")
    return value.rstrip("/")


def _get(url, *, local=False):
    handlers = [NoRedirect()]
    if local:
        handlers.append(ProxyHandler({}))
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "orchestrator-free-models/0.1"})
    with build_opener(*handlers).open(request, timeout=15) as response:
        raw = response.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("Discovery response exceeds size limit")
    return json.loads(raw)


def parse_openrouter(payload, observed_at=None):
    discovery = core().embedded("discovery")
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("OpenRouter catalog requires a data list")
    observed_at = time.time() if observed_at is None else observed_at
    cards = []
    for row in payload["data"]:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]:
            raise ValueError("Invalid OpenRouter catalog entry")
        pricing = row.get("pricing")
        if not isinstance(pricing, dict) or "prompt" not in pricing or "completion" not in pricing:
            # Missing prices are unknown; do not turn malformed data into a catalog removal.
            raise ValueError("OpenRouter catalog lacks explicit pricing")
        if pricing["prompt"] in ("-1", -1) or pricing["completion"] in ("-1", -1):
            continue  # OpenRouter dynamic routers use -1 as unknown pricing, never free.
        prompt, completion = discovery._price(pricing["prompt"], "prompt price"), discovery._price(pricing["completion"], "completion price")
        if prompt != 0 or completion != 0:
            continue
        if pricing.get("overrides"):
            # Time/context dependent tiers need their own verified evaluator.
            continue
        # Other nonzero per-request/image/token fees also prevent a free classification.
        other = [discovery._price(value, "additional price") for key, value in pricing.items()
                 if key not in {"prompt", "completion", "discount", "overrides"} and value is not None]
        if any(value != 0 for value in other):
            continue
        expires = row.get("expiration_date")
        if isinstance(expires, str) and len(expires) == 10:
            expires += "T00:00:00Z"  # date-only upstream deadline: conservative UTC start
        expires_at = None if expires is None else discovery._timestamp(expires, "expiration_date")
        architecture = row.get("architecture", {})
        cards.append(discovery.ModelCard(row["id"], "openrouter", "hermes", billing_mode="free",
            available=True, observed_at=observed_at, expires_at=expires_at, source="openrouter-live",
            capabilities={}, metadata={"pricing_evidence": "public_catalog_zero", "quota_evidence": "unknown",
                "context_length": row.get("context_length"), "input_modalities": architecture.get("input_modalities", [])}))
    return discovery._deduplicate(cards)


def refresh():
    config, store, events, errors = settings(), cache(), [], []
    if config["openrouter"]:
        try:
            cards = parse_openrouter(_get("https://openrouter.ai/api/v1/models"))
            events.extend(store.apply("openrouter-live", cards))
        except Exception as error:
            errors.append({"source": "openrouter-live", "error": type(error).__name__})
    if config["ollama"]:
        try:
            # Validate loopback again at use time, even when loading old settings.
            origin = _local_origin(config["ollama_url"])
            cards = core().embedded("discovery").parse_ollama(_get(origin + "/api/tags", local=True), executor="hermes")
            events.extend(store.apply("ollama", cards))
        except Exception as error:
            errors.append({"source": "ollama", "error": type(error).__name__})
    return {"models": [card.to_dict() for card in store.load()],
            "events": [event.to_dict() for event in events], "errors": errors, "complete": not errors}


def import_models(body):
    discovery = core().embedded("discovery")
    source, payload = body["source"], body["payload"]
    observed = body.get("observed_at")
    if observed is None:
        raise ValueError("Imported catalogs require the source observation timestamp")
    if source == "token-scout":
        cards = discovery.parse_token_scout(payload, observed_at=observed, executor="hermes")
    elif source == "free-models-monitor":
        cards = discovery.parse_free_models_monitor(payload, body["report"], observed_at=observed, executor="hermes")
    elif source == "ollama":
        cards = discovery.parse_ollama(payload, observed_at=observed, executor="hermes")
    else:
        raise ValueError("Unsupported discovery import source")
    events = cache().apply(source, cards, complete=body.get("report", {}).get("complete", True))
    return {"models": [card.to_dict() for card in models()], "events": [event.to_dict() for event in events]}


from contextlib import contextmanager
import threading
_ACTIVE_OPERATIONS = {}
_ACTIVITY_LOCK = threading.RLock()


@contextmanager
def operation_scope():
    owner = str(core().home())
    with _ACTIVITY_LOCK:
        _ACTIVE_OPERATIONS[owner] = _ACTIVE_OPERATIONS.get(owner, 0) + 1
    try:
        yield
    finally:
        with _ACTIVITY_LOCK:
            _ACTIVE_OPERATIONS[owner] -= 1


def is_busy():
    owner = str(core().home())
    with _ACTIVITY_LOCK:
        active = _ACTIVE_OPERATIONS.get(owner, 0) > 0
    return active
