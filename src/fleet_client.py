"""Outbound-only fleet transport; native local chat does not depend on this module.

The cloud receives workspace aliases and explicit task constraints, never local
provider OAuth credentials or API keys. Node tokens authenticate transport only.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import platform
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .runtime_cli import CloudClient, Runtime, RuntimeError, read_json, write_json


class FleetClient(CloudClient):
    def create_pairing(self, *, label="Local machine", accounts=None, ttl_seconds=600):
        body = {"label": label, "ttl_seconds": ttl_seconds}
        if accounts is not None:
            body["accounts"] = accounts
        return self.request("/v1/pairings", body)["pairing"]

    @classmethod
    def redeem_pairing(cls, url, code, *, label, capabilities, timeout=10, allow_http=False):
        # Reuse the hardened URL/redirect/proxy policy, but send NO Bearer header.
        connection = cls(url, "anonymous-pairing-transport", timeout=timeout, allow_http=allow_http)
        body = json.dumps({"code": code, "label": label, "capabilities": capabilities}, allow_nan=False).encode()
        request = urllib.request.Request(connection.url + "/v1/pairings/redeem", data=body,
                                         headers={"Content-Type": "application/json"})
        try:
            with connection.opener.open(request, timeout=timeout) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
        except (OSError, urllib.error.URLError):
            raise RuntimeError("Pairing was not confirmed; check the owner node list before creating a new code") from None
        if len(raw) > 2 * 1024 * 1024:
            raise RuntimeError("Pairing response is too large")
        try:
            result = json.loads(raw)
            if (not isinstance(result, dict) or not isinstance(result.get("node"), dict)
                    or not isinstance(result.get("token"), str) or len(result["token"]) < 24):
                raise ValueError
            if not all(isinstance(result["node"].get(key), str) and result["node"][key]
                       for key in ("owner_id", "node_id")):
                raise ValueError
        except (ValueError, TypeError):
            raise RuntimeError("Pairing response is invalid; reconcile from the owner node list") from None
        return result

    def nodes(self):
        return self.request("/v1/nodes")["nodes"]

    def select_node(self, **requirements):
        return self.request("/v1/nodes/select", requirements)["node"]

    def revoke_node(self, node_id):
        return self.request("/v1/nodes/" + urllib.parse.quote(node_id, safe="") + "/revoke", {})["node"]

    def heartbeat(self, capabilities=None):
        return self.request("/v1/nodes/self/heartbeat", {} if capabilities is None else {"capabilities": capabilities})["node"]

    def submit(self, task_id, payload):
        return self.request("/v1/tasks", {"task_id": task_id, "idempotency_key": task_id,
                                           "payload": payload})["task"]


def advertised_capabilities(config):
    """Only declarative names cross the wire, not filesystem roots or secrets."""
    runtime = config.get("runtime", {})
    host_platform = {"Windows": "windows", "Linux": "linux", "Darwin": "macos"}.get(platform.system(), "other")
    return {"platform": host_platform,
            "tools": list(runtime.get("allowed_tools", [])),
            "skills": list(config.get("skills", [])),
            "executors": list(runtime.get("adapters", {})),
            "models": [card["model_id"] for card in runtime.get("models", [])],
            "workspace_aliases": list(config.get("workspaces", {})),
            "max_actions": runtime.get("max_actions", 20)}


def resolve_node_payload(config, payload):
    """Map a cloud alias to a local root while retaining target/tool admission."""
    if payload.get("client_id") != config["owner_id"] or payload.get("target") != config["node_id"]:
        raise RuntimeError("Task owner or node does not match this paired machine")
    if "workdir" in payload:
        raise RuntimeError("Paired nodes accept workspace aliases, not remote absolute paths")
    alias = payload.get("workspace_alias")
    configured = config.get("workspaces", {})
    if not isinstance(alias, str) or alias not in configured:
        raise RuntimeError("Task workspace alias is not configured on this machine")
    relative = payload.get("relative_dir", ".")
    if (not isinstance(relative, str) or "\\" in relative or ":" in relative
            or Path(relative).is_absolute() or ".." in relative.split("/")):
        raise RuntimeError("Task directory must be relative to its workspace alias")
    root = Path(configured[alias]).expanduser().resolve(strict=True)
    workdir = (root / relative).resolve(strict=True)
    if not root.is_dir() or not workdir.is_dir() or not workdir.is_relative_to(root):
        raise RuntimeError("Task directory escapes its configured workspace")
    mapped = deepcopy(payload)
    mapped.pop("workspace_alias", None)
    mapped.pop("relative_dir", None)
    mapped["workdir"] = str(workdir)
    runtime = deepcopy(config.get("runtime", {}))
    runtime.update(workspace_root=str(root), client_id=config["owner_id"], target=config["node_id"])
    if config.get("receipt_dir"):
        runtime["receipt_dir"] = config["receipt_dir"]
    return runtime, mapped


def fleet_node_once(config, token):
    """Heartbeat, claim one task, execute locally, and acknowledge without replay.

    Callers own the worker lifecycle. A cloud outage never switches a native
    local chat or moves an uncertain task to another machine.
    """
    server = config["server"]
    client = FleetClient(server["url"], token, timeout=server.get("timeout", 10),
                         allow_http=server.get("allow_http", False))
    identity = client.heartbeat(advertised_capabilities(config))
    if identity["owner_id"] != config["owner_id"] or identity["node_id"] != config["node_id"]:
        raise RuntimeError("Server node identity does not match the local pairing")
    home = Path(config["receipt_dir"]).expanduser().resolve()
    home.mkdir(parents=True, exist_ok=True)
    binding = hashlib.sha256((client.url + "\0" + config["owner_id"] + "\0" + config["node_id"]).encode()).hexdigest()
    journal = home / ("fleet-claim-" + binding + ".json")
    claim = read_json(journal) if journal.exists() else {
        "request_id": uuid.uuid4().hex, "target": config["node_id"],
        "lease_seconds": config.get("lease_seconds", 60)}
    if not journal.exists():
        write_json(journal, claim)
    record = client.request("/v1/claim", claim)["task"]
    if record is None:
        journal.unlink(missing_ok=True)
        return {"status": "idle"}
    if record["status"] != "claimed":
        journal.unlink(missing_ok=True)
        return {"task_id": record["task_id"], "status": record["status"], "reconciled": True,
                "result": record.get("result")}
    stop, lease_lost = threading.Event(), threading.Event()

    def renew():
        while not stop.wait(max(float(claim["lease_seconds"]) / 3, 0.1)):
            try:
                client.task(record["task_id"], "heartbeat", {
                    "lease_token": record["lease_token"], "lease_seconds": claim["lease_seconds"]})
                client.heartbeat()
            except Exception:
                lease_lost.set()
                return

    worker = threading.Thread(target=renew, daemon=True)
    worker.start()
    try:
        try:
            runtime_config, mapped = resolve_node_payload(config, record["payload"])
        except (OSError, ValueError, KeyError):
            # No tool was dispatched, so a local admission rejection is definite.
            result = {"task_id": record["task_id"], "status": "failed", "verified": False,
                      "error": "Task violates this node's local workspace policy", "evidence": []}
        else:
            runtime_config["receipt_dir"] = str(home)
            result = Runtime(runtime_config, ledger=client).run(record["task_id"], mapped, execute=True)
        if lease_lost.is_set():
            raise RuntimeError("Node lease lost; keep the execution receipt for reconciliation")
        completed = client.task(record["task_id"], "complete", {
            "lease_token": record["lease_token"], "status": result["status"], "result": result})
        journal.unlink(missing_ok=True)
        return completed
    finally:
        stop.set()
        worker.join(timeout=1)
