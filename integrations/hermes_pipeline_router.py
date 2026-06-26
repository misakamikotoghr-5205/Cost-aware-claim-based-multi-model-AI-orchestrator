#!/usr/bin/env python
"""Reference executor integration: a personal Hermes pipeline router.

This is a REAL, working example of an "execution & verification" layer that the
cost-aware orchestrator's plans could drive. It runs already-configured workers
(Hermes Grok/Codex/Gemini/DeepSeek providers, Claude Code, Antigravity `agy`),
verifies outputs with independent checkers, and records each run as JSON.

It is intentionally a local orchestration helper, NOT a model provider, and it
holds NO secrets — every backend authenticates itself. Paths are env-driven
(see the HPR_* variables below); the optional workspace guard is opt-in.

Decoupled from the planner on purpose: the planner decides *who*; this decides
*how to actually run it* on one specific machine. Use it as a template for your
own executor adapter.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import textwrap
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

# ---------------------------------------------------------------------------
# Configuration (override via environment variables). No secrets live here:
# the underlying CLIs/providers manage their own auth.
# ---------------------------------------------------------------------------
def _env_path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default)).expanduser()


HOME = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or Path.home())

ROOT = _env_path("HPR_ROOT", str(Path.cwd()))
RUN_DIR = _env_path("HPR_RUN_DIR", str(ROOT / "pipeline_runs"))

# Optional: restrict worker working dirs to a prefix (e.g. "E:\\agents").
# Empty = no restriction (portable default). Set HPR_WORKDIR_PREFIX to enforce.
WORKDIR_PREFIX = os.environ.get("HPR_WORKDIR_PREFIX", "")

# Worker executables. Override per machine; defaults follow a typical install.
HERMES = _env_path("HPR_HERMES_BIN", str(HOME / "AppData/Local/hermes/hermes-agent/venv/Scripts/hermes.exe"))
CLAUDE = _env_path("HPR_CLAUDE_BIN", str(HOME / ".local/bin/claude.exe"))
AGY = _env_path("HPR_AGY_BIN", str(HOME / "AppData/Local/agy/bin/agy.exe"))
# Launch agy from the native user home so it does not generate bogus
# /Users/<name>/.gemini transcript paths when the real workspace is on another drive.
AGY_RUN_CWD = _env_path("HPR_AGY_RUN_CWD", str(HOME))
AGY_APP_DIR = _env_path("HPR_AGY_APP_DIR", str(HOME / ".gemini/antigravity-cli"))

QUOTA_PATTERNS = re.compile(
    r"quota|usage limit|rate limit|429|too many requests|overloaded|extra usage|"
    r"not supported|model .* unavailable|billing|insufficient|exhausted|"
    r"limit reached|try again later|auth failed|authentication|unauthorized|"
    r"logged out|missing api key|invalid api key|permission denied",
    re.IGNORECASE,
)

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".heic"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
DOC_EXTS = {".pdf", ".docx", ".pptx", ".xlsx"}


@dataclass
class Worker:
    key: str
    label: str
    role: str
    command_kind: str
    model: str = ""
    timeout: int = 300


WORKERS = {
    "grok": Worker("grok", "Grok / xAI OAuth", "collaborative-search-dispatch", "hermes", "grok-4.3", 240),
    "codex": Worker("codex", "OpenAI Codex", "review-second-opinion", "hermes", "gpt-5.5", 300),
    "gemini": Worker("gemini", "Gemini Pro / API", "multimodal-long-context", "hermes", "gemini-2.5-pro", 300),
    "gemini_flash": Worker("gemini_flash", "Gemini Flash / API", "fast-multimodal-summary", "hermes", "gemini-2.5-flash", 240),
    "deepseek": Worker("deepseek", "DeepSeek API", "low-cost", "hermes", "deepseek-chat", 300),
    "claude": Worker("claude", "Claude Code CLI", "complex-coding", "claude", "sonnet", 900),
    "claude_opus": Worker("claude_opus", "Claude Opus (audit)", "final-fallback-audit", "claude", "opus", 900),
    "antigravity_flash_high": Worker("antigravity_flash_high", "Antigravity Gemini Flash (High)", "search-dispatch", "agy", "Gemini 3.5 Flash (High)", 900),
    "antigravity_opus": Worker("antigravity_opus", "Antigravity Claude Opus (Thinking)", "gpt-substitute", "agy", "Claude Opus 4.6 (Thinking)", 900),
}

PROVIDER_BY_WORKER = {
    "grok": "xai-oauth",
    "codex": "openai-codex",
    "gemini": "gemini",
    "gemini_flash": "gemini",
    "deepseek": "deepseek",
}

FALLBACKS_BY_KIND = {
    "general": ["grok", "deepseek", "gemini_flash", "codex", "antigravity_opus", "claude"],
    "coding": ["codex", "antigravity_opus", "deepseek", "gemini", "grok"],
    "review": ["antigravity_opus", "deepseek", "gemini", "grok", "claude"],
    "multimodal": ["gemini", "grok", "codex", "antigravity_opus", "claude"],
}


@dataclass
class CommandResult:
    worker: str
    ok: bool
    quota_or_model_error: bool
    exit_code: Optional[int]
    stdout: str
    stderr: str
    elapsed_seconds: float
    command_preview: str


def now_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def safe_workdir(value: str | None) -> Path:
    p = Path(value) if value else (ROOT / "projects")
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    if WORKDIR_PREFIX and not str(p).replace("/", "\\").startswith(WORKDIR_PREFIX.replace("/", "\\")):
        raise SystemExit(f"workdir {p} is outside HPR_WORKDIR_PREFIX={WORKDIR_PREFIX}")
    return p


def command_preview(args: List[str]) -> str:
    out = []
    for a in args:
        if len(a) > 120:
            out.append(a[:117] + "...")
        elif re.search(r"\s", a):
            out.append(repr(a))
        else:
            out.append(a)
    return " ".join(out)


def run_subprocess(args: List[str], cwd: Path, timeout: int) -> CommandResult:
    start = datetime.now()
    try:
        cp = subprocess.run(
            args, cwd=str(cwd), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        stdout = (cp.stdout or "").strip()
        stderr = (cp.stderr or "").strip()
        elapsed = (datetime.now() - start).total_seconds()
        combined = stdout + "\n" + stderr
        errorish = (
            cp.returncode != 0
            or "API call failed" in combined
            or "logged out" in combined.lower()
            or "missing api key" in combined.lower()
            or "invalid api key" in combined.lower()
            or "unauthorized" in combined.lower()
            or stdout.lstrip().startswith("Error:")
            or stderr.lstrip().startswith("Error:")
        )
        quota = bool(errorish and QUOTA_PATTERNS.search(combined))
        ok = cp.returncode == 0 and bool(stdout.strip()) and not errorish and not quota
        return CommandResult("", ok, quota, cp.returncode, stdout, stderr, elapsed, command_preview(args))
    except subprocess.TimeoutExpired as exc:
        elapsed = (datetime.now() - start).total_seconds()
        so = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        se = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
        return CommandResult("", False, True, None, so.strip(), (se + "\nTIMEOUT").strip(), elapsed, command_preview(args))
    except FileNotFoundError as exc:
        elapsed = (datetime.now() - start).total_seconds()
        return CommandResult("", False, True, None, "", str(exc), elapsed, command_preview(args))


def recover_agy_print_output(prompt: str) -> str:
    """Recover agy -p output from Antigravity transcript files when stdout is
    empty (a known Windows/git-bash quirk with agy print mode)."""
    brain = AGY_APP_DIR / "brain"
    if not brain.exists():
        return ""
    try:
        recent = sorted((p for p in brain.iterdir() if p.is_dir()),
                        key=lambda p: p.stat().st_mtime, reverse=True)[:5]
    except Exception:
        return ""
    hint = prompt[:80]
    for cid in (p.name for p in recent):
        log_dir = brain / cid / ".system_generated" / "logs"
        for name in ("transcript.jsonl", "transcript_full.jsonl"):
            path = log_dir / name
            if not path.exists():
                continue
            try:
                rows = [json.loads(line) for line in
                        path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
            except Exception:
                continue
            if hint and not any(hint in str(r.get("content", "")) for r in rows if r.get("type") == "USER_INPUT"):
                continue
            outs = [str(r.get("content", "")).strip() for r in rows
                    if r.get("source") == "MODEL" and r.get("type") == "PLANNER_RESPONSE" and str(r.get("content", "")).strip()]
            if outs:
                return outs[-1]
    return ""


def agy_model_resolution_failure(model: str, since_ts: float) -> str:
    """agy may exit 0 and answer with its default model even when --model was
    not recognized. Detect that so we don't falsely report the requested model."""
    if not model:
        return ""
    log_dir = AGY_APP_DIR / "log"
    if not log_dir.exists():
        return ""
    try:
        logs = sorted(log_dir.glob("cli-*.log"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]
    except Exception:
        return ""
    matches: List[str] = []
    for path in logs:
        try:
            if path.stat().st_mtime + 2 < since_ts:
                continue
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if "Failed to resolve model flag" in line and model in line:
                    matches.append(line.strip())
        except Exception:
            continue
    return "\n".join(matches[-5:])


def run_worker(worker_key: str, prompt: str, cwd: Path) -> CommandResult:
    w = WORKERS[worker_key]
    if w.command_kind == "hermes":
        provider = PROVIDER_BY_WORKER[worker_key]
        args = [str(HERMES), "chat", "--source", "tool", "-Q",
                "--provider", provider, "-m", w.model, "--max-turns", "1", "-q", prompt]
    elif w.command_kind == "claude":
        args = [str(CLAUDE), "-p", prompt, "--model", w.model, "--max-turns", "5"]
    elif w.command_kind == "agy":
        # Launch agy from the native user home; add the real workspace via --add-dir.
        args = [str(AGY), "-p", prompt, "--add-dir", str(cwd), "--print-timeout", "20m"]
        if w.model:
            args.extend(["--model", w.model])
    else:
        raise ValueError(w.command_kind)
    command_cwd = AGY_RUN_CWD if w.command_kind == "agy" else cwd
    started_ts = datetime.now().timestamp()
    result = run_subprocess(args, command_cwd, w.timeout)
    result.worker = worker_key
    agy_model_error = ""
    if w.command_kind == "agy" and w.model:
        agy_model_error = agy_model_resolution_failure(w.model, started_ts)
    if w.command_kind == "agy" and not result.stdout.strip() and result.exit_code == 0:
        recovered = recover_agy_print_output(prompt)
        if recovered:
            result.stdout = recovered
            result.ok = True
            result.quota_or_model_error = False
    if agy_model_error:
        result.ok = False
        result.quota_or_model_error = True
        result.stderr = (result.stderr + "\n" if result.stderr else "") + agy_model_error
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Reference executor: run a task through real worker CLIs.")
    parser.add_argument("--task", required=True)
    parser.add_argument("--worker", required=True, choices=list(WORKERS.keys()),
                        help="Which worker to run (the planner would choose this).")
    parser.add_argument("--workdir", default=str(ROOT / "projects"))
    args = parser.parse_args()

    cwd = safe_workdir(args.workdir)
    res = run_worker(args.worker, args.task, cwd)
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RUN_DIR / f"run_{now_id()}.json"
    out_path.write_text(json.dumps(asdict(res), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"worker={res.worker} ok={res.ok} exit={res.exit_code}")
    print(res.stdout[:4000] if res.stdout else f"(no stdout) stderr={res.stderr[:1000]}")
    print(f"record: {out_path}")
    return 0 if res.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
