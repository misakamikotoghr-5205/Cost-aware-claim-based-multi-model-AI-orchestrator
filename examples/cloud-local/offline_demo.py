#!/usr/bin/env python3
"""Offline protocol fixture: real local HTTP, file effect, ledger and verifier.

This does not call Hermes, a model, or any subscription service. A tiny fixture
server implements one JSON-RPC tools/call operation. All artifacts stay inside a
new temporary workspace unless --workspace is supplied.
"""
import argparse
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.cloud_queue import CloudQueue
from src.cloud_server import make_server
from src.quota_ledger import QuotaLedger
from src.runtime_cli import CloudClient, node_once


@contextmanager
def serve(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def demonstrate(root):
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)

    class ToolFixture(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert request["method"] == "tools/call"
            assert request["params"] == {"name": "write_fixture", "arguments": {"path": "result.txt"}}
            (root / "result.txt").write_text("offline fixture completed\n", encoding="utf-8")
            data = json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": {
                "content": [{"type": "text", "text": "fixture wrote result.txt"}], "isError": False}}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    token = "offline-fixture-token-000000000000000000"
    os.environ["ORCHESTRATOR_FIXTURE_TOKEN"] = token
    ledger = QuotaLedger(root / "quota.sqlite")
    ledger.set_quota("fixture", 10, "requests", model="fixture-model", capacity=10, source="offline-fixture")
    queue = CloudQueue(root / "queue.sqlite")
    cloud = make_server("127.0.0.1", 0, queue, {token: {
        "client_id": "fixture-node", "target": "fixture-node", "accounts": ["fixture"]}}, ledger=ledger)
    with serve(ThreadingHTTPServer(("127.0.0.1", 0), ToolFixture)) as tool_url, serve(cloud) as cloud_url:
        config = {"workspace_root": str(root), "target": "fixture-node", "allowed_tools": ["write_fixture"],
                  "extensions": {"judge": {"enabled": False}, "discovery": {"enabled": False}},
                  "server": {"url": cloud_url, "token_env": "ORCHESTRATOR_FIXTURE_TOKEN"},
                  "models": [{"model_id": "fixture-model", "provider": "fixture", "executor": "mcp",
                              "billing_mode": "free", "available": True, "quota_remaining": 1,
                              "capabilities": {"implementation": 0.8}}],
                  "model_accounts": {"fixture-model": {"account": "fixture", "reservation_amount": 1}},
                  "adapters": {"mcp": {"url": tool_url, "tools": {"write_fixture": {
                      "allowed_arguments": ["path"], "required_arguments": ["path"],
                      "path_arguments": ["path"], "constant_arguments": {"path": "result.txt"}}}}},
                  "verification": {"command": [sys.executable, "-c",
                      "from pathlib import Path; assert Path('result.txt').read_text() == 'offline fixture completed\\n'; print('artifact verified')"]}}
        payload = {"client_id": "fixture-node", "target": "fixture-node", "task": "Write and verify fixture artifact",
                   "workdir": str(root), "tools": ["write_fixture"], "max_actions": 1,
                   "metadata": {"task_card": {"type": "code_generation", "requires": ["implementation"]},
                                "tool_calls": [{"name": "write_fixture", "arguments": {"path": "result.txt"}}]}}
        client = CloudClient(cloud_url, token)
        client.request("/v1/tasks", {"task_id": "offline-demo", "payload": payload})
        completed = node_once(config, execute=True)
        print(json.dumps({"fixture_only": True, "workspace": str(root), "task": completed,
                          "quota": ledger.snapshot("fixture", "fixture-model")}, indent=2))
        return 0 if completed["status"] == "done" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path)
    args = parser.parse_args()
    if args.workspace:
        raise SystemExit(demonstrate(args.workspace))
    with tempfile.TemporaryDirectory(prefix="orchestrator-fixture-") as directory:
        raise SystemExit(demonstrate(Path(directory)))
