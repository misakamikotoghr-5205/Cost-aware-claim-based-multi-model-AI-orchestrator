"""Opt-in execution bridges; task data never becomes a shell command.

CLI process isolation is an operator responsibility. See docs/executors.md for
upstream protocol pins and the distinction between CLI turn and MCP action limits.
"""
from __future__ import annotations

import ipaddress
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from typing import Callable, Mapping, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


@dataclass
class ExecutionRequest:
    task_id: str
    prompt: str
    model: str
    workdir: str
    timeout: float = 120
    tools: list[str] = field(default_factory=list)
    max_actions: int = 20
    metadata: dict = field(default_factory=dict)


@dataclass
class ExecutionResult:
    status: str
    output: str = ""
    error: str = ""
    usage: dict = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)
    retryable: bool = False

    def __post_init__(self) -> None:
        if self.status not in {"succeeded", "failed", "unknown"}:
            raise ValueError("invalid execution status")
        if self.status == "unknown" and self.retryable:
            raise ValueError("unknown outcomes must not be automatically retried")


class Adapter(Protocol):
    def execute(self, request: ExecutionRequest) -> ExecutionResult: ...


class _RestrictedAdapter:
    def __init__(self, workspace_root: str | Path, *, max_timeout: float = 600):
        self.workspace_root = Path(workspace_root).resolve(strict=True)
        if not self.workspace_root.is_dir():
            raise ValueError("workspace_root must be a directory")
        if not math.isfinite(max_timeout) or max_timeout <= 0:
            raise ValueError("max_timeout must be positive and finite")
        self.max_timeout = max_timeout

    def _workdir(self, request: ExecutionRequest) -> Path:
        path = Path(request.workdir).resolve(strict=True)
        if not path.is_dir() or not path.is_relative_to(self.workspace_root):
            raise ValueError("workdir must be inside workspace_root")
        if not request.task_id or not isinstance(request.prompt, str) or not request.prompt.strip():
            raise ValueError("task_id and nonempty prompt are required")
        if not isinstance(request.model, str) or not request.model.strip():
            raise ValueError("model is required")
        if not math.isfinite(request.timeout) or not 0 < request.timeout <= self.max_timeout:
            raise ValueError("timeout is outside configured bounds")
        if isinstance(request.max_actions, bool) or not isinstance(request.max_actions, int) or request.max_actions < 1:
            raise ValueError("max_actions must be a positive integer")
        if not isinstance(request.tools, list) or not all(isinstance(t, str) and t for t in request.tools):
            raise ValueError("tools must be a list of nonempty names")
        if not isinstance(request.metadata, dict):
            raise ValueError("metadata must be an object")
        return path


def _failure(error: str, *, retryable: bool = False) -> ExecutionResult:
    return ExecutionResult("failed", error=error, retryable=retryable)


@dataclass
class _ProcessResult:
    returncode: int
    stdout: str
    stderr: str
    uncertain: bool = False
    error: str = ""


def _stop_process(process: subprocess.Popen) -> None:
    """Kill the owned POSIX process group, including surviving grandchildren."""
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass
    process.communicate(timeout=5)


def _run_process(command: list[str], workdir: Path, prompt: str, timeout: float,
                 max_output_bytes: int = 4 * 1024 * 1024) -> _ProcessResult:
    # Spool to files, checking size during execution: communicate() with PIPE
    # output can allocate unbounded RAM before an upstream agent finishes.
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(command, cwd=str(workdir), stdin=subprocess.PIPE,
                                   stdout=stdout, stderr=stderr, shell=False,
                                   start_new_session=os.name == "posix")
        deadline = time.monotonic() + timeout
        first = True
        uncertain, error = False, ""
        while True:
            size = os.fstat(stdout.fileno()).st_size + os.fstat(stderr.fileno()).st_size
            remaining = deadline - time.monotonic()
            if size > max_output_bytes or remaining <= 0:
                uncertain = True
                error = "output limit exceeded" if size > max_output_bytes else "execution timed out"
                _stop_process(process)
                break
            try:
                process.communicate(input=prompt.encode("utf-8") if first else None,
                                    timeout=min(0.05, remaining))
                break
            except subprocess.TimeoutExpired:
                first = False
        size = os.fstat(stdout.fileno()).st_size + os.fstat(stderr.fileno()).st_size
        if size > max_output_bytes:
            uncertain, error = True, "output limit exceeded"
        stdout.seek(0)
        stderr.seek(0)
        return _ProcessResult(process.returncode, stdout.read(max_output_bytes).decode("utf-8", "replace"),
                              stderr.read(min(max_output_bytes, 8192)).decode("utf-8", "replace"),
                              uncertain, error)


