"""Opt-in cloud/local runtime. The original src.cli remains an offline planner.

Configuration is a trusted local file. Remote task payloads never supply commands,
verifiers, connection URLs, credentials, or permission validators.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict
from decimal import Decimal, ROUND_CEILING
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .client_router import ClientRouter, OllamaLocalJudge, RoutingPolicy
from .discovery import (DiscoveryCache, ModelCard, parse_free_models_monitor,
                        parse_ollama, parse_token_scout, read_json_file)
from .executors import (DeepSeekHarnessAdapter, ExecutionRequest, HermesAdapter,
                        MCPAdapter, OllamaAdapter)
from .quota_ledger import QuotaLedger
from .task_card import TaskCard


class RuntimeError(ValueError):
    """A runtime operation could not safely proceed."""


def read_json(path):
    return read_json_file(path, max_bytes=2 * 1024 * 1024)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def inside(path, root):
    path = Path(path).resolve()
    root = Path(root).resolve()
    if not path.is_relative_to(root):
        raise RuntimeError("workdir/path is outside the configured workspace")
    return path


def _loopback(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(parsed.hostname or "").is_loopback
    except ValueError:
        return False


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError("redirects are forbidden for authenticated runtime requests")


class CloudClient:
    """Authenticated protocol client; effectful requests have stable replay IDs."""

    def __init__(self, url, token, *, timeout=10, allow_http=False):
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise RuntimeError("server URL must be an http(s) origin without credentials")
        if parsed.scheme == "http" and not _loopback(url) and not allow_http:
            raise RuntimeError("non-loopback HTTP requires explicit allow_http for a private transport")
        if not isinstance(token, str) or not token:
            raise RuntimeError("server token environment variable is empty")
        self.url, self.token, self.timeout = url.rstrip("/"), token, timeout
        handlers = [_NoRedirect()]
        if _loopback(url):
            handlers.append(urllib.request.ProxyHandler({}))
        self.opener = urllib.request.build_opener(*handlers)

    def request(self, path, body=None):
        payload = None if body is None else json.dumps(body, allow_nan=False).encode()
        req = urllib.request.Request(self.url + path, data=payload, headers={
            "Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
        try:
            with self.opener.open(req, timeout=self.timeout) as response:
                data = response.read(2 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as exc:
            # Never include request headers, token, or arbitrary server error text.
            raise RuntimeError(f"cloud request failed (HTTP {exc.code})") from None
        except (OSError, urllib.error.URLError):
            raise RuntimeError("cloud response unavailable; retain request ID and reconcile") from None
        if len(data) > 2 * 1024 * 1024:
            raise RuntimeError("cloud response exceeds size limit")
        return json.loads(data)

    def snapshot(self, account, model="*"):
        query = urllib.parse.urlencode({"account": account, "model": model})
        return self.request("/v1/quota?" + query)["quota"]

    def reserve(self, reservation_id, account, amount, *, model="*", unit=None, minimum_remaining=0):
        body = {"reservation_id": reservation_id, "account": account, "amount": amount,
                "model": model, "minimum_remaining": minimum_remaining}
        if unit is not None:
            body["unit"] = unit
        return self.request("/v1/quota/reserve", body)["quota"]

    def settle(self, reservation_id, actual_amount):
        return self.request("/v1/quota/settle", {"reservation_id": reservation_id,
                                              "actual_amount": actual_amount})["quota"]

    def mark_unknown(self, reservation_id):
        return self.request("/v1/quota/unknown", {"reservation_id": reservation_id})["quota"]

    def task(self, task_id, action=None, body=None):
        path = "/v1/tasks/" + urllib.parse.quote(task_id, safe="")
        return self.request(path + ("/" + action if action else ""), body)["task"]


class JsonRpcToolClient:
    """Restricted tools/call bridge, not a full MCP session/transport client."""

    def __init__(self, url):
        if not _loopback(url) or urllib.parse.urlsplit(url).scheme != "http":
            raise RuntimeError("local tool bridge must use an HTTP loopback URL")
        self.url = url
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def call_tool(self, name, arguments, *, timeout):
        request_id = uuid.uuid4().hex
        data = json.dumps({"jsonrpc": "2.0", "id": request_id, "method": "tools/call",
                           "params": {"name": name, "arguments": arguments}}).encode()
        req = urllib.request.Request(self.url, data, {"Content-Type": "application/json"})
        with self.opener.open(req, timeout=timeout) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise RuntimeError("tool response exceeds size limit")
        payload = json.loads(raw)
        if payload.get("id") != request_id or "error" in payload or not isinstance(payload.get("result"), dict):
            raise RuntimeError("invalid tool response")
        return payload["result"]


def _tool_validator(spec):
    """Allow explicit keys and workspace paths; tool server must enforce its own ACL."""
    required = set(spec.get("required_arguments", []))
    allowed = set(spec.get("allowed_arguments", []))
    path_keys = set(spec.get("path_arguments", []))
    if not required <= allowed or not path_keys <= allowed:
        raise RuntimeError("tool validator keys must be explicitly allowed")

    def validate(arguments, workdir):
        if not isinstance(arguments, dict) or not required <= arguments.keys() or not arguments.keys() <= allowed:
            return False
        for key in path_keys & arguments.keys():
            if not isinstance(arguments[key], str):
                return False
            try:
                inside(Path(workdir) / arguments[key], workdir)
            except (ValueError, OSError):
                return False
        constants = spec.get("constant_arguments", {})
        return all(arguments.get(key) == value for key, value in constants.items())
    return validate


def build_adapters(config):
    root = config["workspace_root"]
    adapters = {}
    allow_paid = bool(config.get("routing", {}).get("allow_paid_api", False))
    for key, original in config.get("adapters", {}).items():
        opts = dict(original)
        kind = opts.pop("kind", key)
        if kind == "hermes":
            opts["allow_paid_api"] = allow_paid
            adapters[key] = HermesAdapter(root, **opts)
        elif kind == "deepseek":
            opts["allow_paid_api"] = allow_paid
            adapters[key] = DeepSeekHarnessAdapter(root, **opts)
        elif kind == "ollama":
            adapters[key] = OllamaAdapter(root, **opts)
        elif kind == "mcp":
            specs = opts.pop("tools", {})
            client = JsonRpcToolClient(opts.pop("url"))
            adapters[key] = MCPAdapter(root, client=client,
                                      tool_validators={name: _tool_validator(spec) for name, spec in specs.items()},
                                      **opts)
        else:
            raise RuntimeError("unsupported locally configured adapter kind")
    return adapters


def build_router(config):
    extension = config.get("extensions", {}).get("judge", {})
    judge = OllamaLocalJudge(**extension.get("config", {})) if extension.get("enabled") is True else None
    return ClientRouter(policy=RoutingPolicy(**config.get("routing", {})), judge=judge)


class Runtime:
    """Client-side selection, reservation, bounded execution and independent verification."""

    def __init__(self, config, *, ledger=None, adapters=None):
        self.config = config
        self.root = Path(config["workspace_root"]).resolve(strict=True)
        self.receipts = Path(config.get("receipt_dir", self.root / ".orchestrator-receipts")).resolve()
        self.ledger = ledger
        self.adapters = adapters
        self.router = build_router(config)

    def receipt_path(self, task_id):
        return self.receipts / (hashlib.sha256(task_id.encode()).hexdigest() + ".json")

    def _task(self, task_id, payload):
        if self.config.get("target") and payload.get("target") != self.config["target"]:
            raise RuntimeError("task target does not match the locally configured node")
        if self.config.get("client_id") and payload.get("client_id") != self.config["client_id"]:
            raise RuntimeError("task client identity does not match local configuration")
        if not isinstance(payload.get("task"), str) or not payload["task"].strip():
            raise RuntimeError("task must contain nonempty text")
        workdir = inside(payload["workdir"], self.root)
        if not workdir.is_dir():
            raise RuntimeError("task workdir does not exist")
        tools = payload.get("tools", [])
        if (not isinstance(tools, list) or any(not isinstance(t, str) for t in tools)
                or not set(tools) <= set(self.config.get("allowed_tools", []))):
            raise RuntimeError("requested tools exceed the local node allowlist")
        max_actions = payload.get("max_actions", 20)
        if isinstance(max_actions, bool) or not isinstance(max_actions, int) or not 1 <= max_actions <= self.config.get("max_actions", 20):
            raise RuntimeError("task action budget exceeds the local node limit")
        metadata = payload.get("metadata", {})
        raw = dict(metadata.get("task_card", {}))
        raw.update(task_id=task_id, title=payload["task"])
        return TaskCard.from_dict(raw), workdir, tools, max_actions

    def _cards_and_quota(self):
        # Operator declarations are trusted configuration, not fresh discovery
        # evidence. Quota still comes from the independently observed ledger.
        cards = []
        for original in self.config.get("models", []):
            raw = dict(original)
            raw.setdefault("observed_at", time.time())
            raw.setdefault("source", "operator-config")
            cards.append(ModelCard.from_dict(raw))
        extension = self.config.get("extensions", {}).get("discovery", {})
        if extension.get("enabled") is True:
            discovered = DiscoveryCache(extension["cache"],
                                        ttl_seconds=extension.get("ttl_seconds", 900)).load()
            configured_ids = {card.model_id for card in cards}
            for card in discovered:
                if card.model_id in configured_ids:
                    continue
                profile = extension.get("capability_profiles", {}).get(card.model_id)
                if profile:
                    card.capabilities = dict(profile)
                    card.metadata["capability_evidence"] = "operator-profile"
                card.executor = extension.get("executor_overrides", {}).get(card.model_id, card.executor)
                cards.append(card)
        states, costs = {}, {}
        accounts = self.config.get("model_accounts", {})
        for card in cards:
            allocation = accounts.get(card.model_id)
            if allocation:
                card.metadata["account"] = allocation["account"]
                costs[card.model_id] = allocation["reservation_amount"]
                if self.ledger is not None:
                    try:
                        states[card.model_id] = self.ledger.snapshot(allocation["account"], card.metadata.get("quota_pool_model", card.model_id))
                    except (ValueError, RuntimeError):
                        # A missing/unavailable balance must not become an assumed balance.
                        pass
        return cards, states, costs

    def route(self, task_id, payload, *, cards=None):
        task, _, tools, _ = self._task(task_id, payload)
        discovered, states, costs = self._cards_and_quota()
        return self.router.route(task, discovered if cards is None else cards,
                                 available_tools=self.config.get("allowed_tools", []),
                                 required_tools=tools, quota_states=states, quota_costs=costs)

    def _verify(self, workdir, output):
        spec = self.config.get("verification")
        if not spec or not isinstance(spec.get("command"), list) or not spec["command"]:
            return False, ["No independent local verifier configured"], {}
        command = spec["command"]
        if any(not isinstance(item, str) or not item for item in command):
            raise RuntimeError("verifier command must be a nonempty argv list")
        try:
            from .executors import _run_process
            completed = _run_process(command, workdir, output,
                                     min(float(spec.get("timeout", 60)), 600), max_output_bytes=1024 * 1024)
            if completed.uncertain:
                return False, ["local verifier interrupted or exceeded output budget"], {"error": completed.error}
            evidence = {"exit_code": completed.returncode,
                        "stdout": completed.stdout[-16000:], "stderr": completed.stderr[-16000:]}
            return completed.returncode == 0, [f"local verifier exited {completed.returncode}"], evidence
        except subprocess.TimeoutExpired:
            return False, ["local verifier timed out"], {"timeout": True}

    def run(self, task_id, payload, *, execute=False):
        task, workdir, tools, max_actions = self._task(task_id, payload)
        if not execute:
            return {"task_id": task_id, "status": "planned", "executed": False,
                    "route": self.route(task_id, payload).to_dict()}
        path = self.receipt_path(task_id)
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        if path.exists():
            previous = read_json(path)
            if previous.get("payload_sha256") != fingerprint:
                raise RuntimeError("task ID already used for a different payload")
            if previous.get("status") == "pending":
                previous.update(status="unknown", verified=False,
                                error="Previous execution has no final receipt; manual reconciliation required")
            return previous
        receipt = {"task_id": task_id, "payload_sha256": fingerprint, "status": "pending",
                   "verified": False, "evidence": [], "attempts": [], "started_at": time.time()}
        self.receipts.mkdir(parents=True, exist_ok=True)
        # Exclusive journal creation precedes every effect, including quota reserve.
        try:
            with path.open("x", encoding="utf-8") as stream:
                json.dump(receipt, stream)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            raise RuntimeError("task is already being handled; do not launch again") from None
        try:
            self._execute(task, payload, workdir, tools, max_actions, receipt, path)
        except Exception as exc:
            # Any unexpected failure after a reserve/dispatch is uncertain. This
            # covers lost quota settlement responses as well as adapter bugs.
            receipt.update(status="unknown", verified=False,
                           error=f"{type(exc).__name__}: runtime interrupted; reconcile before retry")
            for attempt in receipt["attempts"]:
                reservation = attempt.get("reservation_id")
                if reservation and not attempt.get("settled"):
                    try:
                        self.ledger.mark_unknown(reservation)
                    except Exception:
                        pass  # Existing RESERVED still holds quota if server is offline.
        receipt["finished_at"] = time.time()
        write_json(path, receipt)
        return receipt

    def _execute(self, task, payload, workdir, tools, max_actions, receipt, path):
        cards, _, _ = self._cards_and_quota()
        adapters = self.adapters if self.adapters is not None else build_adapters(self.config)
        attempts_limit = min(max(int(self.config.get("max_attempts", 2)), 1), 3)
        for index in range(attempts_limit):
            route = self.route(task.task_id, payload, cards=cards)
            receipt["route"] = route.to_dict()
            selected = route.selected
            if selected is None:
                receipt.update(status="failed", error="No eligible executor/model")
                return
            if selected.executor not in adapters:
                receipt.update(status="failed", error="Selected executor is not locally configured")
                return
            attempt = {"model_id": selected.model_id, "executor": selected.executor}
            receipt["attempts"].append(attempt)
            allocation = self.config.get("model_accounts", {}).get(selected.model_id)
            if selected.billing_mode != "local":
                if self.ledger is None or allocation is None:
                    receipt.update(status="failed", error="Nonlocal execution requires an explicit quota allocation")
                    return
                pool = selected.metadata.get("quota_pool_model", selected.model_id)
                snapshot = self.ledger.snapshot(allocation["account"], pool)
                if snapshot["unit"] == "requests":
                    expected_requests = None
                    if isinstance(adapters[selected.executor], OllamaAdapter):
                        expected_requests = 1
                    elif isinstance(adapters[selected.executor], MCPAdapter):
                        expected_requests = len(payload.get("metadata", {}).get("tool_calls", []))
                    if expected_requests is not None and allocation["reservation_amount"] < expected_requests:
                        receipt.update(status="failed", error="Request reservation is smaller than the explicit execution plan")
                        return
                reservation_id = f"{task.task_id}:{index}"
                attempt["reservation_id"] = reservation_id
                write_json(path, receipt)
                capacity = 1 if snapshot["unit"] == "fraction" else snapshot.get("capacity")
                floor_fraction = route.reserve_fractions.get(selected.model_id, 0)
                minimum = float((Decimal(str(floor_fraction)) * Decimal(str(capacity or 0)))
                                .quantize(Decimal("0.000000001"), rounding=ROUND_CEILING))
                reservation = self.ledger.reserve(reservation_id, allocation["account"],
                                                  allocation["reservation_amount"], model=pool,
                                                  minimum_remaining=minimum)
                attempt["reservation"] = reservation
                if reservation.get("replayed") or reservation.get("state") != "RESERVED":
                    receipt.update(status="unknown", error="Existing quota reservation; execution was not repeated")
                    return
            request = ExecutionRequest(task_id=task.task_id, prompt=payload["task"],
                                       model=selected.model_id, workdir=str(workdir),
                                       timeout=min(float(self.config.get("execution_timeout", 120)), 600),
                                       tools=tools, max_actions=max_actions,
                                       metadata=payload.get("metadata", {}))
            attempt["dispatch_started_at"] = time.time()
            write_json(path, receipt)
            result = adapters[selected.executor].execute(request)
            attempt["execution"] = asdict(result)
            if result.status not in {"succeeded", "failed", "unknown"}:
                raise RuntimeError("adapter returned an invalid terminal state")
            if result.status == "unknown":
                if attempt.get("reservation_id"):
                    self.ledger.mark_unknown(attempt["reservation_id"])
                receipt.update(status="unknown", error=result.error or "Executor outcome is unknown")
                return
            if allocation and selected.billing_mode != "local":
                unit = attempt["reservation"]["unit"]
                usage_key = allocation.get("usage_key")
                charged = None
                if usage_key and allocation.get("usage_unit") == unit:
                    charged = result.usage.get(usage_key)
                    attempt["accounting"] = "measured " + usage_key + " in " + unit
                elif unit == "requests" and isinstance(adapters[selected.executor], OllamaAdapter):
                    # Native adapter sends exactly one inference request.
                    charged = 1
                    attempt["accounting"] = "one native Ollama request"
                elif unit == "requests" and isinstance(adapters[selected.executor], MCPAdapter):
                    # Explicit plans expose each dispatched tool call. This unit
                    # counts tool requests, not hidden provider inference calls.
                    charged = sum(event.get("type") == "tool_call" for event in result.events)
                    attempt["accounting"] = "observed MCP tool requests"
                if charged is None or isinstance(charged, bool):
                    self.ledger.mark_unknown(attempt["reservation_id"])
                    receipt.update(status="unknown", output=result.output,
                                   error="No trusted usage measurement in the ledger unit; pool frozen pending reconciliation")
                    return
                settlement = self.ledger.settle(attempt["reservation_id"], charged)
                if settlement.get("state") != "SETTLED":
                    receipt.update(status="unknown", output=result.output,
                                   error="Usage exceeded the reservation; pool frozen pending reconciliation")
                    return
                attempt["settled"] = True
            if result.status == "succeeded":
                ok, evidence, verification = self._verify(workdir, result.output)
                receipt.update(status="done" if ok else "failed", verified=ok, evidence=evidence,
                               verification=verification, output=result.output)
                return
            # Only a definite, explicitly retryable failure may use another model.
            # No fallback after timeout/unknown or after a successful side effect
            # whose independent verification failed.
            if not (result.retryable is True and self.config.get("allow_fallback") is True
                    and payload.get("metadata", {}).get("safe_to_retry") is True):
                receipt.update(status="failed", error=result.error)
                return
            cards = [card for card in cards if card.model_id != selected.model_id]
        receipt.update(status="failed", error="Definite failure exhausted bounded fallback attempts")


def cloud_from_config(config):
    server = config["server"]
    return CloudClient(server["url"], os.environ.get(server["token_env"], ""),
                       timeout=server.get("timeout", 10), allow_http=server.get("allow_http", False))


@contextmanager
def _node_lock(path):
    """Kernel-owned lock survives reconnect but is released on process death."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0)
        if not stream.read(1):
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError("Another node process is using this receipt directory") from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def node_once(config, *, execute=False):
    if not execute:
        raise RuntimeError("node requires --execute because claiming starts a lease")
    root = Path(config["workspace_root"]).resolve(strict=True)
    receipt_dir = Path(config.get("receipt_dir", root / ".orchestrator-receipts"))
    with _node_lock(receipt_dir / "node.lock"):
        return _node_once(config, execute=execute)


