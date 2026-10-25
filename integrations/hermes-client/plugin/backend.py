"""Profile-scoped settings, native delegation and explicit fleet controls.

No provider credential is read or copied. Hermes owns native conversations and
provider authentication; this module selects a declared route for child tasks.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib
import importlib.util
import json
import math
from pathlib import Path
import re
import stat
import sys
import tempfile
import os
import threading
import time
from urllib.parse import urlsplit

PLUGIN_ID = "orchestrator-core"
VERSION = "0.2.1"
EXTENSIONS = ("local-judge", "free-models")
ALL_EXTENSIONS = (*EXTENSIONS, "smart-pointer")
FORBIDDEN = {"api_key", "apikey", "password", "token", "access_token", "refresh_token",
             "authorization", "cookie", "cookies", "credentials", "auth", "env", "headers",
             "environment", "env_vars", "secrets"}
_IMPORT_LOCK = threading.RLock()
_WORKERS = {}
_WORKER_LOCK = threading.RLock()
_ONESHOTS = set()
_QUOTA_DASHBOARDS = {}
_QUOTA_DASHBOARD_LOCK = threading.RLock()


def home() -> Path:
    from hermes_constants import get_hermes_home
    return Path(get_hermes_home()).resolve()


def roots() -> list[Path]:
    from hermes_constants import get_default_hermes_root
    return list(dict.fromkeys([home() / "plugins", Path(get_default_hermes_root()).resolve() / "plugins"]))


def plugin_path(name: str) -> Path | None:
    if name not in (PLUGIN_ID, *ALL_EXTENSIONS):
        raise ValueError("Unknown extension")
    for root in roots():
        candidate = root / name
        if candidate.is_dir() and (candidate / "plugin.yaml").is_file():
            manifest = read_manifest(candidate / "plugin.yaml")
            if isinstance(manifest, dict) and manifest.get("name") == name:
                return candidate
    return None


def read_manifest(path):
    try:
        from hermes_yaml import safe_load
    except ImportError:
        from yaml import safe_load
    return safe_load(path.read_text(encoding="utf-8-sig"))


def embedded(name: str):
    """Load the bundled scorer by package path, never a workspace/sys.path hack."""
    directory = Path(__file__).resolve().parent / "_orchestrator"
    if not (directory / "__init__.py").is_file():
        raise RuntimeError("Install the built orchestrator-core package with its scorer payload")
    package = "orchestrator_payload_" + hashlib.sha256(str(directory).encode()).hexdigest()[:12]
    with _IMPORT_LOCK:
        if package not in sys.modules:
            spec = importlib.util.spec_from_file_location(package, directory / "__init__.py",
                                                         submodule_search_locations=[str(directory)])
            module = importlib.util.module_from_spec(spec)
            sys.modules[package] = module
            spec.loader.exec_module(module)
        return importlib.import_module(f"{package}.{name}")


def load_file(path: Path, prefix: str = "orchestrator_extension"):
    name = prefix + "_" + hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:12]
    with _IMPORT_LOCK:
        if name not in sys.modules:
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            try:
                spec.loader.exec_module(module)
            except BaseException:
                sys.modules.pop(name, None)
                raise
        return sys.modules[name]


def _safe(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if (not isinstance(key, str) or key.lower() in FORBIDDEN
                    or key.lower().endswith(("_api_key", "_token", "_password"))
                    or "authorization" in key.lower() or "cookie" in key.lower()):
                raise ValueError("Provider authentication belongs to Hermes; use a secret environment name for fleet access")
            if key.endswith("_env") and (not isinstance(item, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item)):
                raise ValueError("Secret selectors must contain a variable name, never its value")
            _safe(item)
    elif isinstance(value, list):
        for item in value:
            _safe(item)
    elif value is not None and type(value) not in (str, int, float, bool):
        raise ValueError("Settings must be JSON")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Settings require finite numbers")


def state_path(plugin=PLUGIN_ID, filename="settings.json") -> Path:
    if plugin not in (PLUGIN_ID, *EXTENSIONS) or filename not in {"settings.json", "node-grant.json", "models.json"}:
        raise ValueError("Invalid state file")
    return home() / "plugin-data" / plugin / filename


def read_state(plugin=PLUGIN_ID, default=None):
    path = state_path(plugin)
    if not path.exists():
        return deepcopy(default or {})
    raw = path.read_bytes()
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("Settings exceed size limit")
    data = json.loads(raw)
    _safe(data)
    return data


class _WindowsPrivateFiles:
    """Protect real files with an explicit DACL; DOS mode bits are not ACLs."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes as w
        self.c, self.w = ctypes, w
        self.advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "OpenProcessToken": ([w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)], w.BOOL),
            "GetTokenInformation": ([w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD, ctypes.POINTER(w.DWORD)], w.BOOL),
            "ConvertSidToStringSidW": ([w.LPVOID, ctypes.POINTER(w.LPWSTR)], w.BOOL),
            "ConvertStringSecurityDescriptorToSecurityDescriptorW": ([w.LPCWSTR, w.DWORD, ctypes.POINTER(w.LPVOID), ctypes.POINTER(w.DWORD)], w.BOOL),
            "SetNamedSecurityInfoW": ([w.LPWSTR, ctypes.c_int, w.DWORD, w.LPVOID, w.LPVOID, w.LPVOID, w.LPVOID], w.DWORD),
            "GetFileSecurityW": ([w.LPCWSTR, w.DWORD, w.LPVOID, w.DWORD, ctypes.POINTER(w.DWORD)], w.BOOL),
            "GetSecurityDescriptorControl": ([w.LPVOID, ctypes.POINTER(w.WORD), ctypes.POINTER(w.DWORD)], w.BOOL),
            "GetSecurityDescriptorOwner": ([w.LPVOID, ctypes.POINTER(w.LPVOID), ctypes.POINTER(w.BOOL)], w.BOOL),
            "GetSecurityDescriptorDacl": ([w.LPVOID, ctypes.POINTER(w.BOOL), ctypes.POINTER(w.LPVOID), ctypes.POINTER(w.BOOL)], w.BOOL),
            "GetAclInformation": ([w.LPVOID, w.LPVOID, w.DWORD, ctypes.c_int], w.BOOL),
            "GetAce": ([w.LPVOID, w.DWORD, ctypes.POINTER(w.LPVOID)], w.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.advapi, name)
            function.argtypes, function.restype = arguments, result
        self.kernel.GetCurrentProcess.argtypes, self.kernel.GetCurrentProcess.restype = [], w.HANDLE
        self.kernel.CloseHandle.argtypes, self.kernel.CloseHandle.restype = [w.HANDLE], w.BOOL
        self.kernel.LocalFree.argtypes, self.kernel.LocalFree.restype = [w.LPVOID], w.LPVOID
        token = w.HANDLE()
        self._require(self.advapi.OpenProcessToken(self.kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)))
        try:
            needed = w.DWORD()
            self.advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
            if ctypes.get_last_error() != 122 or needed.value == 0:
                raise ctypes.WinError(ctypes.get_last_error())
            user = ctypes.create_string_buffer(needed.value)
            self._require(self.advapi.GetTokenInformation(token, 1, user, needed.value, ctypes.byref(needed)))
            sid = ctypes.cast(user, ctypes.POINTER(w.LPVOID)).contents.value
            self.user_sid = self._sid(sid)
        finally:
            self.kernel.CloseHandle(token)
        # Administrators and SYSTEM already control this process/its files.
        self.allowed_sids = {self.user_sid, "S-1-5-18", "S-1-5-32-544"}

    def _require(self, result):
        if not result:
            raise self.c.WinError(self.c.get_last_error())

    def _sid(self, sid):
        pointer = self.w.LPWSTR()
        self._require(self.advapi.ConvertSidToStringSidW(sid, self.c.byref(pointer)))
        try:
            return pointer.value
        finally:
            self.kernel.LocalFree(self.c.cast(pointer, self.w.LPVOID))

    def protect(self, path):
        # D:P sets SE_DACL_PROTECTED, preventing parent/Everyone inheritance.
        sddl = "D:P" + "".join(f"(A;;FA;;;{sid})" for sid in sorted(self.allowed_sids))
        descriptor = self.w.LPVOID()
        self._require(self.advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, self.c.byref(descriptor), None))
        try:
            present, defaulted, dacl = self.w.BOOL(), self.w.BOOL(), self.w.LPVOID()
            self._require(self.advapi.GetSecurityDescriptorDacl(
                descriptor, self.c.byref(present), self.c.byref(dacl), self.c.byref(defaulted)))
            result = self.advapi.SetNamedSecurityInfoW(str(path), 1, 0x00000004 | 0x80000000,
                                                      None, None, dacl, None)
            if result:
                raise self.c.WinError(result)
        finally:
            self.kernel.LocalFree(descriptor)

    def inspect(self, path):
        c, w = self.c, self.w
        needed = w.DWORD()
        flags = 0x00000001 | 0x00000004  # OWNER + DACL
        self.advapi.GetFileSecurityW(str(path), flags, None, 0, c.byref(needed))
        if c.get_last_error() != 122 or needed.value == 0:
            raise c.WinError(c.get_last_error())
        descriptor = c.create_string_buffer(needed.value)
        self._require(self.advapi.GetFileSecurityW(str(path), flags, descriptor, needed.value, c.byref(needed)))
        control, revision = w.WORD(), w.DWORD()
        self._require(self.advapi.GetSecurityDescriptorControl(descriptor, c.byref(control), c.byref(revision)))
        owner, defaulted = w.LPVOID(), w.BOOL()
        self._require(self.advapi.GetSecurityDescriptorOwner(descriptor, c.byref(owner), c.byref(defaulted)))
        owner_sid = self._sid(owner)
        present, dacl = w.BOOL(), w.LPVOID()
        self._require(self.advapi.GetSecurityDescriptorDacl(descriptor, c.byref(present), c.byref(dacl), c.byref(defaulted)))
        protected = bool(control.value & 0x1000)
        if not present.value or not dacl.value:
            return {"mechanism": "windows-dacl", "secure": False, "protected": protected, "owner": owner_sid, "trustees": []}

        class AclSize(c.Structure):
            _fields_ = [("count", w.DWORD), ("used", w.DWORD), ("free", w.DWORD)]

        class Ace(c.Structure):
            _fields_ = [("type", w.BYTE), ("flags", w.BYTE), ("size", w.WORD), ("mask", w.DWORD)]

        size = AclSize()
        self._require(self.advapi.GetAclInformation(dacl, c.byref(size), c.sizeof(size), 2))
        trustees, direct_full_access = [], True
        for index in range(size.count):
            pointer = w.LPVOID()
            self._require(self.advapi.GetAce(dacl, index, c.byref(pointer)))
            ace = c.cast(pointer, c.POINTER(Ace)).contents
            if ace.type != 0 or ace.size < 16:
                direct_full_access = False
                continue
            trustees.append(self._sid(pointer.value + 8))
            direct_full_access = direct_full_access and ace.flags == 0 and ace.mask == 0x001F01FF
        secure = (protected and direct_full_access and owner_sid in self.allowed_sids
                  and len(trustees) == len(self.allowed_sids) and set(trustees) == self.allowed_sids)
        return {"mechanism": "windows-dacl", "secure": secure, "protected": protected,
                "owner": owner_sid, "trustees": trustees}


