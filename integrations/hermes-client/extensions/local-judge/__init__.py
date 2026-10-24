"""Optional local model extension; importing/registering starts no inference/download."""
def register(ctx):
    from pathlib import Path
    import hashlib
    import importlib.util
    import sys
    path = Path(__file__).resolve().with_name("backend.py")
    name = "native_extension_backend_" + hashlib.sha256(str(path).encode()).hexdigest()[:12]
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    backend = sys.modules[name]
    profile_home = backend.core().home()
    ctx.on_unload(lambda: backend.cancel_all(profile_home))