class _CLIAdapter(_RestrictedAdapter):
    def __init__(self, workspace_root: str | Path, *, command: Sequence[str],
                 billing_mode: str = "unknown", allow_paid_api: bool = False,
                 max_timeout: float = 600):
        super().__init__(workspace_root, max_timeout=max_timeout)
        if isinstance(command, str) or not command or not all(isinstance(x, str) and x for x in command):
            raise ValueError("command must be an explicit argv sequence")
        self.command = list(command)
        self.billing_mode = billing_mode
        self.allow_paid_api = allow_paid_api

    def _argv(self, request: ExecutionRequest) -> list[str]:
        raise NotImplementedError

    def _parse(self, events: list[dict], request: ExecutionRequest) -> ExecutionResult:
        raise NotImplementedError

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        try:
            workdir = self._workdir(request)
            if self.billing_mode not in {"local", "free", "subscription"}:
                if self.billing_mode != "paid_api" or not self.allow_paid_api:
                    return _failure("billing mode is not explicitly authorized")
            argv = self._argv(request)
        except (ValueError, TypeError, OSError) as exc:
            return _failure(str(exc))
        try:
            process = _run_process(argv, workdir, request.prompt, request.timeout)
        except FileNotFoundError:
            return _failure("executor executable not found", retryable=True)
        except subprocess.TimeoutExpired:
            return ExecutionResult("unknown", error="executor cleanup timed out; reconcile before retry")
        except OSError as exc:
            return _failure(f"executor could not start: {exc}")
        if process.uncertain:
            return ExecutionResult("unknown", error=process.error)
        events = []
        try:
            for line in process.stdout.splitlines():
                if not line.strip():
                    continue
                event = json.loads(line)
                if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                    raise ValueError("invalid event envelope")
                events.append(event)
        except (ValueError, TypeError):
            return ExecutionResult("unknown", error="malformed or incompatible JSONL event stream")
        if process.returncode != 0:
            return ExecutionResult("failed", error=f"executor exited {process.returncode}", events=events)
        result = self._parse(events, request)
        actions = sum(e.get("type") in {"tool_use", "tool_call"} for e in events)
        if actions > request.max_actions:
            return ExecutionResult("unknown", output=result.output, error="CLI reported more tool calls than action budget",
                                   usage=result.usage, events=events)
        return result


class HermesAdapter(_CLIAdapter):
    """Hermes stream-json CLI. Request tools are Hermes toolset identifiers."""
    def __init__(self, workspace_root: str | Path, *, provider: str,
                 allowed_toolsets: Sequence[str], command: Sequence[str] = ("hermes",),
                 billing_mode: str = "unknown", allow_paid_api: bool = False,
                 max_timeout: float = 600):
        super().__init__(workspace_root, command=command, billing_mode=billing_mode,
                         allow_paid_api=allow_paid_api, max_timeout=max_timeout)
        if not provider or provider == "auto":
            raise ValueError("an explicit inference provider is required")
        if isinstance(allowed_toolsets, str) or not all(isinstance(t, str) and t for t in allowed_toolsets):
            raise ValueError("allowed_toolsets must be a sequence of nonempty names")
        self.provider = provider
        self.allowed_toolsets = frozenset(allowed_toolsets)
        if any(t in {"all", "*"} or "," in t for t in self.allowed_toolsets):
            raise ValueError("wildcard and composite toolset grants are forbidden")

    def _argv(self, request: ExecutionRequest) -> list[str]:
        if not request.tools or not set(request.tools) <= self.allowed_toolsets:
            raise ValueError("explicit requested toolsets must be granted by operator")
        return self.command + ["chat", "--query-file", "-", "--format", "stream-json", "--oneshot",
                               "--model", request.model, "--provider", self.provider,
                               "--toolsets", ",".join(request.tools), "--max-turns", str(request.max_actions)]

    def _parse(self, events: list[dict], request: ExecutionRequest) -> ExecutionResult:
        terminal = [e for e in events if e["type"] == "result"]
        if len(terminal) != 1 or events[-1] is not terminal[0]:
            return ExecutionResult("unknown", error="missing, duplicate, or misplaced Hermes result", events=events)
        result = terminal[0]
        if type(result.get("exit_code")) is not int:
            return ExecutionResult("unknown", error="invalid Hermes terminal exit_code", events=events)
        if result.get("exit_code") != 0 or result.get("error"):
            return ExecutionResult("failed", error=str(result.get("error") or "Hermes run failed"), events=events)
        text = result.get("text")
        if not isinstance(text, str) or not text.strip():
            return ExecutionResult("unknown", error="Hermes terminal result has no answer", events=events)
        tokens = result.get("tokens")
        usage = {f"{k}_tokens": v for k, v in tokens.items()} if isinstance(tokens, dict) else {}
        return ExecutionResult("succeeded", output=text, usage=usage, events=events)


