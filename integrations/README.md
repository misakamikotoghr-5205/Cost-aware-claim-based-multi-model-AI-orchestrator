# Integrations

Reference adapters that show how the orchestrator's *decisions* (who should do
a task) get turned into *execution* (actually running that worker) on a real
machine.

## `hermes_pipeline_router.py`
A working executor that drives real worker CLIs:

- Hermes providers (Grok / Codex / Gemini / DeepSeek)
- Claude Code CLI (`claude`)
- Antigravity CLI (`agy`), including the empty-stdout transcript recovery and
  the "model flag not resolved" guard

```bash
# point it at your binaries (all optional, sensible home-dir defaults)
export HPR_HERMES_BIN=...   HPR_CLAUDE_BIN=...   HPR_AGY_BIN=...
# optionally restrict where workers may write:
export HPR_WORKDIR_PREFIX="E:\\agents"

python integrations/hermes_pipeline_router.py --worker codex --task "Write a hello-world CLI"
```

It holds **no secrets** — each backend authenticates itself. Treat this as a
template: the planner picks `--worker`, this runs it and records the result.

> The orchestrator (`src/`) and this executor are intentionally decoupled: the
> planner decides *who*, the executor decides *how to actually run it here*.
