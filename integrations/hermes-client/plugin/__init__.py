"""Native Hermes delegation provider. Importing this package starts no work."""
def register(ctx):
    from pathlib import Path
    import hashlib
    import importlib.util
    import sys
    from agent.delegation_provider import DelegationProvider, DelegationRouteError
    path = Path(__file__).resolve().with_name("backend.py")
    name = "native_orchestrator_backend_" + hashlib.sha256(str(path).encode()).hexdigest()[:12]
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    backend = sys.modules[name]

    class OrchestratorDelegation(DelegationProvider):
        @property
        def name(self):
            return "orchestrator-core"

        @property
        def display_name(self):
            return "本机确定性调度器"

        @property
        def summary(self):
            return "按需评分并调用已安装扩展，子任务仍由原生客户端执行。"

        def task_parameters(self, config):
            return {"task_type": {"type": "string", "enum": ["general", "code_generation", "research", "writing", "multimodal"]},
                    "value_level": {"type": "string", "enum": ["trivial", "normal", "important", "critical"]},
                    "requires": {"type": "array", "items": {"type": "string"}, "maxItems": 32}}

        def route_task(self, task, config):
            try:
                if not backend.settings()["enabled"]:
                    return {key: value for key, value in config.items() if key != "service_tier"}
                return backend.route(task, config)["route"]
            except (ValueError, RuntimeError) as error:
                raise DelegationRouteError(str(error)) from None

    ctx.register_delegation_provider(OrchestratorDelegation())
    tools = backend.load_file(Path(__file__).resolve().with_name("fleet_tools.py"), "native_fleet_tools")
    tools.register(ctx, backend)
    profile_home = backend.home()
    ctx.on_unload(lambda: backend.stop_worker(profile_home))
