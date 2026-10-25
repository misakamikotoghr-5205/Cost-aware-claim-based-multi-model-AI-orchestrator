"""Official Hermes plugin REST router; request profile binding belongs to Hermes."""
from pathlib import Path
import importlib.util
import hashlib
import sys
import json
from fastapi import APIRouter, HTTPException

_file = Path(__file__).resolve().parents[1] / "backend.py"
_name = "native_orchestrator_backend_" + hashlib.sha256(str(_file).encode()).hexdigest()[:12]
if _name not in sys.modules:
    _spec = importlib.util.spec_from_file_location(_name, _file)
    _module = importlib.util.module_from_spec(_spec)
    sys.modules[_name] = _module
    _spec.loader.exec_module(_module)
backend = sys.modules[_name]
router = APIRouter()


def invoke(operation, *args):
    try:
        return operation(*args)
    except (ValueError, TypeError, KeyError) as error:
        raise HTTPException(400, str(error)) from None
    except Exception as error:
        raise HTTPException(503, f"Operation unavailable ({type(error).__name__}); check local configuration") from None


@router.get("/settings")
def get_settings():
    return invoke(backend.settings)


@router.put("/settings")
def put_settings(body: dict):
    return invoke(lambda: backend.write_state(backend.PLUGIN_ID, backend.validate_settings(body)))


@router.get("/extensions")
def get_extensions():
    return {"extensions": invoke(backend.inventory)}


@router.post("/extensions/install")
def install_extension(body: dict):
    return invoke(lambda: backend.packages().install_archive(body["archive_path"], hermes_home=backend.home()))


@router.post("/extensions/{extension_id}/remove")
def remove_extension(extension_id: str):
    return invoke(backend.remove_package, extension_id)


@router.post("/route")
def preview_route(body: dict):
    return invoke(backend.route, body.get("task", body))


@router.get("/nodes")
def get_nodes():
    return invoke(backend.nodes)


@router.get("/quota/dashboard")
def quota_dashboard():
    return invoke(backend.quota_dashboard)


@router.post("/quota/refresh")
def quota_refresh():
    return invoke(backend.quota_refresh)


@router.post("/quota/import")
def quota_import(body: dict):
    return invoke(backend.quota_import, body)


@router.post("/pairing")
def pair(body: dict):
    return invoke(backend.pairing, body)


@router.post("/pairing/redeem")
def redeem(body: dict):
    return invoke(backend.pairing, {**body, "action": "redeem"})


@router.post("/nodes/{node_id}/revoke")
def revoke_node(node_id: str):
    return invoke(lambda: {"node": backend._fleet().revoke_node(node_id)})


@router.post("/nodes/select")
def select_node(body: dict):
    return invoke(lambda: {"node": backend._fleet().select_node(**body)})


@router.post("/tasks")
def submit_task(body: dict):
    return invoke(lambda: backend._fleet().submit(body["task_id"], body["payload"]))


@router.get("/worker")
@router.get("/node/status")
def worker_state():
    return invoke(backend.worker_status)


@router.post("/node/run_once")
def run_node_once():
    return invoke(backend.run_node_once)


@router.post("/worker/start")
def worker_start():
    return invoke(backend.start_worker)


@router.post("/worker/stop")
def worker_stop():
    return invoke(backend.stop_worker)


@router.get("/tasks/{task_id}")
def get_task(task_id: str):
    return invoke(lambda: {"task": backend._fleet().task(task_id)})


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str):
    return invoke(lambda: {"task": backend._fleet().task(task_id, "cancel", {})})


@router.post("/agent/fleet")
def agent_fleet(body: dict):
    # The external-agent skill and native model tool share one owner/node/tool/
    # workspace admission path. Host agents retain their own permission policy.
    module = backend.load_file(Path(__file__).resolve().parents[1] / "fleet_tools.py", "native_fleet_api")
    return invoke(lambda: json.loads(module.perform(backend, body)))
