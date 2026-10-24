"""Offline protocol and failure tests. No login or model requests are made."""
from __future__ import annotations

from dataclasses import replace
import io
import json
from pathlib import Path
import sys
from urllib.error import HTTPError

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import executors as ex


@pytest.fixture
def task(tmp_path):
    return ex.ExecutionRequest("task-1", "explain files", "model-1", str(tmp_path))


def process(monkeypatch, events, returncode=0):
    seen = []
    def run(argv, workdir, prompt, timeout):
        seen.append((argv, workdir, prompt, timeout))
        return ex._ProcessResult(returncode, "\n".join(json.dumps(e) for e in events), "")
    monkeypatch.setattr(ex, "_run_process", run)
    return seen


def hermes(root, **kwargs):
    return ex.HermesAdapter(root, provider="operator-provider", allowed_toolsets=["files"], **kwargs)


def test_mutable_defaults_are_independent(task):
    other = replace(task, tools=[])
    task.metadata["x"] = 1
    assert ex.ExecutionRequest("t", "p", "m", task.workdir).metadata == {}
    assert other.tools == []
    with pytest.raises(ValueError):
        ex.ExecutionResult("unknown", retryable=True)


def test_cli_defaults_deny_unknown_billing_and_metadata_cannot_grant(task, monkeypatch):
    seen = process(monkeypatch, [])
    task.metadata.update(billing_mode="subscription", allow_paid_api=True)
    assert hermes(task.workdir).execute(task).status == "failed"
    assert seen == []


@pytest.mark.parametrize("granted", [False, True])
def test_paid_api_requires_constructor_opt_in(task, monkeypatch, granted):
    seen = process(monkeypatch, [{"type": "result", "exit_code": 0, "text": "done"}])
    task.tools = ["files"]
    result = hermes(task.workdir, billing_mode="paid_api", allow_paid_api=granted).execute(task)
    assert result.status == ("succeeded" if granted else "failed")
    assert bool(seen) == granted


def test_hermes_argv_preserves_untrusted_prompt_on_stdin(task, monkeypatch):
    task.prompt = '$(touch /tmp/bad); `echo hello` "quotes"'
    task.tools = ["files"]
    seen = process(monkeypatch, [{"type": "system", "subtype": "init"},
                                 {"type": "result", "exit_code": 0, "text": "done", "tokens": {"input": 5}}])
    result = hermes(task.workdir, billing_mode="subscription").execute(task)
    argv, cwd, prompt, timeout = seen[0]
    assert result.status == "succeeded" and result.usage == {"input_tokens": 5}
    assert argv[:5] == ["hermes", "chat", "--query-file", "-", "--format"]
    assert task.prompt not in argv and prompt == task.prompt
    assert argv[-2:] == ["--max-turns", "20"]
    assert cwd == Path(task.workdir)


@pytest.mark.parametrize("events", [[], [{"type": "text", "text": "maybe done"}],
    [{"type": "result", "exit_code": 0, "text": ""}],
    [{"type": "result", "exit_code": 0, "text": "done"}, {"type": "text", "text": "late"}],
    [{"type": "result", "exit_code": 0, "text": "done"}] * 2])
def test_hermes_ambiguous_protocol_is_unknown(task, monkeypatch, events):
    task.tools = ["files"]
    process(monkeypatch, events)
    result = hermes(task.workdir, billing_mode="free").execute(task)
    assert result.status == "unknown" and result.retryable is False


def test_nonzero_exit_is_not_success_or_automatically_retryable(task, monkeypatch):
    task.tools = ["files"]
    process(monkeypatch, [{"type": "result", "exit_code": 0, "text": "done"}], returncode=1)
    result = hermes(task.workdir, billing_mode="local").execute(task)
    assert result.status == "failed" and not result.retryable


def test_malformed_jsonl_is_unknown(task, monkeypatch):
    task.tools = ["files"]
    monkeypatch.setattr(ex, "_run_process", lambda *a: ex._ProcessResult(0, "not JSON", ""))
    assert hermes(task.workdir, billing_mode="free").execute(task).status == "unknown"


def test_cli_action_overrun_is_unknown(task, monkeypatch):
    task.tools, task.max_actions = ["files"], 1
    process(monkeypatch, [{"type": "tool_use"}, {"type": "tool_use"},
                          {"type": "result", "exit_code": 0, "text": "done"}])
    assert hermes(task.workdir, billing_mode="free").execute(task).status == "unknown"