def private_file_security(path: Path):
    """Inspect actual platform permissions without reading the file contents."""
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        return {"secure": False, "mechanism": "not-a-regular-file"}
    if os.name == "nt":
        return _WindowsPrivateFiles().inspect(path)
    mode = stat.S_IMODE(info.st_mode)
    return {"mechanism": "posix-mode", "mode": mode,
            "secure": mode == 0o600 and info.st_uid == os.getuid()}


def _protect_private_file(path: Path):
    if os.name == "nt":
        _WindowsPrivateFiles().protect(path)
    else:
        path.chmod(0o600)
    if private_file_security(path)["secure"] is not True:
        raise PermissionError("Private file access control could not be verified")


def write_private(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = stream.name
            # Protect the empty file before any transport token is written.
            _protect_private_file(Path(temporary))
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        try:
            if private_file_security(path)["secure"] is not True:
                raise PermissionError("Published private file access control could not be verified")
        except BaseException:
            path.unlink(missing_ok=True)
            raise
    finally:
        if temporary and Path(temporary).exists():
            Path(temporary).unlink()


def write_state(plugin: str, value: dict):
    _safe(value)
    if not isinstance(value, dict):
        raise ValueError("Settings require an object")
    if plugin == PLUGIN_ID:
        with _WORKER_LOCK:
            _reject_active_reconfiguration(value)
            write_private(state_path(plugin), value)
    else:
        write_private(state_path(plugin), value)
    return deepcopy(value)


def defaults():
    return {"enabled": False, "models": [], "routes": {}, "quota_states": {},
            "extensions": {"local-judge": False, "free-models": False},
            "fleet_tools": {"enabled": False, "allowed_tools": [], "max_actions": 20},
            "server": {"url": "", "token_env": "ORCHESTRATOR_TOKEN", "allow_http": False},
            "node": {"owner_id": "", "node_id": "", "workspaces": {}, "runtime": {}}}


def settings():
    return {**defaults(), **read_state(default={})}


def validate_settings(value):
    _safe(value)
    if not isinstance(value, dict) or set(value) - set(defaults()):
        raise ValueError("Unknown core setting")
    result = {**defaults(), **deepcopy(value)}
    if type(result["enabled"]) is not bool or not isinstance(result["models"], list):
        raise ValueError("Invalid routing settings")
    for key in ("routes", "quota_states", "extensions", "server", "node"):
        if not isinstance(result[key], dict):
            raise ValueError(f"{key} requires an object")
    node = result["node"]
    if set(node) - {"owner_id", "node_id", "workspaces", "runtime", "skills", "lease_seconds"}:
        raise ValueError("Unknown node setting")
    if not isinstance(node.get("workspaces", {}), dict) or not isinstance(node.get("runtime", {}), dict):
        raise ValueError("Node workspaces and runtime require objects")
    if set(result["extensions"]) - set(EXTENSIONS) or any(type(x) is not bool for x in result["extensions"].values()):
        raise ValueError("Unknown extension activation")
    for name, route in result["routes"].items():
        if not isinstance(name, str) or not isinstance(route, dict) or set(route) - {"provider", "model", "profile", "label", "base_url", "reasoning_effort"}:
            raise ValueError("Route must name a native provider/model or profile, without authentication")
        if any(not isinstance(item, str) for item in route.values()):
            raise ValueError("Route values must be strings")
        if route.get("profile"):
            raise ValueError("Profile routes require a fallback audit; use an explicit native provider and model for scored delegation")
        if not route.get("provider") or not route.get("model"):
            raise ValueError("Scored routes require an explicit provider and model")
        if route.get("base_url"):
            parsed = urlsplit(route["base_url"])
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("Native route endpoints cannot contain authentication or query parameters")
    server = result["server"]
    fleet = result["fleet_tools"]
    if (not isinstance(fleet, dict) or set(fleet) - {"enabled", "allowed_tools", "max_actions"}
            or type(fleet.get("enabled")) is not bool or not isinstance(fleet.get("allowed_tools"), list)
            or any(not isinstance(item, str) or not item for item in fleet["allowed_tools"])
            or type(fleet.get("max_actions")) is not int or not 1 <= fleet["max_actions"] <= 1000):
        raise ValueError("Invalid fleet tool policy")
    if set(server) - {"url", "token_env", "allow_http"}:
        raise ValueError("Unknown fleet setting")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", server.get("token_env", "ORCHESTRATOR_TOKEN")):
        raise ValueError("Fleet token_env must name a profile-scoped secret")
    if server.get("url"):
        parsed = urlsplit(server["url"])
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Fleet URL requires an HTTP(S) origin without credentials")
    if type(server.get("allow_http", False)) is not bool:
        raise ValueError("allow_http must be boolean")
    # Card validation happens before committing; user declarations contain no keys.
    if result["models"]:
        discovery = embedded("discovery")
        for item in result["models"]:
            discovery.ModelCard.from_dict({"observed_at": 0, **item})
    return result


def inventory():
    config = settings()
    items = []
    for name in ALL_EXTENSIONS:
        path = plugin_path(name)
        version = None
        if path is not None:
            version = str(read_manifest(path / "plugin.yaml").get("version", ""))
        native = native_enabled(name)
        items.append({"id": name, "version": version, "installed": path is not None,
                      "enabled": path is not None and native,
                      "routing_enabled": path is not None and name in EXTENSIONS and native and config["extensions"].get(name) is True})
    return items


def native_enabled(name):
    from hermes_cli.config import load_config_readonly
    plugins = load_config_readonly().get("plugins", {})
    enabled, disabled = plugins.get("enabled", []), plugins.get("disabled", [])
    return name in enabled and name not in disabled


def extension(name):
    if name not in EXTENSIONS or not native_enabled(name) or settings()["extensions"].get(name) is not True:
        return None
    path = plugin_path(name)
    return None if path is None else load_file(path / "backend.py", "native_extension_backend")


def route(task, delegation_config=None, *, judge_override=None):
    """Reuse ClientRouter at native child-task selection; leave turn execution to Hermes."""
    config = settings()
    if not config["enabled"]:
        return {"route": {}, "selected": None, "notes": ["Native Hermes route retained; optional router is inactive."]}
    now = time.time()
    discovery, routing = embedded("discovery"), embedded("client_router")
    cards = []
    for raw in config["models"]:
        # Persistent operator declarations are not a live discovery observation.
        value = {"observed_at": now, "source": "operator-config", **raw}
        cards.append(discovery.ModelCard.from_dict(value))
    free = extension("free-models")
    if free is not None:
        cards.extend(free.models())  # cache reads only, never a refresh/inference
    judge = judge_override
    if judge is None:
        optional = extension("local-judge")
        if optional is not None:
            judge = optional.make_judge()
    title = str(task.get("goal", task.get("title", "")))
    task_card = embedded("task_card").TaskCard.from_dict({"task_id": str(task.get("task_id", "native-delegation")),
        "title": title, "type": task.get("task_type", task.get("type", "general")), "requires": task.get("requires", []),
        "value_level": task.get("value_level", "normal")})
    tools = task.get("toolsets", []) or []
    if isinstance(tools, str):
        tools = [tools]
    receipt = routing.ClientRouter(judge=judge).route(task_card, cards, available_tools=tools,
                required_tools=tools, quota_states=config["quota_states"], now=now)
    answer = receipt.to_dict()
    if receipt.selected is None:
        raise ValueError("No declared model passed routing admission; check source freshness and quota")
    selected = receipt.selected
    declared = config["routes"].get(selected.model_id)
    if not declared:
        # Discovery cannot authorize a new native execution target by itself.
        raise ValueError("Selected candidate needs a configured native Hermes route")
    answer["route"] = {**deepcopy(declared), "fallback_providers": []}
    return answer


def _fleet(node=False):
    config = settings()
    server = config["server"]
    if node:
        token = _node_grant(config)["token"]
    else:
        from agent.secret_scope import get_secret
        token = get_secret(server.get("token_env", "ORCHESTRATOR_TOKEN")) or ""
    return embedded("fleet_client").FleetClient(server.get("url", ""), token,
                                              allow_http=server.get("allow_http", False))


def nodes():
    return {"nodes": _fleet().nodes()}


def _quota_dashboard():
    profile_home = home()
    with _QUOTA_DASHBOARD_LOCK:
        key = str(profile_home)
        if key not in _QUOTA_DASHBOARDS:
            _QUOTA_DASHBOARDS[key] = embedded("quota_dashboard").QuotaDashboard(profile_home)
        return _QUOTA_DASHBOARDS[key]


def quota_dashboard():
    value = _quota_dashboard().view()
    cloud_module = Path(__file__).with_name("quota_cloud.py")
    if cloud_module.is_file():
        module = load_file(cloud_module, "quota_cloud")
        value["cloud"] = module.view_cloud_quota(sys.modules[__name__], value["cloud"], time.time())
    state = worker_status()
    value["node"] = {key: state.get(key) for key in ("execution_location", "platform", "running")}
    value["node"]["status"] = "running" if state.get("running") else "stopped"
    return value


def quota_refresh():
    cloud_module = Path(__file__).with_name("quota_cloud.py")
    cloud_reader = None
    if cloud_module.is_file():
        # The cloud component only reads the owner's existing ledger. It never
        # receives model credentials and does not reserve or execute work.
        cloud_reader = lambda: load_file(cloud_module, "quota_cloud").read_cloud_quota(sys.modules[__name__])
    _quota_dashboard().refresh(cloud_reader=cloud_reader)
    return quota_dashboard()


def quota_import(body):
    _quota_dashboard().import_file(body)
    return quota_dashboard()


def pairing(body):
    with _WORKER_LOCK:
        return _pairing_locked(body)


def _pairing_locked(body):
    with _WORKER_LOCK:
        current = _WORKERS.get(str(home()))
        if str(home()) in _ONESHOTS or current and current["thread"].is_alive():
            raise ValueError("Stop the node worker and wait for completion before pairing")
    action = body.get("action", "create")
    if action == "create":
        return {"pairing": _fleet().create_pairing(label=body.get("label") or "New node",
                  accounts=body.get("accounts", []), ttl_seconds=body.get("ttl_seconds", 600))}
    if action != "redeem":
        raise ValueError("Pairing action must be create or redeem")
    server = settings()["server"]
    node_config = settings()["node"]
    capabilities = embedded("fleet_client").advertised_capabilities(node_config)
    result = embedded("fleet_client").FleetClient.redeem_pairing(
        server["url"], body.get("code", ""), label=body.get("label", ""),
        capabilities=capabilities, allow_http=server.get("allow_http", False))
    write_private(state_path(filename="node-grant.json"), {"token": result["token"],
                  "server_url": _server_binding(server), "owner_id": result["node"]["owner_id"],
                  "node_id": result["node"]["node_id"]})
    config = settings()
    config["node"].update(owner_id=result["node"]["owner_id"], node_id=result["node"]["node_id"])
    write_state(PLUGIN_ID, validate_settings(config))
    return {"node": result["node"], "paired": True}  # Never return token to a renderer.


def worker_status():
    with _WORKER_LOCK:
        worker = _WORKERS.get(str(home()))
        config = settings()
        background = bool(worker and worker["thread"].is_alive())
        one_shot = str(home()) in _ONESHOTS
        return {"running": background or one_shot, "background_running": background,
                "one_shot_running": one_shot, "platform": sys.platform,
                "execution_location": "this_backend_host", "owner_id": config["node"].get("owner_id", ""),
                "node_id": config["node"].get("node_id", ""),
                "workspace_aliases": sorted(config["node"].get("workspaces", {})),
                "last_result": worker.get("last_result") if worker else None,
                "last_error": worker.get("last_error") if worker else None}


def stop_worker(profile_home=None):
    with _WORKER_LOCK:
        worker = _WORKERS.get(str(profile_home or home()))
        if worker:
            worker["stop"].set()
    return {"stopping": bool(worker and worker["thread"].is_alive()),
            "one_shot_running": str(profile_home or home()) in _ONESHOTS}


def _server_binding(server):
    parsed = urlsplit(server.get("url", ""))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Configure the fleet server origin before pairing")
    host = parsed.hostname.lower()
    if ":" in host:
        host = "[" + host + "]"
    port = parsed.port
    suffix = "" if port is None or port == {"http": 80, "https": 443}[parsed.scheme] else ":" + str(port)
    return parsed.scheme + "://" + host + suffix + parsed.path.rstrip("/")


def _node_grant(config):
    path = state_path(filename="node-grant.json")
    if not path.is_file():
        raise ValueError("Pair this node first")
    grant = json.loads(path.read_text(encoding="utf-8-sig"))
    node = config["node"]
    if (grant.get("server_url") != _server_binding(config["server"])
            or grant.get("owner_id") != node.get("owner_id") or grant.get("node_id") != node.get("node_id")
            or not isinstance(grant.get("token"), str) or not grant["token"]):
        raise ValueError("Node transport grant does not match this server and identity; pair again")
    return grant


def _reject_active_reconfiguration(value):
    with _WORKER_LOCK:
        owner = str(home())
        worker = _WORKERS.get(owner)
        if owner not in _ONESHOTS and not (worker and worker["thread"].is_alive()):
            return
        old = read_state(default=defaults())
        if any(old.get(key) != value.get(key) for key in ("server", "node")):
            raise ValueError("Stop the node worker and wait before changing its server, identity or runtime")


def run_node_once():
    owner = str(home())
    with _WORKER_LOCK:
        if owner in _ONESHOTS or worker_status()["running"]:
            raise ValueError("A node worker is already executing; wait before a one-shot run")
        config = settings()
        token = _node_grant(config)["token"]
        _ONESHOTS.add(owner)
    try:
        node = {**config["node"], "server": config["server"],
                "receipt_dir": str(home() / "plugin-data" / PLUGIN_ID / "node-receipts")}
        return _execute_node_once(node, token)
    finally:
        with _WORKER_LOCK:
            _ONESHOTS.discard(owner)


def _execute_node_once(config, token):
    # Child CLI processes must inherit the owning profile, not os.environ's launch profile.
    from tools.environments.local import served_profile_child_env
    child_env = served_profile_child_env(inherit_credentials=True)
    client = embedded("fleet_client")
    execution = embedded("executors")
    if hasattr(execution, "execution_environment"):
        with execution.execution_environment(child_env):
            return client.fleet_node_once(config, token)
    raise RuntimeError("This core build lacks profile-scoped child-process environment support")


def packages():
    return load_file(Path(__file__).resolve().with_name("package_manager.py"), "orchestrator_package_manager")


def remove_package(name):
    if name not in ALL_EXTENSIONS:
        raise ValueError("Use native package management to remove the core")
    if native_enabled(name):
        raise ValueError("Disable the extension through native plugin controls before uninstalling it")
    path = plugin_path(name)
    if path is not None:
        module_name = "native_extension_backend_" + hashlib.sha256(str(path / "backend.py").encode()).hexdigest()[:12]
        module = sys.modules.get(module_name)
        if module is not None and getattr(module, "is_busy", lambda: False)():
            raise ValueError("Wait for extension operations to stop before uninstalling")
    return packages().remove_extension(name, hermes_home=home())


def start_worker():
    profile_home = home()
    with _WORKER_LOCK:
        prior = _WORKERS.get(str(profile_home))
        if str(profile_home) in _ONESHOTS:
            raise ValueError("Wait for the current one-shot node task before starting the worker")
        if prior and prior["thread"].is_alive():
            return worker_status()
        config = settings()
        token = _node_grant(config)["token"]
        stop = threading.Event()
        worker = {"stop": stop, "last_result": None, "last_error": None}
        def run():
            while not stop.is_set():
                try:
                    node = config["node"]
                    worker_config = {**node, "server": config["server"],
                                     "receipt_dir": str(profile_home / "plugin-data" / PLUGIN_ID / "node-receipts")}
                    worker["last_result"] = _execute_node_once(worker_config, token)
                    worker["last_error"] = None
                except Exception as error:
                    worker["last_error"] = type(error).__name__  # no backend/token text
                stop.wait(2)
        from agent.memory_provider import spawn_context_thread
        thread = spawn_context_thread(run, name="orchestrator-node-" + hashlib.sha256(str(profile_home).encode()).hexdigest()[:8])
        worker["thread"] = thread
        _WORKERS[str(profile_home)] = worker
        thread.start()
    return worker_status()
