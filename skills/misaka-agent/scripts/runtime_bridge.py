"""One local HTTP request inside the explicitly selected Hermes runtime.

The native-http ticket is minted and consumed here. It never leaves this process
through stdout or files. Runtime ownership/ACL/pipe checks use the native client.
"""
from contextlib import redirect_stdout
import ipaddress
import json
import os
import re
from pathlib import Path
import sys
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

MAX_BYTES = 2 * 1024 * 1024
PRIVATE_FIELDS={'token','ticket','api_key','apikey','authorization','cookie','cookies','password',
                'credentials','headers','access_token','refresh_token','lease_token'}


def public_result(value):
    """Lease/auth credentials stay in the native runtime, not the host agent."""
    if isinstance(value,dict):
        return {key:public_result(item) for key,item in value.items()
                if key.lower() not in PRIVATE_FIELDS and not key.lower().endswith(('_token','_password','_api_key'))}
    if isinstance(value,list):return [public_result(item) for item in value]
    return value


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise ValueError("redirect_refused")


def validate_endpoint(value, expected_home):
    if (not isinstance(value, dict) or value.get("runtime_protocol") != 1
            or type(value.get("runtime_protocol")) is not int
            or not isinstance(value.get('profile_id'),str)
            or os.path.normcase(str(Path(value['profile_id']).resolve())) != os.path.normcase(str(Path(expected_home).resolve()))
            or not isinstance(value.get("instance_id"), str) or not value["instance_id"]):
        raise ValueError("profile_or_protocol_mismatch")
    parsed = urlsplit(value.get("api_origin", ""))
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1"}
            or not ipaddress.ip_address(parsed.hostname).is_loopback
            or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path
            or parsed.port is None or not 1 <= parsed.port <= 65535):
        raise ValueError("nonlocal_origin_refused")
    return value["api_origin"]


def request(payload):
    endpoint = payload["endpoint"]
    origin = validate_endpoint(endpoint, payload["home"])
    path, method = payload["path"], payload["method"]
    plugin=payload.get('plugin','orchestrator-core')
    allowed = {("GET", "/extensions"), ("GET", "/worker"), ("GET", "/quota/dashboard"),
               ("POST", "/quota/refresh"), ("POST", "/route"), ("POST", "/agent/fleet")}
    optional={('free-models','GET','/models'),('free-models','POST','/refresh'),
              ('local-judge','POST','/test'),('smart-pointer','POST','/sessions')}
    intent=plugin=='smart-pointer' and method=='POST' and re.fullmatch(r'/sessions/[A-Za-z0-9_-]{1,128}/(?:refine|commit)',path)
    if not ((plugin=='orchestrator-core' and (method,path) in allowed) or (plugin,method,path) in optional or intent):
        raise ValueError("operation_refused")
    if Path(os.environ.get("HERMES_HOME", "")).resolve() != Path(payload["home"]).resolve():
        raise ValueError("profile_environment_mismatch")
    with redirect_stdout(sys.stderr):
        from hermes_cli.gateway_client import _session_ticket
        from hermes_cli.gateway_runtime_discovery import _private_node
        if os.name != "nt":
            _private_node(Path(payload["home"]), kind="directory", home=True)
        ticket = _session_ticket(Path(payload["home"]), SimpleNamespace(**endpoint), purpose="native-http")
    data = None if method == "GET" else json.dumps(payload.get("body", {}), allow_nan=False).encode()
    req = Request(origin + "/api/plugins/" + plugin + path, data=data, method=method,
                  headers={"X-Hermes-Gateway-Ticket": ticket, "Content-Type": "application/json"})
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(req, timeout=15) as response:
            raw = response.read(MAX_BYTES + 1)
    except HTTPError as exc:
        if exc.code==404 and intent:
            return {'ok':False,'error':'intent_session_missing_or_expired','http_status':404}
        return {"ok": False, "error": ("core_unavailable" if plugin=='orchestrator-core' else 'extension_unavailable') if exc.code == 404 else "native_http_refused",
                "http_status": exc.code}
    if len(raw) > MAX_BYTES:
        raise ValueError("response_too_large")
    return {"ok": True, "result": public_result(json.loads(raw))}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        raw = sys.stdin.buffer.read(131073)
        if len(raw) > 131072:
            raise ValueError("request_too_large")
        value = request(json.loads(raw))
    except Exception:
        value = {"ok": False, "error": "native_request_unconfirmed"}
    print(json.dumps(value, ensure_ascii=False, allow_nan=False))