def _node_once(config, *, execute=False):
    if not execute:
        raise RuntimeError("node requires --execute because claiming starts a lease")
    client = cloud_from_config(config)
    runtime = Runtime(config, ledger=client)
    journal = runtime.receipts / "claim-request.json"
    if journal.exists():
        claim = read_json(journal)
    else:
        claim = {"request_id": uuid.uuid4().hex, "target": config["target"],
                 "lease_seconds": config.get("lease_seconds", 60)}
        write_json(journal, claim)
    # A lost response leaves this journal intact. The next call reuses the exact
    # request ID and receives the same task instead of claiming a second task.
    record = client.request("/v1/claim", claim)["task"]
    if record is None:
        journal.unlink(missing_ok=True)
        return {"status": "idle"}
    task_id = record["task_id"]
    if record["status"] != "claimed":
        journal.unlink(missing_ok=True)
        return {"task_id": task_id, "status": record["status"], "reconciled": True,
                "result": record.get("result")}
    stop = threading.Event()
    lease_lost = threading.Event()

    def heartbeat():
        while not stop.wait(max(float(claim["lease_seconds"]) / 3, 0.1)):
            try:
                client.task(task_id, "heartbeat", {"lease_token": record["lease_token"],
                                                   "lease_seconds": claim["lease_seconds"]})
            except Exception:
                lease_lost.set()
                return

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        if record["payload"]["target"] != config["target"]:
            raise RuntimeError("claimed task target does not match this node")
        receipt = runtime.run(task_id, record["payload"], execute=True)
        if lease_lost.is_set():
            raise RuntimeError("lease heartbeat was lost; execution receipt retained for reconciliation")
        completed = client.task(task_id, "complete", {"lease_token": record["lease_token"],
                                                     "status": receipt["status"], "result": receipt})
        journal.unlink(missing_ok=True)
        return completed
    finally:
        stop.set()
        thread.join(timeout=1)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Create one local client configuration with extensions disabled")
    init.add_argument("--config", required=True)
    init.add_argument("--workspace", required=True)
    discovery = commands.add_parser("discovery", help="Import model discovery evidence into a TTL cache")
    discovery.add_argument("--source", choices=["token-scout", "free-models-monitor", "ollama", "show"], required=True)
    discovery.add_argument("--input")
    discovery.add_argument("--report")
    discovery.add_argument("--observed-at", type=float, help="UTC epoch observation time; default input mtime")
    discovery.add_argument("--cache", required=True)
    for name in ("route", "run"):
        command = commands.add_parser(name)
        command.add_argument("--config", required=True)
        command.add_argument("--task", required=True, help="JSON {task_id,payload}")
        if name == "run":
            command.add_argument("--execute", action="store_true")
    node = commands.add_parser("node")
    node.add_argument("--config", required=True)
    node.add_argument("--execute", action="store_true")
    node.add_argument("--once", action="store_true")
    node.add_argument("--poll-seconds", type=float, default=3)
    enqueue = commands.add_parser("submit", aliases=["enqueue"])
    enqueue.add_argument("--config", required=True)
    enqueue.add_argument("--task", required=True)
    status = commands.add_parser("status")
    status.add_argument("--config", required=True)
    status.add_argument("--task-id", required=True)
    server = commands.add_parser("server")
    server.add_argument("--db", required=True)
    server.add_argument("--quota-db", required=True)
    server.add_argument("--tokens-env", default="ORCHESTRATOR_TOKENS")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8787)
    quota = commands.add_parser("quota-set", help="Record an explicitly observed balance on the ledger-owning host")
    quota.add_argument("--db", required=True)
    quota.add_argument("--input", required=True, help="JSON parameters for QuotaLedger.set_quota")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            if Path(args.config).exists():
                raise RuntimeError("configuration already exists")
            root = Path(args.workspace).resolve(strict=True)
            result = {"workspace_root": str(root), "models": [], "adapters": {},
                      "allowed_tools": [], "max_actions": 20, "target": "desktop", "client_id": "desktop",
                      "extensions": {"discovery": {"enabled": False}, "judge": {"enabled": False}},
                      "routing": {"allow_paid_api": False}}
            write_json(args.config, result)
        elif args.command == "discovery":
            cache = DiscoveryCache(args.cache)
            if args.source == "show":
                result = [card.to_dict() for card in cache.load()]
            else:
                if not args.input:
                    raise RuntimeError("discovery import requires --input")
                payload = read_json(args.input)
                observed_at = args.observed_at if args.observed_at is not None else Path(args.input).stat().st_mtime
                if args.source == "token-scout":
                    cards = parse_token_scout(payload, observed_at=observed_at)
                elif args.source == "ollama":
                    cards = parse_ollama(payload, observed_at=observed_at)
                else:
                    if not args.report:
                        raise RuntimeError("free-models-monitor requires both snapshot --input and --report")
                    observed_at = min(observed_at, Path(args.report).stat().st_mtime)
                    cards = parse_free_models_monitor(payload, read_json(args.report), observed_at=observed_at)
                events = cache.apply(args.source, cards, complete=args.source != "free-models-monitor")
                result = {"events": [asdict(event) for event in events],
                          "models": [card.to_dict() for card in cache.load()]}
        elif args.command == "server":
            from .cloud_queue import CloudQueue
            from .cloud_server import make_server
            tokens = json.loads(os.environ.get(args.tokens_env, "{}"))
            server = make_server(args.host, args.port, CloudQueue(args.db), tokens,
                                 ledger=QuotaLedger(args.quota_db))
            print(json.dumps({"listening": list(server.server_address)}), flush=True)
            try:
                server.serve_forever()
            finally:
                server.server_close()
            return 0
        elif args.command == "quota-set":
            result = QuotaLedger(args.db).set_quota(**read_json(args.input))
        else:
            config = read_json(args.config)
            if args.command == "node":
                while True:
                    result = node_once(config, execute=args.execute)
                    print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)
                    if args.once:
                        return 0 if result.get("status") in {"idle", "done"} else 1
                    time.sleep(max(args.poll_seconds, 0.1))
            elif args.command == "status":
                result = cloud_from_config(config).task(args.task_id) if config.get("server") else read_json(Runtime(config).receipt_path(args.task_id))
            elif args.command in {"submit", "enqueue"}:
                result = cloud_from_config(config).request("/v1/tasks", read_json(args.task))["task"]
            else:
                item = read_json(args.task)
                ledger = QuotaLedger(config["quota_db"]) if config.get("quota_db") else None
                runtime = Runtime(config, ledger=ledger)
                if args.command == "route":
                    result = runtime.route(item["task_id"], item["payload"]).to_dict()
                else:
                    result = runtime.run(item["task_id"], item["payload"], execute=args.execute)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 1 if isinstance(result, dict) and result.get("status") in {"failed", "unknown"} else 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=__import__("sys").stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
