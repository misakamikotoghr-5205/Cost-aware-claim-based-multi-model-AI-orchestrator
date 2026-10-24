"""Intent sessions and the independently installed native plugin API."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import shutil
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.pointer_intents import (ACTIONS, IntentError, IntentSessions,
                                 LocalOllamaSuggestions, RevisionConflict,
                                 SessionNotFound, normalize_context)


def context(**changes):
    return {"app": "Microsoft Word", "window_title": "Report.docx", "kind": "auto",
            "region": {"x": 10, "y": 20, "width": 300, "height": 200}, **changes}


def ids(response):
    return [candidate["action_id"] for candidate in response["candidates"]]


@pytest.mark.parametrize("kind,keyword,family", [
    ("document", "润色", "document.polish"),
    ("document", "改写", "document.rewrite"),
    ("document", "翻译", "document.translate"),
    ("document", "摘要", "document.summary"),
    ("spreadsheet", "格式", "spreadsheet.format"),
    ("spreadsheet", "清理", "spreadsheet.clean"),
    ("spreadsheet", "排序", "spreadsheet.sort"),
    ("spreadsheet", "公式", "spreadsheet.formula"),
    ("spreadsheet", "统计", "spreadsheet.statistics"),
    ("spreadsheet", "图表", "spreadsheet.chart"),
])
def test_document_and_spreadsheet_intent_families(kind, keyword, family):
    response = IntentSessions().create(context(kind=kind), keyword)
    assert 2 <= len(response["candidates"]) <= 4
    assert all(action_id.startswith(family + ".") for action_id in ids(response))
    for candidate in response["candidates"]:
        assert candidate["label"] and candidate["description"] and candidate["preview_prompt"]
    assert response["executed"] is False
    assert response["mode"] == "draft-only"


def test_app_title_are_evidence_and_manual_override_wins():
    assert normalize_context(context(app="Word", window_title=""))["derived_kind"] == "document"
    assert normalize_context(context(app="", window_title="Sales.xlsx"))["kind_source"] == "window_title"
    normalized = normalize_context(context(app="Excel", kind="document"))
    assert normalized["derived_kind"] == "document"
    assert normalized["kind_source"] == "manual"
    assert normalized["evidence_only"] is True
    assert normalize_context(context(app="Excel", kind="unknown"))["derived_kind"] == "unknown"


def test_unknown_app_uses_keywords_and_asks_when_uncertain():
    engine = IntentSessions()
    inferred = engine.create(context(app="Unknown App", window_title="", kind="auto"), "公式")
    assert all(action_id.startswith("spreadsheet.formula.") for action_id in ids(inferred))
    unclear = engine.create(context(app="Unknown App", window_title="", kind="auto"), "帮我弄一下")
    assert unclear["question"]
    assert {action_id.split(".")[0] for action_id in ids(unclear)} == {"document", "spreadsheet"}
    empty = engine.create({}, "")
    assert empty["question"] and len(empty["candidates"]) >= 2
    mismatch = engine.create(context(kind="document"), "排序")
    assert mismatch["question"] and not mismatch["candidates"]


def test_refine_accumulates_keywords_and_excludes_rejected_and_seen():
    engine = IntentSessions()
    first = engine.create(context(), "润色")
    second = engine.refine(first["session_id"], 1, "摘要", ids(first))
    assert second["revision"] == 2
    assert second["keywords"] == "润色\n摘要"
    assert not set(ids(first)) & set(ids(second))
    assert all(action_id.startswith("document.summary.") for action_id in ids(second))
    third = engine.refine(first["session_id"], 2, "", ids(second))
    assert not third["candidates"] and third["question"]


def test_another_batch_and_manual_override_create_new_revision():
    engine = IntentSessions()
    first = engine.create(context(), "")
    second = engine.refine(first["session_id"], 1)
    assert not set(ids(first)) & set(ids(second))
    third = engine.refine(first["session_id"], 2, "图表", context=context(kind="spreadsheet"))
    assert third["revision"] == 3
    assert all(action_id.startswith("spreadsheet.chart.") for action_id in ids(third))


def test_commit_requires_current_selected_action_and_returns_only_draft(monkeypatch):
    import src.pointer_intents as module
    def forbidden(*args, **kwargs):
        raise AssertionError("default intent sessions must not call a model or tool")
    monkeypatch.setattr(module.http.client, "HTTPConnection", forbidden)
    engine = IntentSessions()
    first = engine.create(context(), "翻译")
    with pytest.raises(IntentError):
        engine.commit(first["session_id"], 1, "shell.run")
    second = engine.refine(first["session_id"], 1, "摘要")
    with pytest.raises(RevisionConflict):
        engine.commit(first["session_id"], 1, ids(first)[0])
    with pytest.raises(IntentError):
        engine.commit(first["session_id"], 2, ids(first)[0])
    result = engine.commit(first["session_id"], 2, ids(second)[0])
    assert result["draft_prompt"] == second["candidates"][0]["preview_prompt"]
    assert set(result) == {"session_id", "revision", "action_id", "draft_prompt", "executed"}
    assert result["executed"] is False
    assert "不是截图识别或OCR结果" in result["draft_prompt"]
    assert engine.commit(first["session_id"], 2, ids(second)[0]) == result
    with pytest.raises(RevisionConflict):
        engine.commit(first["session_id"], 2, ids(second)[1])


def test_revisions_prevent_concurrent_conflicting_selections():
    engine = IntentSessions()
    response = engine.create(context(), "润色")
    def commit(action_id):
        try:
            return engine.commit(response["session_id"], 1, action_id)
        except RevisionConflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as workers:
        committed = list(workers.map(commit, ids(response)[:2]))
    assert sum(item is not None for item in committed) == 1


@pytest.mark.parametrize("invalid", [
    {"kind": []}, {"kind": "shell"}, {"image": "base64"},
    {"screenshot": "private image"}, {"ocr": "invented text"},
    {"region": {"x": 0, "y": 0, "width": 0, "height": 1}},
    {"region": {"x": 0, "y": 0, "width": 1, "height": float("nan")}},
    {"region": {"x": True, "y": 0, "width": 1, "height": 1}},
    {"region": {"x": 10**1000, "y": 0, "width": 1, "height": 1}},
])
def test_rejects_images_ocr_and_malformed_context(invalid):
    with pytest.raises(IntentError):
        IntentSessions().create(invalid)


def test_session_expiry_bounds_and_response_copies():
    now = [100]
    engine = IntentSessions(max_sessions=1, ttl_seconds=10, clock=lambda: now[0])
    first = engine.create(context())
    copy = engine.get(first["session_id"])
    copy["candidates"].clear()
    assert engine.get(first["session_id"])["candidates"]
    second = engine.create(context())
    with pytest.raises(SessionNotFound):
        engine.get(first["session_id"])
    now[0] = 110
    with pytest.raises(SessionNotFound):
        engine.get(second["session_id"])


def test_reject_only_current_candidates_and_revision_bool_is_invalid():
    engine = IntentSessions()
    response = engine.create(context(), "摘要")
    with pytest.raises(IntentError):
        engine.refine(response["session_id"], 1, reject_action_ids=["spreadsheet.chart.line"])
    with pytest.raises(RevisionConflict):
        engine.refine(response["session_id"], True, "more")
    assert engine.get(response["session_id"])["revision"] == 1


@pytest.mark.parametrize("url", ["https://api.example.com", "http://192.168.1.1:11434", "http://localhost@evil.test", "http://127.0.0.1:11434/private", "http://localhost?secret=1"])
def test_local_suggestions_refuse_remote_or_credential_endpoints(url):
    with pytest.raises(IntentError):
        LocalOllamaSuggestions("qwen3:4b", url)


def test_cloud_named_models_rejected_and_timeout_falls_back():
    with pytest.raises(IntentError):
        LocalOllamaSuggestions("qwen3:480b-cloud")
    class Unavailable:
        def suggest(self, *args):
            raise TimeoutError("local service unavailable")
    response = IntentSessions(Unavailable()).create(context(), "润色")
    assert response["suggestions"] == "fallback"
    assert len(response["candidates"]) >= 2


@pytest.mark.parametrize("reply", ["not-json", '{"action_ids":["shell.run","document.polish.clear"]}', '{"action_ids":["document.polish.clear","document.polish.clear"]}', '{"action_ids":["document.polish.clear","document.polish.formal"],"command":"delete"}'])
def test_ollama_strict_allowlist_and_json_fallback(reply, monkeypatch):
    suggestion = LocalOllamaSuggestions("qwen3:4b")
    def fake_request(method, path, body, deadline):
        if path == "/api/tags":
            return {"models": [{"name": "qwen3:4b", "size": 1000}]}
        if path == "/api/show":
            return {"model_info": {"general.architecture": "qwen3"}}
        return {"response": reply}
    monkeypatch.setattr(suggestion, "_request", fake_request)
    response = IntentSessions(suggestion).create(context(), "润色")
    assert response["suggestions"] == "fallback"
    assert all(action_id.startswith("document.polish.") for action_id in ids(response))


def test_ollama_only_text_metadata_and_allowlisted_ranking(monkeypatch):
    suggestion = LocalOllamaSuggestions("qwen3:4b")
    requests = []
    def fake_request(method, path, body, deadline):
        requests.append((method, path, body))
        if path == "/api/tags":
            return {"models": [{"name": "qwen3:4b", "size": 1000}]}
        if path == "/api/show":
            return {"model_info": {"general.architecture": "qwen3"}}
        decoded = json.loads(body["prompt"])
        assert set(decoded["context"]) == {"app", "window_title", "derived_kind"}
        assert "images" not in body and "region" not in decoded["context"]
        return {"response": json.dumps({"action_ids": ["document.polish.formal", "document.polish.clear"]})}
    monkeypatch.setattr(suggestion, "_request", fake_request)
    response = IntentSessions(suggestion).create(context(), "润色")
    assert response["suggestions"] == "local"
    assert ids(response)[0] == "document.polish.formal"
    assert [path for _, path, _ in requests] == ["/api/tags", "/api/show", "/api/generate"]


def test_unconfirmed_cloud_metadata_never_reaches_generate(monkeypatch):
    suggestion = LocalOllamaSuggestions("qwen3:4b")
    calls = []
    def fake_request(method, path, body, deadline):
        calls.append(path)
        if path == "/api/tags":
            return {"models": [{"name": "qwen3:4b", "size": 1000}]}
        return {"remote_host": "https://cloud.example"}
    monkeypatch.setattr(suggestion, "_request", fake_request)
    assert IntentSessions(suggestion).create(context(), "润色")["suggestions"] == "fallback"
    assert "/api/generate" not in calls


@pytest.fixture
def native_plugin(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    source = ROOT / "integrations/hermes-client/extensions/smart-pointer"
    installed = tmp_path / "plugins/smart-pointer"
    shutil.copytree(source, installed)
    (installed / "_impl").mkdir()
    shutil.copy2(ROOT / "src/pointer_intents.py", installed / "_impl/pointer_intents.py")
    path = installed / "dashboard/plugin_api.py"
    spec = importlib.util.spec_from_file_location("smart_pointer_test_plugin", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_home", lambda: tmp_path)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(module.router, prefix="/api/plugins/smart-pointer")
    # The standalone extension has no core/judge/discovery sibling installed.
    assert not (tmp_path / "plugins/orchestrator-core").exists()
    with TestClient(app) as client:
        yield module, client, tmp_path


def test_independent_native_plugin_rest_flow_and_no_implicit_submit(native_plugin):
    module, client, home = native_plugin
    prefix = "/api/plugins/smart-pointer"
    assert module._MODULE is None
    assert client.get(prefix + "/settings").json()["local_suggestions"]["enabled"] is False
    assert module._MODULE is None
    first = client.post(prefix + "/sessions", json={"context": context(), "keywords": "润色"}).json()
    assert first["revision"] == 1
    assert client.post(prefix + "/sessions", json={"context": context(), "image": "must-not-upload"}).status_code == 400
    session_url = prefix + "/sessions/" + first["session_id"]
    assert client.post(session_url + "/commit", json={"revision": 1, "action_id": "shell.run"}).status_code == 400
    second = client.post(session_url + "/refine", json={"revision": 1, "keywords": "摘要", "reject_action_ids": ids(first)}).json()
    assert client.post(session_url + "/commit", json={"revision": 1, "action_id": ids(first)[0]}).status_code == 409
    committed = client.post(session_url + "/commit", json={"revision": 2, "action_id": ids(second)[0]}).json()
    assert committed["executed"] is False and committed["draft_prompt"]
    assert client.get(session_url).json()["revision"] == 2
    assert not (home / "smart-pointer").exists()  # no images/session text written to disk


def test_native_settings_validated_and_profiles_do_not_share_sessions(native_plugin, monkeypatch):
    module, client, home = native_plugin
    prefix = "/api/plugins/smart-pointer"
    first = client.post(prefix + "/sessions", json={"context": {}, "keywords": ""}).json()
    rejected = client.post(prefix + "/settings", json={"local_suggestions": {"enabled": True, "base_url": "https://paid.example"}})
    assert rejected.status_code == 400
    saved = client.post(prefix + "/settings", json={"local_suggestions": {"enabled": False, "model": "qwen3:4b"}})
    assert saved.status_code == 200
    assert (home / "smart-pointer/settings.json").is_file()
    # Settings changes invalidate earlier sessions rather than mixing model/context revisions.
    assert client.get(prefix + "/sessions/" + first["session_id"]).status_code == 404
    current = client.post(prefix + "/sessions", json={"context": {}, "keywords": ""}).json()
    monkeypatch.setattr(module, "_home", lambda: home / "profile-b")
    assert client.get(prefix + "/sessions/" + current["session_id"]).status_code == 404


def test_native_register_is_inert_and_declares_no_tools():
    directory = ROOT / "integrations/hermes-client/extensions/smart-pointer"
    spec = importlib.util.spec_from_file_location("smart_pointer_test_inert", directory / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    class NoCapabilities:
        def __getattr__(self, name):
            raise AssertionError("register must not register or execute capabilities")
    assert module.register(NoCapabilities()) is None
    manifest = json.loads((directory / "dashboard/manifest.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "smart-pointer" and manifest["api"] == "plugin_api.py"


def test_local_ollama_http_transport_timeout_and_no_images():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    import time
    received = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            self.respond()
        def do_POST(self):
            self.respond()
        def respond(self):
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else None
            received.append((self.path, body))
            if self.path == "/api/tags":
                result = {"models": [{"name": "qwen3:4b", "size": 1000}]}
            elif self.path == "/api/show":
                result = {"model_info": {"general.architecture": "qwen3"}}
            else:
                result = {"response": json.dumps({"action_ids": ["document.polish.formal", "document.polish.clear"]})}
            raw = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        response = IntentSessions(LocalOllamaSuggestions("qwen3:4b", f"http://127.0.0.1:{server.server_port}")).create(context(), "润色")
        assert response["suggestions"] == "local"
        assert len(received) == 3
        assert "images" not in received[-1][1]
        # A bounded deadline is checked even before opening a socket.
        with pytest.raises(TimeoutError):
            LocalOllamaSuggestions("qwen3:4b")._request("GET", "/api/tags", None, time.monotonic() - 1)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
