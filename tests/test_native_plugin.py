"""Native packages exercised through actual installed paths and HTTP plugin routers."""
from contextvars import ContextVar
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "integrations" / "hermes-client"


def load(path, prefix="native_test"):
    name = prefix + "_" + hashlib.sha256(str(path).encode()).hexdigest()[:12]
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


@pytest.fixture
def installed(tmp_path, monkeypatch):
    active = ContextVar("test_native_home", default=tmp_path / "home")
    base = active.get()
    (base / "plugins").mkdir(parents=True)
    core = base / "plugins" / "orchestrator-core"
    shutil.copytree(SOURCE / "plugin", core)
    shutil.copytree(ROOT / "src", core / "_orchestrator", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "configs", core / "configs")
    constants = types.ModuleType("hermes_constants")
    constants.get_hermes_home = active.get
    constants.get_default_hermes_root = lambda: base
    monkeypatch.setitem(sys.modules, "hermes_constants", constants)
    native_config = {"plugins": {"enabled": ["orchestrator-core"], "disabled": []}}
    package = types.ModuleType("hermes_cli")
    package.__path__ = []
    configuration = types.ModuleType("hermes_cli.config")
    configuration.load_config_readonly = lambda: deepcopy(native_config)
    monkeypatch.setitem(sys.modules, "hermes_cli", package)
    monkeypatch.setitem(sys.modules, "hermes_cli.config", configuration)
    backend = load(core / "backend.py", "native_orchestrator_backend")
    def add(name):
        target = base / "plugins" / name
        shutil.copytree(SOURCE / "extensions" / name, target)
        native_config["plugins"]["enabled"].append(name)
        return load(target / "backend.py", "native_extension_backend")
    yield types.SimpleNamespace(home=base, core=core, backend=backend, add=add,
                                active=active, native_config=native_config)
    backend.stop_worker()


def test_core_inactive_keeps_native_route_without_optional_imports(installed):
    before = set(sys.modules)
    result = installed.backend.route({"goal": "normal native chat"})
    assert result["route"] == {}
    assert not any("local_judge" in name or "free_models" in name for name in set(sys.modules) - before)
    inventory = installed.backend.inventory()
    assert {item["id"] for item in inventory} == {"local-judge", "free-models", "smart-pointer"}
    assert not any(item["installed"] for item in inventory)


def test_installed_native_disabled_extension_is_not_loaded(installed, monkeypatch):
    installed.add("local-judge")
    config = installed.backend.defaults()
    config["extensions"]["local-judge"] = True
    installed.backend.write_state("orchestrator-core", config)
    installed.native_config["plugins"]["disabled"] = ["local-judge"]
    monkeypatch.setattr(installed.backend, "load_file", lambda *args: pytest.fail("disabled extension imported"))
    assert installed.backend.extension("local-judge") is None
    assert installed.backend.inventory()[0]["installed"] is True
    assert installed.backend.inventory()[0]["enabled"] is False
    installed.native_config["plugins"]["disabled"] = []
    shutil.rmtree(installed.home / "plugins" / "local-judge")
    assert installed.backend.extension("local-judge") is None


def test_settings_are_profile_scoped_and_reject_provider_credentials(installed):
    config = installed.backend.defaults()
    config["server"]["url"] = "https://private.example"
    installed.backend.write_state("orchestrator-core", installed.backend.validate_settings(config))
    assert installed.backend.settings()["server"]["url"] == "https://private.example"
    other = installed.home / "profiles" / "other"
    other.mkdir(parents=True)
    token = installed.active.set(other)
    try:
        assert installed.backend.settings()["server"]["url"] == ""
        assert installed.backend.state_path().is_relative_to(other)
    finally:
        installed.active.reset(token)
    before = installed.backend.state_path().read_bytes()
    config["node"]["runtime"] = {"models": [], "api_key": "must-not-save"}
    with pytest.raises(ValueError, match="authentication"):
        installed.backend.write_state("orchestrator-core", config)
    assert installed.backend.state_path().read_bytes() == before
    assert "must-not-save" not in installed.backend.state_path().read_text()


