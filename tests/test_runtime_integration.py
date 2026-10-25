"""Real loopback transport, durable ledger, subprocess verification and replay tests."""
from contextlib import contextmanager
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import sys
import threading
import time

import pytest

from src.cloud_queue import CloudQueue
from src.cloud_server import make_server
from src.quota_ledger import QuotaLedger
from src.runtime_cli import CloudClient, Runtime, RuntimeError, node_once

TOKEN = "integration-test-token-000000000000000"


@contextmanager
def serving(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture
def stack(tmp_path, monkeypatch):
    effects = []
    behavior = {"drop_tool_reply": False, "drop_claim_reply": False, "drop_complete_reply": False,
                "judge_requests": []}

    class Tools(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/api/generate":
                behavior["judge_requests"].append(body)
                data = {"response": json.dumps({"model_id": "fixture-model"}),
                        "done": True, "done_reason": "stop"}
            elif self.path == "/api/chat":
                effects.append({"ollama_request": body})
                data = {"done": True, "message": {"content": "fixture complete"}, "eval_count": 2}
            else:
                assert body["method"] == "tools/call"
                arguments = body["params"]["arguments"]
                effects.append(arguments)
                (tmp_path / arguments["path"]).write_text(arguments["text"])
                if behavior["drop_tool_reply"]:
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                data = {"jsonrpc": "2.0", "id": body["id"], "result": {
                    "content": [{"type": "text", "text": "artifact written"}], "isError": False}}
            raw = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    ledger = QuotaLedger(tmp_path / "quota.db")
    ledger.set_quota("fixture", 10, "requests", model="fixture-model", capacity=10)
    queue = CloudQueue(tmp_path / "queue.db")
    server = make_server("127.0.0.1", 0, queue, {TOKEN: {
        "client_id": "test-node", "target": "test-node", "accounts": ["fixture"]}}, ledger=ledger)
    monkeypatch.setenv("TEST_NODE_TOKEN", TOKEN)
    with serving(ThreadingHTTPServer(("127.0.0.1", 0), Tools)) as tools_url, serving(server) as cloud_url:
        config = {"workspace_root": str(tmp_path), "allowed_tools": ["write_file"], "max_actions": 2,
                  "target": "test-node", "lease_seconds": 3,
                  "server": {"url": cloud_url, "token_env": "TEST_NODE_TOKEN"},
                  "models": [{"model_id": "fixture-model", "provider": "fixture", "executor": "mcp",
                              "capabilities": {"implementation": 0.8}, "billing_mode": "free",
                              "available": True, "quota_remaining": 1}],
                  "model_accounts": {"fixture-model": {"account": "fixture", "reservation_amount": 1}},
                  "adapters": {"mcp": {"url": tools_url, "tools": {"write_file": {
                      "allowed_arguments": ["path", "text"], "required_arguments": ["path", "text"],
                      "path_arguments": ["path"], "constant_arguments": {"text": "fixture complete"}}}}},
                  "verification": {"command": [sys.executable, "-c",
                      "from pathlib import Path; assert Path('result.txt').read_text() == 'fixture complete'; print('checked artifact')"]},
                  "extensions": {"discovery": {"enabled": False, "cache": "/absent/disabled-cache"},
                                 "judge": {"enabled": False, "config": {"base_url": "https://disabled.invalid"}}}}
        payload = {"client_id": "test-node", "target": "test-node", "task": "Write the fixture artifact",
                   "workdir": str(tmp_path), "tools": ["write_file"], "max_actions": 1,
                   "metadata": {"task_card": {"type": "code_generation", "requires": ["implementation"]},
                                "tool_calls": [{"name": "write_file", "arguments": {
                                    "path": "result.txt", "text": "fixture complete"}}]}}
        yield config, payload, queue, ledger, effects, behavior, tools_url


def test_cloud_claim_route_reserve_execute_verify_and_replay(stack):
    config, payload, queue, ledger, effects, behavior, tools_url = stack
    # Explicitly enabled judge is called locally; disabled discovery is not needed.
    config["extensions"]["judge"] = {"enabled": True, "config": {"base_url": tools_url, "model": "fixture-judge"}}
    # A single admitted candidate now skips inference, so this transport test
    # supplies two equally capable live routes and verifies a real local choice.
    alternate = deepcopy(config["models"][0])
    alternate["model_id"] = "alternate-fixture-model"
    config["models"].append(alternate)
    config["model_accounts"][alternate["model_id"]] = deepcopy(config["model_accounts"]["fixture-model"])
    ledger.set_quota("fixture", 10, "requests", model=alternate["model_id"], capacity=10)
    cloud = CloudClient(config["server"]["url"], TOKEN)
    first = cloud.request("/v1/tasks", {"task_id": "E2E", "payload": payload})["task"]
    duplicate = cloud.request("/v1/tasks", {"task_id": "E2E", "payload": payload})["task"]
    assert first["task_id"] == duplicate["task_id"]
    completed = node_once(config, execute=True)
    assert completed["status"] == "done"
    receipt = completed["result"]
    assert receipt["verified"] is True
    assert receipt["verification"]["exit_code"] == 0
    assert "checked artifact" in receipt["verification"]["stdout"]
    assert receipt["attempts"][0]["execution"]["events"][-1]["type"] == "tool_result"
    assert any("Local Ollama judge" in note for note in receipt["route"]["notes"])
    assert len(behavior["judge_requests"]) == 1
    assert behavior["judge_requests"][0]["format"]["properties"]["model_id"]["enum"] == [
        "alternate-fixture-model", "fixture-model"]
    assert ledger.snapshot("fixture", "fixture-model")["remaining"] == 9
    assert len(effects) == 1
    assert node_once(config, execute=True)["status"] == "idle"
    assert Runtime(config, ledger=cloud).run("E2E", payload, execute=True) == receipt
    assert len(effects) == 1


def test_tool_reply_lost_is_unknown_keeps_reservation_and_never_reexecutes(stack):
    config, payload, queue, ledger, effects, behavior, _ = stack
    behavior["drop_tool_reply"] = True
    queue.enqueue("uncertain", payload)
    completed = node_once(config, execute=True)
    assert completed["status"] == "unknown"
    snapshot = ledger.snapshot("fixture", "fixture-model")
    assert snapshot["held"] == 1
    assert snapshot["remaining"] == 10
    assert len(effects) == 1
    assert Runtime(config, ledger=CloudClient(config["server"]["url"], TOKEN)).run("uncertain", payload, execute=True)["status"] == "unknown"
    assert len(effects) == 1


def test_cloud_completion_lost_reconciles_same_claim_without_effect_replay(stack, monkeypatch):
    config, payload, queue, ledger, effects, _, _ = stack
    queue.enqueue("lost-complete", payload)
    original = CloudClient.request
    dropped = []

    def lose_completion(self, path, body=None):
        result = original(self, path, body)
        if path.endswith("/complete") and not dropped:
            dropped.append(True)
            raise RuntimeError("simulated transport loss after server committed")
        return result

    monkeypatch.setattr(CloudClient, "request", lose_completion)
    with pytest.raises(RuntimeError):
        node_once(config, execute=True)
    assert queue.get("lost-complete", "test-node")["status"] == "done"
    assert node_once(config, execute=True)["status"] == "done"
    assert len(effects) == 1
    assert ledger.snapshot("fixture", "fixture-model")["remaining"] == 9


def test_cloud_claim_lost_reuses_request_id_instead_of_claiming_next(stack, monkeypatch):
    config, payload, queue, ledger, effects, _, _ = stack
    queue.enqueue("first", payload)
    queue.enqueue("second", payload)
    original = CloudClient.request
    dropped = []

    def lose_claim(self, path, body=None):
        result = original(self, path, body)
        if path == "/v1/claim" and not dropped:
            dropped.append(True)
            raise RuntimeError("simulated lost claim response")
        return result

    monkeypatch.setattr(CloudClient, "request", lose_claim)
    with pytest.raises(RuntimeError):
        node_once(config, execute=True)
    assert node_once(config, execute=True)["task_id"] == "first"
    assert queue.get("second", "test-node")["status"] == "queued"
    assert len(effects) == 1


def test_disabled_extensions_and_dry_run_have_no_tool_effects(stack):
    config, payload, _, _, effects, _, _ = stack
    dry = Runtime(config).run("dry", payload)
    assert dry["status"] == "planned"
    assert dry["executed"] is False
    assert dry["route"]["selected"]["model_id"] == "fixture-model"
    assert not effects
    assert not Runtime(config).receipt_path("dry").exists()


def test_local_tool_path_escape_and_no_verifier_cannot_succeed(stack):
    config, payload, queue, ledger, effects, _, _ = stack
    escaped = deepcopy(payload)
    escaped["metadata"]["tool_calls"][0]["arguments"]["path"] = "../outside.txt"
    queue.enqueue("escape", escaped)
    assert node_once(config, execute=True)["status"] == "failed"
    assert not effects
    config.pop("verification")
    queue.enqueue("unverified", payload)
    completed = node_once(config, execute=True)
    assert completed["status"] == "failed"
    assert completed["result"]["verified"] is False
    assert len(effects) == 1


def test_local_receipt_detects_different_payload(stack):
    config, payload, _, _, _, _, _ = stack
    runtime = Runtime(config, ledger=CloudClient(config["server"]["url"], TOKEN))
    runtime.run("same", payload, execute=True)
    changed = dict(payload, task="different work")
    with pytest.raises(RuntimeError, match="different payload"):
        runtime.run("same", changed, execute=True)


def test_unmetered_tokens_freeze_pool_and_prevent_second_execution(stack):
    from src.executors import ExecutionResult
    config, payload, _, ledger, _, _, _ = stack
    ledger = QuotaLedger(Path(config["workspace_root"]) / "tokens.sqlite")
    ledger.set_quota("fixture", 10, "tokens", model="fixture-model", capacity=10)
    calls = []

    class TokenExecutor:
        def execute(self, request):
            calls.append(request.task_id)
            return ExecutionResult("succeeded", output="answer", usage={"total_tokens": 9})

    # A quantity appearing in usage is not automatically the ledger's unit.
    runtime = Runtime(config, ledger=ledger, adapters={"mcp": TokenExecutor()})
    first = runtime.run("unmetered-first", payload, execute=True)
    assert first["status"] == "unknown"
    snapshot = ledger.snapshot("fixture", "fixture-model")
    assert snapshot["status"] == "blocked"
    assert snapshot["fresh"] is False
    second = runtime.run("unmetered-second", payload, execute=True)
    assert second["status"] == "failed"
    assert calls == ["unmetered-first"]


def test_measured_token_overrun_is_unknown_and_blocks_pool(stack):
    from src.executors import ExecutionResult
    config, payload, _, ledger, _, _, _ = stack
    ledger = QuotaLedger(Path(config["workspace_root"]) / "tokens.sqlite")
    ledger.set_quota("fixture", 10, "tokens", model="fixture-model", capacity=10)
    config["model_accounts"]["fixture-model"].update(usage_key="total_tokens", usage_unit="tokens")

    class TokenExecutor:
        def execute(self, request):
            return ExecutionResult("succeeded", output="answer", usage={"total_tokens": 9})

    result = Runtime(config, ledger=ledger, adapters={"mcp": TokenExecutor()}).run("overrun", payload, execute=True)
    assert result["status"] == "unknown"
    assert ledger.snapshot("fixture", "fixture-model")["status"] == "blocked"
    assert ledger.snapshot("fixture", "fixture-model")["held"] == 9


def test_direct_runtime_cannot_bypass_node_target(stack):
    config, payload, _, _, effects, _, _ = stack
    alien = dict(payload, target="other-node")
    with pytest.raises(RuntimeError, match="target"):
        Runtime(config).run("alien", alien, execute=True)
    assert not effects


def test_request_plan_must_fit_reserved_request_bound(stack):
    config, payload, queue, ledger, effects, _, _ = stack
    payload["max_actions"] = 2
    payload["metadata"]["tool_calls"] *= 2
    queue.enqueue("too-many", payload)
    assert node_once(config, execute=True)["status"] == "failed"
    assert not effects
    assert ledger.snapshot("fixture", "fixture-model")["held"] == 0


def test_runtime_passes_exact_strong_model_reserve_to_atomic_ledger(stack):
    from src.executors import ExecutionResult
    config, payload, _, ledger, _, _, _ = stack
    config["models"][0].update(billing_mode="subscription", capabilities={"implementation": 0.9})
    config["model_accounts"]["fixture-model"].update(usage_key="request_count", usage_unit="requests")

    class MeteredExecutor:
        def execute(self, request):
            return ExecutionResult("succeeded", output="answer", usage={"request_count": 1})

    Runtime(config, ledger=ledger, adapters={"mcp": MeteredExecutor()}).run("protected", payload, execute=True)
    reservation = ledger.get_reservation("protected:0")
    assert reservation["minimum_remaining"] == 3
    assert reservation["state"] == "SETTLED"


def test_desktop_service_submits_claims_executes_and_runs_locally_with_extensions_disabled(stack):
    from src.desktop_service import DesktopService, default_config, make_payload
    config, _, queue, _, effects, _, tools_url = stack
    merged = default_config(config["workspace_root"])
    merged.update(config)
    merged["client_id"] = "test-node"
    merged["models"][0].update(executor="ollama", provider="ollama", billing_mode="local")
    merged["adapters"] = {"ollama": {"base_url": tools_url}}
    merged["allowed_tools"] = []
    merged["verification"] = {"command": [sys.executable, "-c",
        "import sys; assert sys.stdin.read() == 'fixture complete'; print('output verified')"]}
    payload = make_payload(merged, "Answer fixture prompt", workdir=merged["workspace_root"], tools=[])
    service = DesktopService(merged)
    submitted = service.submit("gui-cloud", payload)
    assert submitted["task"]["payload"]["client_id"] == "test-node"
    assert service.node_once()["status"] == "done"
    assert queue.get("gui-cloud", "test-node")["result"]["verification"]["exit_code"] == 0
    local = service.run("gui-local", payload, execute=True)
    assert local["status"] == "done"
    assert local["attempts"][0]["execution"]["events"][0]["type"] == "ollama_response"
    assert len(effects) == 2
