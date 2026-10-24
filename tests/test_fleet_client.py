"""Node-local workspace and secret boundaries for outbound fleet workers."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from src.fleet_client import FleetClient, advertised_capabilities, resolve_node_payload


def node_config(tmp_path):
    return {"owner_id": "owner", "node_id": "node-a", "workspaces": {"documents": str(tmp_path)},
            "receipt_dir": str(tmp_path / "receipts"),
            "runtime": {"adapters": {"hermes": {"command": ["/private/bin/hermes"]}},
                        "models": [{"model_id": "configured-model"}], "allowed_tools": ["files"],
                        "max_actions": 5}}


def task():
    return {"client_id": "owner", "target": "node-a", "task": "Review the document",
            "workspace_alias": "documents", "relative_dir": ".", "tools": ["files"], "max_actions": 2}


def test_node_advertises_names_without_local_paths_or_model_credentials(tmp_path):
    config = node_config(tmp_path)
    config["runtime"]["provider_api_key"] = "private-provider-fixture"
    capabilities = advertised_capabilities(config)
    assert capabilities["workspace_aliases"] == ["documents"]
    encoded = json.dumps(capabilities)
    assert str(tmp_path) not in encoded
    assert "private-provider-fixture" not in encoded
    assert "/private/bin" not in encoded


def test_alias_resolution_keeps_cloud_and_local_payloads_separate(tmp_path):
    config, original = node_config(tmp_path), task()
    runtime, mapped = resolve_node_payload(config, original)
    assert mapped["workdir"] == str(tmp_path)
    assert "workspace_alias" not in mapped
    assert original["workspace_alias"] == "documents"
    assert "workdir" not in original
    assert runtime["workspace_root"] == str(tmp_path)
    assert runtime["client_id"] == "owner"
    assert runtime["target"] == "node-a"
    assert "server" not in runtime  # native local execution has no cloud configuration fallback


@pytest.mark.parametrize("patch", [{"target": "node-b"}, {"client_id": "someone-else"},
                                    {"workspace_alias": "private"}, {"relative_dir": "../private"},
                                    {"relative_dir": "/etc"}, {"relative_dir": "C:\\private"},
                                    {"workdir": "/tmp"}])
def test_node_rejects_wrong_identity_or_remote_path(tmp_path, patch):
    payload = task()
    payload.update(patch)
    with pytest.raises(ValueError):
        resolve_node_payload(node_config(tmp_path), payload)


def test_alias_symlink_escape_is_rejected(tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    try:
        (root / "escape").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("host does not permit symlink creation")
    config = node_config(root)
    payload = task()
    payload["relative_dir"] = "escape"
    with pytest.raises(ValueError, match="escapes"):
        resolve_node_payload(config, payload)


def test_fleet_requests_preserve_explicit_target_and_stable_id(monkeypatch):
    seen = []
    def request(self, path, body=None):
        seen.append((path, deepcopy(body)))
        return {"task": body, "node": {"node_id": "node-a"}, "pairing": {"code": "one-use"}, "nodes": []}
    monkeypatch.setattr(FleetClient, "request", request)
    client = FleetClient("http://127.0.0.1:1234", "transport-fixture-token")
    payload = task()
    client.submit("stable-task", payload)
    assert seen[-1][1]["idempotency_key"] == "stable-task"
    assert seen[-1][1]["payload"]["target"] == "node-a"
    client.revoke_node("node/a")
    assert seen[-1][0] == "/v1/nodes/node%2Fa/revoke"


def test_same_node_cannot_process_two_claims_concurrently(tmp_path, monkeypatch):
    from threading import Event, Thread
    import src.fleet_client as module
    started, finish = Event(), Event()
    calls = []
    def blocked(config, token):
        calls.append(token)
        started.set()
        finish.wait(timeout=5)
        return {"status": "idle"}
    monkeypatch.setattr(module, "_fleet_node_once", blocked)
    config = node_config(tmp_path)
    worker = Thread(target=lambda: module.fleet_node_once(config, "first"))
    worker.start()
    assert started.wait(timeout=5)
    try:
        with pytest.raises(ValueError, match="Another node process"):
            module.fleet_node_once(config, "second")
    finally:
        finish.set()
        worker.join(timeout=5)
    assert calls == ["first"]


def test_real_cloud_pairs_two_nodes_executes_and_reconciles_lost_completion(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import ExitStack, contextmanager
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import sys
    from threading import Thread
    from src.cloud_queue import CloudQueue
    from src.cloud_server import make_server
    from src.fleet_client import fleet_node_once

    calls = []
    class Model(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            assert self.path == "/api/chat"
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            prompt = data["messages"][0]["content"]
            calls.append(prompt)
            result = json.dumps({"done": True, "message": {"content": "checked: " + prompt}}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(result)))
            self.end_headers()
            self.wfile.write(result)
    @contextmanager
    def serving(server):
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
    token = "owner-fleet-client-real-http-test"
    queue = CloudQueue(tmp_path / "cloud.sqlite3")
    cloud = make_server("127.0.0.1", 0, queue, {token: {"role": "owner", "owner_id": "owner", "accounts": []}})
    with ExitStack() as stack:
        url = stack.enter_context(serving(cloud))
        model_url = stack.enter_context(serving(ThreadingHTTPServer(("127.0.0.1", 0), Model)))
        owner = FleetClient(url, token)
        configs = []
        for name in ("first", "second"):
            root = tmp_path / name
            root.mkdir()
            config = {"server": {"url": url}, "workspaces": {"documents": str(root)},
                      "receipt_dir": str(root / "receipts"), "lease_seconds": 3,
                      "runtime": {"allowed_tools": [], "max_actions": 2,
                          "models": [{"model_id": "fixture-model", "provider": "ollama", "executor": "ollama",
                                      "billing_mode": "local", "available": True, "capabilities": {"reasoning": 0.8}}],
                          "adapters": {"ollama": {"base_url": model_url}},
                          "verification": {"command": [sys.executable, "-c",
                              "import sys; assert sys.stdin.read().startswith('checked: ')"]}}}
            code = owner.create_pairing(label=name)["code"]
            paired = FleetClient.redeem_pairing(url, code, label=name, capabilities=advertised_capabilities(config))
            config.update(owner_id=paired["node"]["owner_id"], node_id=paired["node"]["node_id"])
            configs.append((config, paired["token"]))
            payload = dict(task(), target=config["node_id"], task=name, tools=[])
            owner.submit(name, payload)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda pair: fleet_node_once(*pair), configs))
        assert [result["status"] for result in results] == ["done", "done"]
        assert sorted(calls) == ["first", "second"]
        assert len(owner.nodes()) == 2

        config, node_token = configs[0]
        owner.submit("lost-ack", dict(task(), target=config["node_id"], task="lost-ack", tools=[]))
        original = FleetClient.request
        dropped = []
        def lose_ack(self, path, body=None):
            result = original(self, path, body)
            if path.endswith("/complete") and not dropped:
                dropped.append(True)
                raise ValueError("fixture lost response after commit")
            return result
        monkeypatch.setattr(FleetClient, "request", lose_ack)
        with pytest.raises(ValueError, match="fixture"):
            fleet_node_once(config, node_token)
        reconciled = fleet_node_once(config, node_token)
        assert reconciled["status"] == "done" and reconciled["reconciled"] is True
        assert calls.count("lost-ack") == 1