def test_register_and_rest_share_one_backend_and_worker_teardown(installed, monkeypatch):
    package = types.ModuleType("agent"); package.__path__ = []
    delegation = types.ModuleType("agent.delegation_provider")
    class Provider:
        pass
    delegation.DelegationProvider = Provider
    delegation.DelegationRouteError = ValueError
    monkeypatch.setitem(sys.modules, "agent", package)
    monkeypatch.setitem(sys.modules, "agent.delegation_provider", delegation)
    plugin = load(installed.core / "__init__.py", "native_register")
    class Context:
        def register_delegation_provider(self, provider): self.provider = provider
        def on_unload(self, callback): self.cleanup = callback
        def register_tool(self, **kwargs): self.tool = kwargs
        def register_hook(self, name, callback): self.approval = callback
    ctx = Context()
    plugin.register(ctx)
    api = load(installed.core / "dashboard" / "plugin_api.py", "native_api")
    assert api.backend is installed.backend
    assert ctx.provider.route_task({}, {"model": "original", "provider": "native", "service_tier": "fast"}) == {"model": "original", "provider": "native"}
    config = installed.backend.defaults(); config["enabled"] = True
    installed.backend.write_state("orchestrator-core", config)
    seen = []
    monkeypatch.setattr(api.backend, "route", lambda *args: {"route": {"model": "native"}})
    assert ctx.provider.route_task({}, {}) == {"model": "native"}
    monkeypatch.setattr(api.backend, "stop_worker", lambda path=None: seen.append(path))
    ctx.cleanup()
    assert seen == [installed.home.resolve()]


