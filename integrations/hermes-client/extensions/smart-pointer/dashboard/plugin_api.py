"""Native Hermes dashboard API, mounted at /api/plugins/smart-pointer.

Hermes enforces authentication, profile routing and plugins.enabled before
loading this module. The standalone install ZIP includes _impl/pointer_intents.py;
no core, judge, discovery extension or checkout import is needed.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading

from fastapi import APIRouter, HTTPException

router = APIRouter()
_LOCK = threading.RLock()
_MODULE = None
_SERVICES = {}
_DEFAULTS = {"local_suggestions": {"enabled": False, "model": "qwen3:4b",
                                    "base_url": "http://127.0.0.1:11434", "timeout": 2}}


def _implementation():
    global _MODULE
    with _LOCK:
        if _MODULE is None:
            path = Path(__file__).resolve().parents[1] / "_impl" / "pointer_intents.py"
            if not path.is_file():
                raise HTTPException(503, detail="Smart Pointer implementation is missing; reinstall its standalone extension ZIP")
            name = "hermes_smart_pointer_" + hashlib.sha256(str(path).encode()).hexdigest()[:16]
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            try:
                spec.loader.exec_module(module)
            except BaseException:
                sys.modules.pop(name, None)
                raise
            _MODULE = module
        return _MODULE


def _home():
    from hermes_constants import get_hermes_home
    return Path(get_hermes_home()).resolve()


def _fields(body, required, optional=()):
    if not isinstance(body, dict) or not set(required) <= body.keys() or body.keys() - set(required) - set(optional):
        raise HTTPException(400, detail="missing or unsupported request fields")


def _normalize_settings(body):
    _fields(body, ("local_suggestions",))
    spec = body["local_suggestions"]
    _fields(spec, ("enabled",), ("model", "base_url", "timeout"))
    if not isinstance(spec["enabled"], bool):
        raise HTTPException(400, detail="local suggestion enabled must be boolean")
    normalized = dict(_DEFAULTS["local_suggestions"], **spec)
    try:
        # Constructor validates local-only configuration without making requests.
        _implementation().LocalOllamaSuggestions(normalized["model"], normalized["base_url"], normalized["timeout"])
    except (ValueError, TypeError, OverflowError):
        raise HTTPException(400, detail="use an installed local model, a loopback Ollama URL and a timeout of 0.05 to 5 seconds") from None
    return {"local_suggestions": normalized}


def _settings(home):
    path = home / "smart-pointer" / "settings.json"
    if not path.exists():
        return json.loads(json.dumps(_DEFAULTS))
    try:
        with path.open("rb") as stream:
            raw = stream.read(16 * 1024 + 1)
        if len(raw) > 16 * 1024:
            raise ValueError()
        return _normalize_settings(json.loads(raw))
    except (OSError, ValueError):
        raise HTTPException(400, detail="Smart Pointer settings are invalid; save valid settings again") from None


def _service():
    home = _home()
    with _LOCK:
        settings = _settings(home)
        signature = json.dumps(settings, sort_keys=True)
        existing = _SERVICES.get(str(home))
        if existing is None or existing[0] != signature:
            module = _implementation()
            spec = settings["local_suggestions"]
            suggestions = module.LocalOllamaSuggestions(spec["model"], spec["base_url"], spec["timeout"]) if spec["enabled"] else None
            existing = (signature, module.IntentSessions(suggestions))
            # Hosts normally isolate profiles into separate processes; bound the
            # fallback map too without making sessions persistent on disk.
            if len(_SERVICES) >= 16:
                _SERVICES.pop(next(iter(_SERVICES)))
            _SERVICES[str(home)] = existing
        return existing[1]


def _invoke(operation, *args, **kwargs):
    module = _implementation()
    try:
        return getattr(_service(), operation)(*args, **kwargs)
    except module.RevisionConflict as exc:
        raise HTTPException(409, detail=str(exc)) from None
    except module.SessionNotFound as exc:
        raise HTTPException(404, detail=str(exc)) from None
    except (module.IntentError, TypeError, ValueError, OverflowError):
        raise HTTPException(400, detail="invalid Smart Pointer context, keywords or selection") from None


@router.get("/settings")
def get_settings():
    return _settings(_home())


@router.post("/settings")
def save_settings(body: dict):
    settings = _normalize_settings(body)
    home = _home()
    directory = home / "smart-pointer"
    directory.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, delete=False) as stream:
            temporary = stream.name
            json.dump(settings, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, directory / "settings.json")
    finally:
        if temporary and Path(temporary).exists():
            Path(temporary).unlink()
    with _LOCK:
        _SERVICES.pop(str(home), None)
    return settings


@router.post("/sessions")
@router.post("/candidates")
def create_session(body: dict):
    _fields(body, ("context",), ("keywords",))
    return _invoke("create", body["context"], body.get("keywords", ""))


@router.get("/sessions/{session_id}")
def get_session(session_id: str):
    return _invoke("get", session_id)


@router.post("/sessions/{session_id}/refine")
def refine_session(session_id: str, body: dict):
    _fields(body, ("revision",), ("keywords", "reject_action_ids", "context"))
    return _invoke("refine", session_id, body["revision"], body.get("keywords", ""),
                   body.get("reject_action_ids"), body.get("context"))


@router.post("/sessions/{session_id}/commit")
def commit_session(session_id: str, body: dict):
    _fields(body, ("revision", "action_id"))
    return _invoke("commit", session_id, body["revision"], body["action_id"])
