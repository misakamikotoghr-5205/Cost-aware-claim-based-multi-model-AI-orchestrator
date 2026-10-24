"""Headless application services for the single desktop client.

Neither importing this module nor opening the application starts extensions,
agents or network requests. Backends keep their own authentication.
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import uuid


def default_config(workspace: str | Path) -> dict:
    root = str(Path(workspace).expanduser().resolve())
    return {"workspace_root": root, "models": [], "adapters": {},
            "allowed_tools": [], "max_actions": 20, "target": "desktop", "client_id": "desktop",
            "extensions": {"discovery": {"enabled": False}, "judge": {"enabled": False}},
            "routing": {"allow_paid_api": False}}


def save_config(path: str | Path, config: dict) -> None:
    """Atomically replace user configuration; never persist account credentials."""
    root = Path(config["workspace_root"]).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Choose an existing workspace directory")
    if "token" in config.get("server", {}):
        raise ValueError("Use a token environment variable, not a token in settings")
    normalized = deepcopy(config)
    normalized["workspace_root"] = str(root)
    data = json.dumps(normalized, ensure_ascii=False, indent=2, allow_nan=False)
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                                         delete=False) as stream:
            temporary = stream.name
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary and Path(temporary).exists():
            Path(temporary).unlink()


def load_config(path: str | Path) -> dict:
    with Path(path).expanduser().open("rb") as stream:
        raw = stream.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("Configuration is too large")
    config = json.loads(raw)
    if not isinstance(config, dict) or not isinstance(config.get("models", []), list):
        raise ValueError("Invalid client configuration")
    config["workspace_root"] = str(Path(config["workspace_root"]).expanduser().resolve(strict=True))
    return config


def configure_backend(config: dict, *, kind: str, model: str, provider: str,
                      executable: str = "", billing_mode: str = "subscription",
                      tools: list[str] | None = None, account: str = "",
                      reservation_amount: float = 1, base_url: str = "http://127.0.0.1:11434") -> dict:
    """One form configures both a model declaration and its execution adapter."""
    if kind not in {"hermes", "deepseek", "ollama"}:
        raise ValueError("Use Hermes, DeepSeek Harness or Ollama; advanced tool bridges use JSON settings")
    if not model.strip() or not provider.strip():
        raise ValueError("Model and provider are required")
    if billing_mode not in {"local", "subscription"}:
        raise ValueError("The basic setup accepts local or subscription backends")
    if kind == "ollama" and billing_mode != "local":
        raise ValueError("Ollama setup is local; use the installed local model name")
    if kind != "ollama" and not executable.strip():
        raise ValueError("Choose the installed backend executable")
    if billing_mode != "local" and not account.strip():
        raise ValueError("Subscription backends need a quota account name")
    if isinstance(reservation_amount, bool) or not 0 < reservation_amount <= 1e15:
        raise ValueError("Reservation must be a positive amount in the account's unit")
    result = deepcopy(config)
    tools = list(tools or [])
    if kind == "ollama" and tools:
        raise ValueError("The local Ollama text backend does not execute tools")
    if kind == "hermes" and not tools:
        raise ValueError("Hermes requires explicitly enabled toolsets")
    key = "backend-" + uuid.uuid4().hex[:12]
    if kind == "hermes":
        adapter = {"kind": kind, "command": [executable], "provider": provider,
                   "allowed_toolsets": tools, "billing_mode": billing_mode}
    elif kind == "deepseek":
        adapter = {"kind": kind, "command": [executable], "configured_model": model,
                   "configured_tools": tools, "profile": "headless", "billing_mode": billing_mode}
    else:
        adapter = {"kind": kind, "base_url": base_url}
    old = next((item for item in result.get("models", []) if item["model_id"] == model), None)
    if old:
        result["adapters"].pop(old["executor"], None)
    result.setdefault("adapters", {})[key] = adapter
    # These are editable operator estimates, never vendor benchmark claims.
    card = {"model_id": model, "provider": provider, "executor": key,
            "billing_mode": billing_mode, "available": True,
            "capabilities": {"reasoning": 0.7, "code_generation": 0.7,
                             "code_architecture": 0.6, "verification": 0.6},
            "source": "operator-config", "metadata": {"required_tools": tools}}
    if account:
        card["metadata"]["account"] = account
        card["metadata"]["quota_pool_model"] = "*"
        result.setdefault("model_accounts", {})[model] = {
            "account": account, "reservation_amount": reservation_amount}
    else:
        result.setdefault("model_accounts", {}).pop(model, None)
    result["models"] = [item for item in result.get("models", []) if item["model_id"] != model] + [card]
    result["allowed_tools"] = sorted(set(result.get("allowed_tools", [])) | set(tools))
    return result


def make_payload(config: dict, text: str, *, workdir: str, tools: list[str],
                 task_type: str = "general", value_level: str = "normal") -> dict:
    if not text.strip():
        raise ValueError("Enter a task")
    root = Path(config["workspace_root"]).expanduser().resolve(strict=True)
    directory = Path(workdir).expanduser().resolve(strict=True)
    if not directory.is_dir() or not directory.is_relative_to(root):
        raise ValueError("Task directory must be inside the configured workspace")
    if not set(tools) <= set(config.get("allowed_tools", [])):
        raise ValueError("Requested tools are not enabled in client settings")
    return {"task": text.strip(), "workdir": str(directory), "tools": tools,
            "max_actions": config.get("max_actions", 20),
            "target": config.get("target", "desktop"),
            "client_id": config.get("client_id", "desktop"),
            "metadata": {"task_card": {"type": task_type, "value_level": value_level}}}


class DesktopService:
    def __init__(self, config: dict):
        self.config = deepcopy(config)

    def _ledger(self):
        from .runtime_cli import cloud_from_config
        from .quota_ledger import QuotaLedger
        if self.config.get("server"):
            return cloud_from_config(self.config)
        path = Path(self.config.get("quota_db", Path(self.config["workspace_root"]) / ".orchestrator-quota.sqlite3"))
        path.parent.mkdir(parents=True, exist_ok=True)
        return QuotaLedger(path)

    def run(self, task_id: str, payload: dict, *, execute=False):
        from .runtime_cli import Runtime
        return Runtime(self.config, ledger=self._ledger()).run(task_id, payload, execute=execute)

    def submit(self, task_id: str, payload: dict):
        from .runtime_cli import cloud_from_config
        client = cloud_from_config(self.config)
        return client.request("/v1/tasks", {"task_id": task_id, "payload": payload,
                                           "idempotency_key": task_id})

    def node_once(self):
        from .runtime_cli import node_once
        return node_once(self.config, execute=True)

    def status(self, task_id):
        from .runtime_cli import Runtime, cloud_from_config, read_json
        if self.config.get("server"):
            return cloud_from_config(self.config).task(task_id)
        return read_json(Runtime(self.config).receipt_path(task_id))

    def import_quota(self, path):
        from .quota_sources import CCSwitchQuotaSource
        if self.config.get("server"):
            raise ValueError("Import shared quota on the cloud host that owns the ledger")
        return CCSwitchQuotaSource(path).sync(self._ledger())

    def import_discovery(self, path, source, report=None):
        from .discovery import (DiscoveryCache, parse_token_scout, parse_ollama,
                                parse_free_models_monitor, read_json_file)
        spec = self.config.get("extensions", {}).get("discovery", {})
        if spec.get("enabled") is not True:
            raise ValueError("Enable the free-model discovery extension first")
        observed = Path(path).stat().st_mtime
        data = read_json_file(path)
        if source == "token-scout":
            cards = parse_token_scout(data, observed_at=observed)
        elif source == "ollama":
            cards = parse_ollama(data, observed_at=observed)
        elif source == "free-models-monitor" and report:
            observed = min(observed, Path(report).stat().st_mtime)
            cards = parse_free_models_monitor(data, read_json_file(report), observed_at=observed)
        else:
            raise ValueError("Free Models Monitor needs both a snapshot and report")
        cache = DiscoveryCache(spec["cache"], ttl_seconds=spec.get("ttl_seconds", 900))
        events = cache.apply(source, cards, complete=source != "free-models-monitor")
        return {"events": [event.to_dict() for event in events],
                "models": [card.to_dict() for card in cache.load()]}