class DeepSeekHarnessAdapter(_CLIAdapter):
    """Harness 0.2.1-rc.3 headless; model/tool composition is operator pinned."""
    def __init__(self, workspace_root: str | Path, *, configured_model: str,
                 configured_tools: Sequence[str] = (), profile: str = "headless",
                 patch_file: str | None = None, command: Sequence[str] = ("dsh",),
                 billing_mode: str = "unknown", allow_paid_api: bool = False,
                 max_timeout: float = 600):
        super().__init__(workspace_root, command=command, billing_mode=billing_mode,
                         allow_paid_api=allow_paid_api, max_timeout=max_timeout)
        if not configured_model or not profile or profile.startswith("-"):
            raise ValueError("configured_model and profile must be explicit")
        if isinstance(configured_tools, str) or not all(isinstance(t, str) and t for t in configured_tools):
            raise ValueError("configured_tools must be a sequence of nonempty names")
        self.configured_model, self.profile = configured_model, profile
        self.configured_tools = frozenset(configured_tools)
        self.patch_file = str(Path(patch_file).resolve(strict=True)) if patch_file else None

    def _argv(self, request: ExecutionRequest) -> list[str]:
        if request.model != self.configured_model:
            raise ValueError("request model differs from pinned Harness profile")
        if set(request.tools) != self.configured_tools:
            raise ValueError("requested tools differ from pinned Harness composition")
        argv = self.command + ["--profile", self.profile]
        if self.patch_file:
            argv += ["--patch", self.patch_file]
        # Never bypass approvals or grant extra directories. Operator profile
        # supplies sandbox/tool policy; unattended approval requests are denied.
        return argv + ["--json", "--approval-policy", "never", "-"]

    def _parse(self, events: list[dict], request: ExecutionRequest) -> ExecutionResult:
        terminal = [e for e in events if e["type"] == "final"]
        if len(terminal) != 1 or events[-1] is not terminal[0]:
            return ExecutionResult("unknown", error="missing, duplicate, or misplaced Harness final", events=events)
        result = terminal[0]
        reason = result.get("reason")
        if not isinstance(reason, dict) or reason.get("kind") != "completed":
            return ExecutionResult("failed", error="Harness turn did not complete", events=events)
        text = result.get("text")
        if not isinstance(text, str) or not text.strip():
            return ExecutionResult("unknown", error="Harness final has no answer", events=events)
        steps = [e["usage"] for e in events if e["type"] == "status" and e.get("phase") == "step_end"
                 and isinstance(e.get("usage"), dict)]
        return ExecutionResult("succeeded", output=text, usage={"steps": steps} if steps else {}, events=events)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class OllamaAdapter(_RestrictedAdapter):
    """Native Ollama /api/chat, local loopback only, no implicit tool execution."""
    def __init__(self, workspace_root: str | Path, *, base_url: str = "http://127.0.0.1:11434",
                 max_timeout: float = 600):
        super().__init__(workspace_root, max_timeout=max_timeout)
        url = urlsplit(base_url)
        try:
            local = url.hostname == "localhost" or ipaddress.ip_address(url.hostname or "").is_loopback
        except ValueError:
            local = False
        if not local or url.scheme not in {"http", "https"} or url.username or url.password or url.path not in {"", "/"} or url.query or url.fragment:
            raise ValueError("Ollama endpoint must be a credential-free loopback URL")
        self.base_url = base_url.rstrip("/")
        self._opener = build_opener(_NoRedirect())

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        try:
            self._workdir(request)
            if "cloud" in request.model.lower():
                return _failure("cloud Ollama models are not authorized")
            if request.tools:
                return _failure("Ollama adapter performs inference only; use MCPAdapter for explicit tool plans")
            payload = json.dumps({"model": request.model, "messages": [{"role": "user", "content": request.prompt}],
                                  "stream": False}).encode("utf-8")
            req = Request(self.base_url + "/api/chat", data=payload, headers={"Content-Type": "application/json"}, method="POST")
            with self._opener.open(req, timeout=request.timeout) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                return ExecutionResult("unknown", error="Ollama response limit exceeded")
            try:
                data = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                return ExecutionResult("unknown", error="malformed Ollama JSON response")
            if not isinstance(data, dict):
                return ExecutionResult("unknown", error="invalid Ollama response")
            if data.get("error"):
                return _failure(str(data["error"]))
            message = data.get("message", {})
            if not isinstance(message, dict) or data.get("done") is not True or message.get("tool_calls"):
                return ExecutionResult("unknown", error="Ollama response incomplete or contains unexecuted tools")
            text = message.get("content")
            if not isinstance(text, str) or not text.strip():
                return ExecutionResult("unknown", error="Ollama response has no answer")
            usage = {k: data[k] for k in ("prompt_eval_count", "eval_count", "total_duration") if k in data}
            return ExecutionResult("succeeded", output=text, usage=usage, events=[{"type": "ollama_response", "done": True}])
        except HTTPError as exc:
            return _failure(f"Ollama HTTP {exc.code}", retryable=exc.code in {429, 502, 503, 504})
        except (TimeoutError, URLError):
            return ExecutionResult("unknown", error="Ollama request interrupted; consumption is unknown")
        except (ValueError, TypeError, OSError) as exc:
            return _failure(str(exc))


