"""Hermes authenticated plugin REST routes, scoped by the active profile."""
from pathlib import Path
import importlib.util
import hashlib
import sys
from fastapi import APIRouter, HTTPException
_file = Path(__file__).resolve().parents[1] / "backend.py"
_name = "native_extension_backend_" + hashlib.sha256(str(_file).encode()).hexdigest()[:12]
if _name not in sys.modules:
    _spec = importlib.util.spec_from_file_location(_name, _file)
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[_name] = _mod
    _spec.loader.exec_module(_mod)
backend = sys.modules[_name]
router = APIRouter()


def invoke(operation, *args):
    try:
        with backend.operation_scope():
            return operation(*args)
    except (ValueError, KeyError, TypeError) as error:
        raise HTTPException(400, str(error)) from None
    except Exception as error:
        raise HTTPException(503, f"Operation unavailable ({type(error).__name__})") from None


@router.get("/settings")
def get_settings():
    return invoke(backend.settings)


@router.put("/settings")
def put_settings(body: dict):
    return invoke(backend.save, body)


@router.post("/refresh")
def refresh():
    return invoke(backend.refresh)


@router.get("/models")
def models():
    return {"models": [card.to_dict() for card in invoke(backend.models)]}


@router.post("/import")
def import_models(body: dict):
    return invoke(backend.import_models, body)
