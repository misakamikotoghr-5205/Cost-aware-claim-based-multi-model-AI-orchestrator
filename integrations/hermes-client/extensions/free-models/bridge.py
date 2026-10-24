"""Resolve the installed core under Hermes profile/root locations."""
from pathlib import Path
import hashlib
import importlib.util
import sys


def core():
    from hermes_constants import get_hermes_home, get_default_hermes_root
    for root in dict.fromkeys([Path(get_hermes_home()), Path(get_default_hermes_root())]):
        path = (root / "plugins" / "orchestrator-core" / "backend.py").resolve()
        if not path.is_file():
            continue
        name = "native_orchestrator_backend_" + hashlib.sha256(str(path).encode()).hexdigest()[:12]
        if name not in sys.modules:
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
        return sys.modules[name]
    raise RuntimeError("Install orchestrator-core in this Hermes profile/root first")