def test_core_rest_uses_native_authenticated_router_and_installer(installed, monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    testclient = pytest.importorskip("fastapi.testclient")
    api = load(installed.core / "dashboard" / "plugin_api.py", "native_api")
    app = fastapi.FastAPI()
    app.include_router(api.router, prefix="/api/plugins/orchestrator-core")
    client = testclient.TestClient(app)
    assert client.get("/api/plugins/orchestrator-core/settings").json()["enabled"] is False
    config = installed.backend.defaults()
    config["server"]["token_env"] = "CUSTOM_FLEET_TOKEN"
    assert client.put("/api/plugins/orchestrator-core/settings", json=config).status_code == 200
    config["provider"] = {"token": "no"}
    assert client.put("/api/plugins/orchestrator-core/settings", json=config).status_code == 400
    called = []
    monkeypatch.setattr(installed.backend, "packages", lambda: types.SimpleNamespace(
        install_archive=lambda path, **kw: called.append((path, kw)) or {"installed": "free-models"}))
    assert client.post("/api/plugins/orchestrator-core/extensions/install", json={"archive_path": "chosen.zip"}).json() == {"installed": "free-models"}
    assert called[0][1]["hermes_home"] == installed.home.resolve()


@pytest.mark.parametrize("change", ["url", "token_env", "pool", "same-config-aging"])
def test_quota_dashboard_get_fences_changed_cloud_cache_without_requests(installed, monkeypatch, change):
    fastapi = pytest.importorskip("fastapi")
    testclient = pytest.importorskip("fastapi.testclient")
    backend = installed.backend
    config = backend.defaults()
    config["server"]["url"] = "https://cloud.example"
    config["quota_states"] = {"declared": {"account":"subscription", "model":"*"}}
    backend.write_state(backend.PLUGIN_ID, config)
    module = backend.load_file(installed.core / "quota_cloud.py", "quota_cloud")
    at = time.time()-400 if change == "same-config-aging" else time.time()
    cached = {"configured":True, "configuration_id":module.config_binding(backend), "state":"ready", "source":"fleet-ledger", "accounts":[{
        "id":"subscription", "state":"ready", "observed_at":at, "windows":[{
            "id":"*", "state":"ready", "observed_at":at, "expires_at":at+300, "reset_at":None,
            "remaining":80,"available":75,"held":5,"unit":"requests"}]}]}
    dashboard = backend._quota_dashboard()
    dashboard._write({"cloud":cached, "accounts":[], "imports":[]})
    before = dashboard.path.read_bytes()
    if change == "pool":config["quota_states"]["declared"]["model"]="different"
    elif change in {"url","token_env"}:
        config["server"][change]="https://different.example" if change == "url" else "OTHER_SELECTOR"
    backend.write_state(backend.PLUGIN_ID,config)
    monkeypatch.setattr(backend,"_fleet",lambda:pytest.fail("GET dashboard must not resolve credentials or query transport"))
    api = load(installed.core / "dashboard" / "plugin_api.py", "native_api")
    app = fastapi.FastAPI(); app.include_router(api.router,prefix="/api/plugins/orchestrator-core")
    response = testclient.TestClient(app).get("/api/plugins/orchestrator-core/quota/dashboard")
    assert response.status_code == 200
    value = response.json()["cloud"]
    assert value["state"] == "unknown"
    if change == "same-config-aging":
        assert value["accounts"][0]["windows"][0]["state"] == "stale"
        assert value["accounts"][0]["observed_at"] == at
    else:assert value["accounts"] == []
    assert dashboard.path.read_bytes() == before


@pytest.fixture
def ollama():
    state = {"choice": "second", "fail": False, "calls": [], "remote": False}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            state["calls"].append(self.path)
            body = {"models": [{"name": "judge-local", "digest": "known", "size": 123,
                                 **({"remote_host": "https://ollama.com"} if state["remote"] else {})}]}
            raw = json.dumps(body).encode()
            self.send_response(200); self.end_headers(); self.wfile.write(raw)
        def do_POST(self):
            state["calls"].append(self.path)
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/api/show":
                raw = json.dumps({"model_info": {"general.architecture": "qwen"}}).encode()
                self.send_response(200); self.end_headers(); self.wfile.write(raw)
                return
            assert body["model"] == "judge-local" and body["stream"] is False
            response = "invalid" if state["fail"] else json.dumps({"model_id": state["choice"]})
            raw = json.dumps({"response": response, "done": True, "done_reason": "stop"}).encode()
            self.send_response(200); self.end_headers(); self.wfile.write(raw)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    yield f"http://127.0.0.1:{server.server_port}", state
    server.shutdown(); server.server_close(); thread.join(timeout=2)


def local_card(model, score=0.6):
    return {"model_id": model, "provider": "ollama", "executor": "hermes", "billing_mode": "local",
            "available": True, "observed_at": time.time(), "capabilities": {"reasoning": score}}


def test_installed_judge_tests_connection_and_really_selects_candidates(installed, ollama):
    judge = installed.add("local-judge")
    url, state = ollama
    judge.save({"base_url": url, "model": "judge-local", "timeout": 2})
    assert state["calls"] == []  # configure alone does not infer
    assert judge.test_connection()["ok"] is True
    body = {"task": {"task_id": "J", "title": "simple", "type": "general"},
            "candidates": [local_card("first"), local_card("second")]}
    receipt = judge.judge_task(body)
    assert receipt["selected"]["model_id"] == "second"
    assert state["calls"] == ["/api/tags", "/api/show", "/api/tags", "/api/show", "/api/generate"]
    state["fail"] = True
    fallback = judge.judge_task(body)
    assert fallback["selected"] is not None
    assert any("deterministic selection retained" in note for note in fallback["notes"])
    with pytest.raises(ValueError):
        judge.save({"base_url": "https://provider.example", "model": "cloud-model"})
    state["remote"] = True
    before_generate = state["calls"].count("/api/generate")
    hidden_remote = judge.judge_task(body)
    assert state["calls"].count("/api/generate") == before_generate
    assert any("deterministic selection retained" in note for note in hidden_remote["notes"])


def test_native_delegation_reuses_scorer_and_installed_judge(installed, ollama):
    judge = installed.add("local-judge")
    judge.save({"base_url": ollama[0], "model": "judge-local"})
    config = installed.backend.defaults()
    config.update(enabled=True, models=[local_card("first"), local_card("second")],
                  routes={"first": {"provider": "custom", "model": "first"},
                          "second": {"provider": "custom", "model": "second"}})
    config["extensions"]["local-judge"] = True
    installed.backend.write_state("orchestrator-core", installed.backend.validate_settings(config))
    answer = installed.backend.route({"goal": "native task"})
    assert answer["route"] == {"provider": "custom", "model": "second", "fallback_providers": []}
    assert answer["selected"]["model_id"] == "second"
    installed.native_config["plugins"]["enabled"].remove("local-judge")
    before = len(ollama[1]["calls"])
    installed.backend.route({"goal": "no judge now"})
    assert len(ollama[1]["calls"]) == before


def test_free_discovery_gets_catalog_zero_prices_and_local_inventory(installed, monkeypatch):
    free = installed.add("free-models")
    responses = {"https://openrouter.ai/api/v1/models": {"data": [
        {"id": "free", "pricing": {"prompt": "0", "completion": "0"}},
        {"id": "paid", "pricing": {"prompt": "0.01", "completion": "0"}},
        {"id": "request-fee", "pricing": {"prompt": "0", "completion": "0", "request": "0.01"}}]},
        "http://127.0.0.1:11434/api/tags": {"models": [{"name": "local", "digest": "abc"}, {"name": "remote:cloud"}]}}
    calls = []
    monkeypatch.setattr(free, "_get", lambda url, **kw: calls.append(url) or responses[url])
    assert calls == []
    receipt = free.refresh()
    assert receipt["complete"] is True
    assert {card["model_id"] for card in receipt["models"]} == {"free", "local"}
    assert set(calls) == set(responses)
    assert all(card["billing_mode"] in {"free", "local"} for card in receipt["models"])
    # A malformed/failed check is not a successful empty catalog and does not announce removal.
    responses["https://openrouter.ai/api/v1/models"] = {"data": [{"id": "unknown:free"}]}
    failed = free.refresh()
    assert failed["complete"] is False
    assert "free" in {card["model_id"] for card in failed["models"]}
    assert not any(event["kind"] == "removed" for event in failed["events"])


def test_live_catalog_promotion_expiry_import_ttl_and_removal(installed, monkeypatch):
    free = installed.add("free-models")
    card = free.parse_openrouter({"data": [{"id": "expired", "pricing": {"prompt": "0", "completion": "0"},
                                          "expiration_date": "2020-01-01"}]})[0]
    assert card.expires_at < time.time()
    store = free.cache(); store.apply("openrouter-live", [card])
    assert free.models() == []
    with pytest.raises(ValueError, match="observation timestamp"):
        free.import_models({"source": "ollama", "payload": {"models": []}})
    imported = free.import_models({"source": "ollama", "payload": {"models": [{"name": "old"}]}, "observed_at": 0})
    assert imported["models"] == []


def test_pairing_stores_only_node_grant_and_never_returns_it(installed, monkeypatch):
    token = "private-node-grant"
    node = {"owner_id": "owner", "node_id": "node", "capabilities": {"platform": "test"}}
    class Fleet:
        @staticmethod
        def redeem_pairing(*args, **kwargs):
            assert kwargs["capabilities"] == {"platform": "test"}
            return {"token": token, "node": node}
    fake = types.SimpleNamespace(FleetClient=Fleet, advertised_capabilities=lambda config: {"platform": "test"})
    config = installed.backend.defaults(); config["server"]["url"] = "https://owned.example"
    installed.backend.write_state("orchestrator-core", config)
    original = installed.backend.embedded
    monkeypatch.setattr(installed.backend, "embedded", lambda name: fake if name == "fleet_client" else original(name))
    result = installed.backend.pairing({"action": "redeem", "code": "one-time", "label": "desktop"})
    assert result == {"paired": True, "node": node}
    assert token not in json.dumps(result)
    assert token not in json.dumps(installed.backend.settings())
    path = installed.backend.state_path(filename="node-grant.json")
    assert json.loads(path.read_text()) == {"token": token, "server_url": "https://owned.example", "owner_id": "owner", "node_id": "node"}
    security = installed.backend.private_file_security(path)
    assert security["secure"] is True
    if os.name == "nt":
        assert security["mechanism"] == "windows-dacl" and security["protected"] is True
    else:
        assert security["mechanism"] == "posix-mode" and security["mode"] == 0o600
    config = installed.backend.settings(); config["server"]["url"] = "https://other.example"
    installed.backend.write_state("orchestrator-core", config)
    with pytest.raises(ValueError, match="does not match"):
        installed.backend._fleet(node=True)


def test_private_writer_protects_before_token_write_and_verifies_final_file(installed, monkeypatch, tmp_path):
    original = installed.backend._protect_private_file
    protected = []
    def protect_empty(path):
        assert path.stat().st_size == 0
        original(path)
        assert installed.backend.private_file_security(path)["secure"] is True
        protected.append(path)
    monkeypatch.setattr(installed.backend, "_protect_private_file", protect_empty)
    path = tmp_path / "transport-grant.json"
    installed.backend.write_private(path, {"token": "private-fixture"})
    assert len(protected) == 1 and not protected[0].exists()
    assert json.loads(path.read_text()) == {"token": "private-fixture"}
    assert installed.backend.private_file_security(path)["secure"] is True


def test_private_file_audit_rejects_real_platform_permission_widening(installed, tmp_path):
    path = tmp_path / "transport-grant.json"
    installed.backend.write_private(path, {"token": "private-fixture"})
    if os.name == "nt":
        import subprocess
        # Numeric Everyone SID needs no account lookup. This changes a fixture,
        # and verifies that the audit reads the actual DACL, not DOS mode bits.
        subprocess.run(["icacls", str(path), "/grant", "*S-1-1-0:R"],
                       check=True, capture_output=True, timeout=10)
    else:
        path.chmod(0o644)
    assert installed.backend.private_file_security(path)["secure"] is False
    installed.backend._protect_private_file(path)
    assert installed.backend.private_file_security(path)["secure"] is True


def test_optional_judge_model_pull_progress_success_and_cancel_are_honest(installed, monkeypatch):
    import io
    judge = installed.add("local-judge")
    memory = types.ModuleType("agent.memory_provider")
    memory.spawn_context_thread = lambda target, **kwargs: threading.Thread(target=target, **kwargs)
    monkeypatch.setitem(sys.modules, "agent.memory_provider", memory)
    routing = installed.backend.embedded("client_router")
    original_judge = routing.OllamaLocalJudge
    calls = []
    allow = threading.Event()
    class Stream(io.BytesIO):
        def readline(self, size=-1):
            if not allow.is_set():
                allow.wait(1)
            return super().readline(size)
    class Opener:
        def open(self, request, timeout):
            calls.append((request.full_url, json.loads(request.data)))
            return Stream(b'{"status":"downloading","total":100,"completed":50}\n{"status":"success"}\n')
    def fake_judge(*args, **kwargs):
        result = original_judge(*args, **kwargs)
        result._opener = Opener()
        return result
    monkeypatch.setattr(routing, "OllamaLocalJudge", fake_judge)
    assert calls == []
    job = judge.start_pull({"model": "tiny-local:1b"})
    assert judge.start_pull({"model": "another-local"})["job_id"] == job["job_id"]
    allow.set()
    for _ in range(100):
        status = judge.pull_status(job["job_id"])
        if status["status"] not in {"queued", "running"}: break
        time.sleep(0.01)
    assert status["status"] == "complete" and status["progress"] == 1
    assert calls == [("http://127.0.0.1:11434/api/pull", {"model": "tiny-local:1b", "stream": True})]
    allow.clear()
    second = judge.start_pull({"model": "tiny-local:1b"})
    cancelled = judge.cancel_pull(second["job_id"])
    assert cancelled["status"] == "cancelling"
    allow.set()
    for _ in range(100):
        status = judge.pull_status(second["job_id"])
        if status["status"] not in {"queued", "running", "cancelling"}: break
        time.sleep(0.01)
    assert status["status"] == "cancelled"
    assert status["download_outcome"] in {"unknown", "not_started"}
    assert not any(key.startswith("_") for key in status)
    with pytest.raises(ValueError):
        judge.start_pull({"model": "huge:cloud"})


def test_judge_setup_missing_runtime_does_not_fall_back_to_paid(installed, monkeypatch):
    judge = installed.add("local-judge")
    def unavailable(*args): raise ConnectionError("local runtime missing")
    monkeypatch.setattr(judge, "test_connection", unavailable)
    result = judge.setup()
    assert result["runtime_reachable"] is False
    assert result["model_installed"] is False
    assert result["weights_bundled"] is False
    assert result["official_download_url"] == "https://ollama.com/download"


@pytest.mark.parametrize('secret', [{'provider_api_key':'secret'}, {'env':{'OPENAI_API_KEY':'secret'}},
                                   {'headers':{'Authorization':'secret'}}, {'custom_token':'secret'}])
def test_no_provider_credential_aliases_in_settings(installed, secret):
    config = installed.backend.defaults(); config['node']['runtime'] = secret
    with pytest.raises(ValueError, match='authentication'):
        installed.backend.validate_settings(config)
    assert not installed.backend.state_path().exists()


def test_node_config_cannot_override_verified_server_or_change_running_identity(installed):
    config = installed.backend.defaults()
    config['node']['server'] = {'url':'https://not-owned.example'}
    with pytest.raises(ValueError, match='Unknown node'):
        installed.backend.validate_settings(config)
    config = installed.backend.defaults()
    installed.backend.write_state('orchestrator-core',config)
    installed.backend._WORKERS[str(installed.home)] = {'thread':types.SimpleNamespace(is_alive=lambda:True),
                                                       'stop':threading.Event()}
    changed = deepcopy(config); changed['server']['url'] = 'https://new.example'
    with pytest.raises(ValueError, match='Stop the node worker'):
        installed.backend.write_state('orchestrator-core',changed)
    with pytest.raises(ValueError, match='Stop the node worker'):
        installed.backend.pairing({'action':'redeem','code':'new'})
    assert installed.backend.settings()['server']['url'] == ''


def test_agent_fleet_bridge_is_narrow_owner_scoped_and_approval_gated(installed, monkeypatch):
    bridge = installed.backend.load_file(installed.core/'fleet_tools.py','native_fleet_tools')
    class Context:
        def register_tool(self, **kwargs): self.tool=kwargs
        def register_hook(self,name,callback): self.approval=callback
    ctx=Context(); bridge.register(ctx,installed.backend)
    assert not ctx.tool['check_fn']()
    denied=json.loads(ctx.tool['handler']({'action':'list'}))
    assert denied['submitted'] is False
    config=installed.backend.defaults()
    config['server']['url']='https://owner.example'
    config['fleet_tools']={'enabled':True,'allowed_tools':['terminal'],'max_actions':5}
    installed.backend.write_state('orchestrator-core',config)
    calls=[]
    class Fleet:
        def nodes(self): return [{'node_id':'desktop','owner_id':'owner','capabilities':{'tools':['terminal'],'workspace_aliases':['reports'],'max_actions':10}}]
        def submit(self,task_id,payload): calls.append((task_id,payload)); return {'task_id':task_id,'status':'queued'}
        def task(self,task_id,*args): return {'task_id':task_id,'status':'unknown'}
    monkeypatch.setattr(installed.backend,'_fleet',lambda:Fleet())
    args={'action':'submit','task_id':'stable-id','target':'desktop','task':'Create a report',
          'workspace_alias':'reports','tools':['terminal'],'max_actions':3}
    assert ctx.approval('orchestrator_fleet',args)['action']=='approve'
    assert ctx.approval('orchestrator_fleet',{'action':'list'}) is None
    result=json.loads(ctx.tool['handler'](args))
    assert result['task']['status']=='queued'
    assert calls[0][1]['client_id']=='owner'
    assert calls[0][1]['relative_dir']=='.'
    assert 'workdir' not in calls[0][1]
    forbidden=json.loads(ctx.tool['handler']({**args,'tools':['arbitrary-shell']}))
    assert forbidden['submitted'] is False and len(calls)==1
    assert json.loads(ctx.tool['handler']({'action':'status','task_id':'stable-id'}))['task']['status']=='unknown'


def test_scored_profile_route_and_native_enabled_uninstall_are_rejected(installed, monkeypatch):
    config=installed.backend.defaults(); config['routes']={'model':{'profile':'unaudited'}}
    with pytest.raises(ValueError,match='fallback audit'):
        installed.backend.validate_settings(config)
    free=installed.add('free-models')
    with pytest.raises(ValueError,match='Disable'):
        installed.backend.remove_package('free-models')
    installed.native_config['plugins']['enabled'].remove('free-models')
    with free.operation_scope():
        with pytest.raises(ValueError,match='operations'):
            installed.backend.remove_package('free-models')
    removed=[]
    monkeypatch.setattr(installed.backend,'packages',lambda:types.SimpleNamespace(remove_extension=lambda name,**kw:removed.append(name) or {'removed':True}))
    assert installed.backend.remove_package('free-models') == {'removed':True}
    assert removed==['free-models']


def test_actual_fleet_tool_http_node_mcp_and_verified_receipt(installed, monkeypatch, tmp_path):
    if not (ROOT/'src/fleet_client.py').is_file():
        pytest.skip('fleet transport lands in its independent PR; exercised again after integration')
    from contextlib import ExitStack,contextmanager
    import sys
    fleet=installed.backend.embedded('fleet_client')
    queue_module=installed.backend.embedded('cloud_queue')
    cloud_module=installed.backend.embedded('cloud_server')
    marker=tmp_path/'node-workspace';marker.mkdir()
    calls=[]
    class MCP(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            request=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            assert request['method']=='tools/call'
            params=request['params'];calls.append(params)
            assert params['name']=='write_note'
            (marker/params['arguments']['path']).write_text(params['arguments']['text'])
            raw=json.dumps({'jsonrpc':'2.0','id':request['id'],'result':{'content':[{'type':'text','text':'created'}],'isError':False}}).encode()
            self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    @contextmanager
    def serving(server):
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:yield f'http://127.0.0.1:{server.server_port}'
        finally:server.shutdown();server.server_close();thread.join(timeout=2)
    token='owner-native-tool-http-test-token'
    queue=queue_module.CloudQueue(tmp_path/'fleet.sqlite')
    cloud=cloud_module.make_server('127.0.0.1',0,queue,{token:{'role':'owner','owner_id':'owner','accounts':[]}})
    with ExitStack() as stack:
        url=stack.enter_context(serving(cloud))
        tool_url=stack.enter_context(serving(ThreadingHTTPServer(('127.0.0.1',0),MCP)))
        owner=fleet.FleetClient(url,token)
        config={'server':{'url':url},'workspaces':{'notes':str(marker)},'receipt_dir':str(tmp_path/'receipts'),
            'runtime':{'allowed_tools':['write_note'],'max_actions':3,
                'models':[{'model_id':'direct-tools','provider':'mcp','executor':'mcp','billing_mode':'local','available':True,
                           'capabilities':{'reasoning':0.8}}],
                'adapters':{'mcp':{'url':tool_url,'tools':{'write_note':{'allowed_arguments':['path','text'],
                    'required_arguments':['path','text'],'path_arguments':['path']}}}},
                'verification':{'command':[sys.executable,'-c',"from pathlib import Path;assert Path('note.txt').read_text()=='verified local tools'"]}}}
        code=owner.create_pairing(label='tool-node')['code']
        paired=fleet.FleetClient.redeem_pairing(url,code,label='tool-node',capabilities=fleet.advertised_capabilities(config))
        config.update(owner_id=paired['node']['owner_id'],node_id=paired['node']['node_id'])
        policy=installed.backend.defaults();policy['server']['url']=url
        policy['fleet_tools']={'enabled':True,'allowed_tools':['write_note'],'max_actions':3}
        installed.backend.write_state('orchestrator-core',policy)
        monkeypatch.setattr(installed.backend,'_fleet',lambda:owner)
        bridge=installed.backend.load_file(installed.core/'fleet_tools.py','native_fleet_tools')
        class Context:
            def register_tool(self,**kwargs):self.tool=kwargs
            def register_hook(self,name,callback):self.approval=callback
        ctx=Context();bridge.register(ctx,installed.backend)
        args={'action':'submit','task_id':'native-mcp','target':config['node_id'],'task':'Write the approved note',
              'tools':['write_note'],'max_actions':1,'workspace_alias':'notes',
              'tool_calls':[{'name':'write_note','arguments':{'path':'note.txt','text':'verified local tools'}}],
              'task_card':{'type':'general','value_level':'normal','requires':[]}}
        assert 'tool_calls' in ctx.tool['schema']['parameters']['properties']
        assert ctx.approval('orchestrator_fleet',args)['action']=='approve'
        submitted=json.loads(ctx.tool['handler'](args))
        assert submitted['task']['status']=='queued',submitted
        completed=fleet.fleet_node_once(config,paired['token'])
        assert completed['status']=='done',completed
        assert (marker/'note.txt').read_text()=='verified local tools'
        assert len(calls)==1
        receipt=json.loads(ctx.tool['handler']({'action':'status','task_id':'native-mcp'}))
        assert receipt['task']['status']=='done'
        assert receipt['task']['result']['verified'] is True