class MCPClient(Protocol):
    """Transport bridge: enforce timeout at the actual MCP transport boundary."""
    def call_tool(self, name: str, arguments: dict, *, timeout: float) -> dict: ...


class MCPAdapter(_RestrictedAdapter):
    """Run an explicit MCP plan through per-tool, operator-owned validators.

    The client returns the MCP CallToolResult wire dictionary. It must enforce
    the provided deadline, and must never implicitly retry a mutating call.
    """
    def __init__(self, workspace_root: str | Path, *, client: MCPClient,
                 tool_validators: Mapping[str, Callable[[dict, Path], bool]], max_timeout: float = 600):
        super().__init__(workspace_root, max_timeout=max_timeout)
        self.client = client
        self.tool_validators = dict(tool_validators)
        if not all(isinstance(k, str) and callable(v) for k, v in self.tool_validators.items()):
            raise ValueError("each tool requires an argument validator")

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        try:
            workdir = self._workdir(request)
            calls = request.metadata.get("tool_calls")
            if not isinstance(calls, list) or not calls or len(calls) > request.max_actions:
                return _failure("explicit nonempty tool_calls must fit max_actions")
            # Validate the entire plan before invoking even its first tool.
            for call in calls:
                if not isinstance(call, dict) or set(call) != {"name", "arguments"}:
                    return _failure("tool call must contain exactly name and arguments")
                name, arguments = call["name"], call["arguments"]
                if not isinstance(name, str) or name not in request.tools or name not in self.tool_validators:
                    return _failure("tool is not granted")
                if not isinstance(arguments, dict) or self.tool_validators[name](arguments, workdir) is not True:
                    return _failure("tool arguments rejected by local policy")
        except Exception:
            return _failure("MCP plan validation failed")
        events, output = [], []
        deadline = time.monotonic() + request.timeout
        for call in calls:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return ExecutionResult("unknown", output="\n".join(output), error="MCP execution deadline exceeded", events=events)
            events.append({"type": "tool_call", "name": call["name"]})
            try:
                result = self.client.call_tool(call["name"], call["arguments"], timeout=remaining)
            except Exception:
                # A transport exception can occur after the server committed a
                # write. An automatic retry could duplicate the side effect.
                return ExecutionResult("unknown", output="\n".join(output), error="MCP call interrupted; reconcile before retry", events=events)
            if (not isinstance(result, dict) or not isinstance(result.get("content"), list)
                    or ("isError" in result and not isinstance(result["isError"], bool))):
                return ExecutionResult("unknown", error="malformed MCP CallToolResult", events=events)
            try:
                if len(json.dumps(result).encode("utf-8")) > 4 * 1024 * 1024:
                    return ExecutionResult("unknown", error="MCP response limit exceeded", events=events)
            except (TypeError, ValueError):
                return ExecutionResult("unknown", error="non-JSON MCP result", events=events)
            if time.monotonic() > deadline:
                return ExecutionResult("unknown", error="MCP result arrived after deadline", events=events)
            if result.get("isError") is True:
                return ExecutionResult("failed", error="MCP tool reported an error", events=events)
            for block in result["content"]:
                if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                    output.append(block["text"])
            events.append({"type": "tool_result", "name": call["name"], "is_error": False})
        return ExecutionResult("succeeded", output="\n".join(output), events=events)