def test_workdir_escape_and_symlink_are_rejected(task, tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    seen = process(monkeypatch, [])
    adapter = hermes(root, billing_mode="free")
    task.tools = ["files"]
    for path in [outside, root / "link"]:
        assert adapter.execute(replace(task, workdir=str(path))).status == "failed"
    assert not seen


@pytest.mark.parametrize("tools", [[], ["terminal"], ["files,terminal"]])
def test_hermes_ungranted_toolsets_rejected_before_spawn(task, monkeypatch, tools):
    task.tools = tools
    seen = process(monkeypatch, [])
    assert hermes(task.workdir, billing_mode="free").execute(task).status == "failed"
    assert not seen


def test_harness_uses_actual_headless_protocol_and_pinned_model(task, monkeypatch):
    seen = process(monkeypatch, [{"type": "session", "sessionId": "s1"},
                                 {"type": "status", "phase": "step_end", "usage": {"inputTokens": 6}},
                                 {"type": "final", "text": "done", "reason": {"kind": "completed"}}])
    adapter = ex.DeepSeekHarnessAdapter(task.workdir, configured_model=task.model, billing_mode="free")
    assert adapter.execute(task).status == "succeeded"
    assert seen[0][0] == ["dsh", "--profile", "headless", "--json", "--approval-policy", "never", "-"]
    assert adapter.execute(replace(task, model="other")).status == "failed"
    assert len(seen) == 1


@pytest.mark.parametrize("reason", [{"kind": "error"}, {"kind": "max-tokens"}, None])
def test_harness_incomplete_final_is_not_success(task, monkeypatch, reason):
    process(monkeypatch, [{"type": "final", "text": "partial", "reason": reason}])
    result = ex.DeepSeekHarnessAdapter(task.workdir, configured_model=task.model,
                                      billing_mode="free").execute(task)
    assert result.status != "succeeded" and not result.retryable


def test_real_fake_subprocess_stdin_is_literal_and_bounded(task):
    code = "import sys,json; print(json.dumps({'type':'text','text':sys.stdin.read()}))"
    result = ex._run_process([sys.executable, "-c", code], Path(task.workdir), "$(bad);`bad`", 5)
    assert json.loads(result.stdout)["text"] == "$(bad);`bad`"
    assert result.returncode == 0 and not result.uncertain
    noisy = ex._run_process([sys.executable, "-c", "print('x'*10000)"], Path(task.workdir), "x", 5, max_output_bytes=100)
    assert noisy.uncertain and len(noisy.stdout) == 100


def test_real_fake_subprocess_timeout_is_unknown(task):
    result = ex._run_process([sys.executable, "-c", "import time; time.sleep(10)"], Path(task.workdir), "x", .05)
    assert result.uncertain and "timed out" in result.error


class Opener:
    def __init__(self, payload):
        self.payload, self.seen = payload, []
    def open(self, req, timeout):
        self.seen.append((req, timeout))
        if isinstance(self.payload, Exception):
            raise self.payload
        return io.BytesIO(self.payload if isinstance(self.payload, bytes) else json.dumps(self.payload).encode())


def test_ollama_native_request_and_usage(task):
    adapter = ex.OllamaAdapter(task.workdir)
    adapter._opener = opener = Opener({"done": True, "message": {"content": "answer"}, "eval_count": 7})
    result = adapter.execute(task)
    assert result.status == "succeeded" and result.usage["eval_count"] == 7
    req, timeout = opener.seen[0]
    assert req.full_url == "http://127.0.0.1:11434/api/chat"
    assert json.loads(req.data) == {"model": "model-1", "messages": [{"role": "user", "content": "explain files"}], "stream": False}
    assert timeout == task.timeout


@pytest.mark.parametrize("payload", [b"broken", [], {"done": False}, {"done": True, "message": {"content": ""}},
    {"done": True, "message": {"content": "thinking", "tool_calls": [{"function": {"name": "write"}}]}}, TimeoutError()])
def test_ollama_ambiguous_responses_unknown(task, payload):
    adapter = ex.OllamaAdapter(task.workdir)
    adapter._opener = Opener(payload)
    result = adapter.execute(task)
    assert result.status == "unknown" and not result.retryable


@pytest.mark.parametrize("url", ["https://example.com", "http://127.0.0.1.evil.test", "http://user:pass@127.0.0.1", "http://127.0.0.1/redirect", "file:///tmp/socket"])
def test_ollama_rejects_remote_or_credential_endpoints(task, url):
    with pytest.raises(ValueError):
        ex.OllamaAdapter(task.workdir, base_url=url)


def test_ollama_cloud_and_tools_denied_without_http(task):
    adapter = ex.OllamaAdapter(task.workdir)
    adapter._opener = opener = Opener({})
    assert adapter.execute(replace(task, model="qwen:cloud")).status == "failed"
    assert adapter.execute(replace(task, tools=["filesystem.write"])).status == "failed"
    assert not opener.seen


def test_ollama_http_rate_limit_can_be_retried(task):
    adapter = ex.OllamaAdapter(task.workdir)
    adapter._opener = Opener(HTTPError("url", 429, "limit", {}, None))
    result = adapter.execute(task)
    assert result.status == "failed" and result.retryable


class Client:
    def __init__(self, result=None):
        self.result = result if result is not None else {"content": [{"type": "text", "text": "found"}]}
        self.calls = []
    def call_tool(self, name, arguments, *, timeout):
        self.calls.append((name, arguments, timeout))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def mcp(task, client):
    def validate(args, root):
        return set(args) == {"path"} and (root / args["path"]).resolve().is_relative_to(root)
    return ex.MCPAdapter(task.workdir, client=client, tool_validators={"read": validate})


def plan(task, calls=None):
    return replace(task, tools=["read"], metadata={"tool_calls": calls or [{"name": "read", "arguments": {"path": "file.txt"}}]})


def test_mcp_native_tool_result_and_shared_deadline(task):
    client = Client()
    result = mcp(task, client).execute(plan(task))
    assert result.status == "succeeded" and result.output == "found"
    assert len(client.calls) == 1 and 0 < client.calls[0][2] <= task.timeout
    assert [e["type"] for e in result.events] == ["tool_call", "tool_result"]


@pytest.mark.parametrize("bad_call", [
    {"name": "read", "arguments": {"path": "../../escape"}},
    {"name": "shell", "arguments": {"command": "rm -rf /"}},
    {"name": "read", "arguments": {}, "extra": True},
])
def test_mcp_entire_plan_prevalidated_before_any_side_effect(task, bad_call):
    client = Client()
    req = plan(task, [{"name": "read", "arguments": {"path": "safe"}}, bad_call])
    assert mcp(task, client).execute(req).status == "failed"
    assert client.calls == []


def test_mcp_action_budget_checked_before_execution(task):
    client = Client()
    req = replace(plan(task), max_actions=1)
    req.metadata["tool_calls"] *= 2
    assert mcp(task, client).execute(req).status == "failed"
    assert not client.calls


@pytest.mark.parametrize("response", [TimeoutError(), {"content": [], "isError": "false"}, None])
def test_mcp_timeout_and_malformed_result_never_retried(task, response):
    client = Client(response)
    if response is None:
        client.result = None
    result = mcp(task, client).execute(plan(task))
    assert result.status == "unknown" and result.retryable is False


def test_mcp_tool_error_is_not_automatically_retried(task):
    client = Client({"content": [], "isError": True})
    result = mcp(task, client).execute(plan(task))
    assert result.status == "failed" and result.retryable is False


def test_cli_timeout_result_cannot_be_reclassified_as_success(task, monkeypatch):
    task.tools = ["files"]
    monkeypatch.setattr(ex, "_run_process", lambda *a: ex._ProcessResult(
        0, '{"type":"result","exit_code":0,"text":"late"}', "", True, "execution timed out"))
    result = hermes(task.workdir, billing_mode="free").execute(task)
    assert result.status == "unknown" and not result.retryable


def test_real_fake_hermes_executable_uses_protocol(task):
    code = "import sys,json; print(json.dumps({'type':'result','exit_code':0,'text':sys.stdin.read()}))"
    adapter = hermes(task.workdir, billing_mode="local", command=(sys.executable, "-c", code))
    result = adapter.execute(replace(task, tools=["files"], prompt="literal $(text)"))
    assert result.status == "succeeded" and result.output == "literal $(text)"


def test_ollama_real_local_fake_http_and_redirect_denial(task):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from threading import Thread
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(data)
            if data["model"] == "redirect":
                self.send_response(307)
                self.send_header("Location", "https://example.invalid/model")
                self.end_headers()
                return
            payload = json.dumps({"done": True, "message": {"content": "local fake"}}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            adapter = ex.OllamaAdapter(task.workdir, base_url=f"http://127.0.0.1:{server.server_port}")
            assert adapter.execute(task).output == "local fake"
            redirected = adapter.execute(replace(task, model="redirect"))
            assert redirected.status == "failed" and "307" in redirected.error
            assert len(seen) == 2
        finally:
            server.shutdown()
            thread.join(timeout=2)
