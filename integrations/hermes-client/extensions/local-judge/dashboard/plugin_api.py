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


@router.post("/test")
def test_connection(body: dict = {}):
    return invoke(backend.test_connection, body)


@router.post("/judge")
def judge(body: dict):
    return invoke(backend.judge_task, body)


@router.get("/setup")
def setup():
    return invoke(backend.setup)


@router.post("/models/pull")
def start_pull(body: dict = {}):
    return invoke(backend.start_pull, body)


@router.get("/models/pull/{job_id}")
def pull_status(job_id: str):
    return invoke(backend.pull_status, job_id)


@router.post("/models/pull/{job_id}/cancel")
def cancel_pull(job_id: str):
    return invoke(backend.cancel_pull, job_id)
