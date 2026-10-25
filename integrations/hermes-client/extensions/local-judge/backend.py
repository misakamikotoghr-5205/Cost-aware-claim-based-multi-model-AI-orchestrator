"""Explicit local Ollama judgement, with deterministic fallback on failed inference."""
from pathlib import Path
import importlib.util
import json
import math
import hashlib
import sys

_bridge = Path(__file__).with_name("bridge.py")
_name = "orchestrator_bridge_" + hashlib.sha256(str(_bridge).encode()).hexdigest()[:12]
if _name not in sys.modules:
    _spec = importlib.util.spec_from_file_location(_name, _bridge)
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[_name] = _mod
    _spec.loader.exec_module(_mod)
core = sys.modules[_name].core
ID = "local-judge"
DEFAULTS = {"base_url": "http://127.0.0.1:11434", "model": "qwen3:4b", "timeout": 5,
            "max_task_chars": 512, "max_candidates": 3}


def settings():
    return {**DEFAULTS, **core().read_state(ID, DEFAULTS)}


def save(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise ValueError("Unknown local judge setting")
    candidate = {**DEFAULTS, **value}
    core()._safe(candidate)
    core().embedded("client_router").OllamaLocalJudge(**candidate)
    return core().write_state(ID, candidate)


def make_judge():
    base = core().embedded("client_router").OllamaLocalJudge
    class VerifiedLocalJudge(base):
        def choose(self, task, cards, ranking):
            verify_local_weights(self)
            return super().choose(task, cards, ranking)
    return VerifiedLocalJudge(**settings())


def _metadata(judge, path, body=None):
    from urllib.request import Request
    request = Request(judge.base_url + path, data=None if body is None else json.dumps(body).encode(),
                      headers={"Content-Type": "application/json"}, method="GET" if body is None else "POST")
    with judge._opener.open(request, timeout=judge.timeout) as response:
        raw = response.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("Ollama metadata exceeds size limit")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Invalid Ollama metadata")
    return value


def verify_local_weights(judge, tags=None):
    tags = _metadata(judge, "/api/tags") if tags is None else tags
    local = next((row for row in tags.get("models", []) if isinstance(row, dict)
                  and row.get("name", row.get("model")) == judge.model), None)
    if (not local or local.get("remote_host") or local.get("remote_model")
            or type(local.get("size")) is not int or local["size"] <= 0):
        raise ValueError("Selected judge has no verified local weights")
    info = _metadata(judge, "/api/show", {"model": judge.model})
    if (info.get("remote_host") or info.get("remote_model") or not isinstance(info.get("model_info"), dict)
            or not info["model_info"].get("general.architecture")):
        raise ValueError("Selected judge is remote or has no local architecture metadata")
    return True


def test_connection(value=None):
    config = {**settings(), **(value or {})}
    judge = core().embedded("client_router").OllamaLocalJudge(**config)
    tags = _metadata(judge, "/api/tags")
    cards = core().embedded("discovery").parse_ollama(tags)
    names = [card.model_id for card in cards if card.billing_mode == "local"]
    try:
        verified = verify_local_weights(judge, tags)
    except ValueError:
        verified = False
    return {"ok": verified, "models": names, "configured_model": judge.model,
            "inference_performed": False}


def judge_task(body):
    lib = core()
    discovery, routing = lib.embedded("discovery"), lib.embedded("client_router")
    cards = [discovery.ModelCard.from_dict(item) for item in body.get("candidates", [])]
    task = lib.embedded("task_card").TaskCard.from_dict(body["task"])
    receipt = routing.ClientRouter(judge=make_judge()).route(task, cards,
        available_tools=body.get("available_tools", []), required_tools=body.get("required_tools", []),
        quota_states=body.get("quota_states", {}))
    return receipt.to_dict()

# Downloads are explicit, bounded local operations, with no hosted-model fallback.
import threading
import time
import uuid
from urllib.request import Request

_JOBS = {}
_JOBS_LOCK = threading.RLock()


def setup():
    try:
        result = test_connection()
        reachable, installed = True, result["ok"]
        names = result["models"]
    except Exception:
        reachable, installed, names = False, False, []
    return {"runtime_reachable": reachable, "model_installed": installed, "model": settings()["model"],
            "models": names, "platform": sys.platform, "official_download_url": "https://ollama.com/download",
            "weights_bundled": False, "inference_performed": False}


def _view(job):
    with _JOBS_LOCK:
        return {key: value for key, value in job.items() if not key.startswith("_")}


def pull_status(job_id):
    with _JOBS_LOCK:
        job = _JOBS.get((str(core().home()), job_id))
        if job is None:
            raise ValueError("No download job in this profile")
        return _view(job)


def cancel_pull(job_id):
    with _JOBS_LOCK:
        job = _JOBS.get((str(core().home()), job_id))
        if job is None:
            raise ValueError("No download job in this profile")
        if job["status"] in {"queued", "running", "cancelling"}:
            job["_stop"].set()
            job.update(status="cancelling", detail="Cancelling local download stream; remote completion is not confirmed")
        return _view(job)


def cancel_all(profile_home):
    with _JOBS_LOCK:
        for (owner, _), job in _JOBS.items():
            if owner == str(profile_home) and job["status"] in {"queued", "running", "cancelling"}:
                job["_stop"].set()
                job["status"] = "cancelling"


def start_pull(body):
    config = settings()
    model = body.get("model", config["model"])
    # The existing helper checks loopback origin and rejects hosted/cloud names.
    judge = core().embedded("client_router").OllamaLocalJudge(config["base_url"], model, min(config["timeout"], 30))
    if len(model) > 200 or not __import__('re').fullmatch(r"(?:[A-Za-z0-9_.-]+/)?[A-Za-z0-9_.-]+(?::[A-Za-z0-9_.+-]+)?", model):
        raise ValueError("Use an exact local Ollama model identifier")
    owner, job_id = str(core().home()), uuid.uuid4().hex
    with _JOBS_LOCK:
        for (profile, _), job in _JOBS.items():
            if profile == owner and job["status"] in {"queued", "running", "cancelling"}:
                return _view(job)  # Duplicate button presses reuse one operation.
        job = {"job_id": job_id, "model": model, "status": "queued", "progress": 0,
               "detail": "Waiting for local Ollama", "download_outcome": "pending",
               "started_at": time.time(), "_stop": threading.Event()}
        _JOBS[(owner, job_id)] = job
    def run():
        response = None
        try:
            if job["_stop"].is_set():
                with _JOBS_LOCK:
                    job.update(status="cancelled", download_outcome="not_started", detail="Cancelled before download submission")
                return
            request = Request(judge.base_url + "/api/pull", data=json.dumps({"model": model, "stream": True}).encode(),
                              headers={"Content-Type": "application/json"}, method="POST")
            with _JOBS_LOCK:
                job["status"] = "running"
            response = judge._opener.open(request, timeout=30)
            success = False
            while not job["_stop"].is_set():
                raw = response.readline(65537)
                if not raw:
                    break
                if len(raw) > 65536:
                    raise ValueError("Ollama progress record exceeds size limit")
                item = json.loads(raw)
                if not isinstance(item, dict) or item.get("error"):
                    raise ValueError("Ollama reported a download error")
                status = item.get("status", "")
                if not isinstance(status, str):
                    raise ValueError("Invalid Ollama progress status")
                completed, total = item.get("completed"), item.get("total")
                with _JOBS_LOCK:
                    job["detail"] = status[:500]
                    if (type(completed) in (int, float) and type(total) in (int, float)
                            and math.isfinite(completed) and math.isfinite(total) and 0 <= completed <= total and total > 0):
                        job["progress"] = completed / total
                        job["completed_bytes"], job["total_bytes"] = completed, total
                if status == "success":
                    success = True
                    break
            with _JOBS_LOCK:
                if job["_stop"].is_set():
                    job.update(status="cancelled", download_outcome="unknown", detail="Local tracking cancelled; check installed models before retrying")
                elif success:
                    job.update(status="complete", progress=1, download_outcome="success", detail="Ollama confirmed model installation")
                else:
                    job.update(status="failed", download_outcome="unknown", detail="Download stream ended before confirmed success")
        except Exception as error:
            with _JOBS_LOCK:
                job.update(status="cancelled" if job["_stop"].is_set() else "failed", download_outcome="unknown",
                           detail=f"Local download not confirmed ({type(error).__name__}); inspect installed models")
        finally:
            if response is not None:
                response.close()
            with _JOBS_LOCK:
                job["finished_at"] = time.time()
    from agent.memory_provider import spawn_context_thread
    thread = spawn_context_thread(run, name="ollama-model-pull-" + job_id[:8])
    job["_thread"] = thread
    thread.start()
    return _view(job)


from contextlib import contextmanager
import threading
_ACTIVE_OPERATIONS = {}
_ACTIVITY_LOCK = threading.RLock()


@contextmanager
def operation_scope():
    owner = str(core().home())
    with _ACTIVITY_LOCK:
        _ACTIVE_OPERATIONS[owner] = _ACTIVE_OPERATIONS.get(owner, 0) + 1
    try:
        yield
    finally:
        with _ACTIVITY_LOCK:
            _ACTIVE_OPERATIONS[owner] -= 1


def is_busy():
    owner = str(core().home())
    with _ACTIVITY_LOCK:
        active = _ACTIVE_OPERATIONS.get(owner, 0) > 0
    with _JOBS_LOCK:
        return active or any(profile == owner and job.get("_thread") and job["_thread"].is_alive()
                             for (profile, _), job in _JOBS.items())
