#!/usr/bin/env python3
"""Prepare the pinned full Hermes native client, plus only orchestrator-core.

No model logins or provider secrets are read/written. Optional ZIPs are installed
separately with src.extension_packages and enabled through native Capabilities.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.extension_packages import ExtensionError, ExtensionManager, _atomic_json
from scripts.build_extensions import build_one


def run(argv, *, cwd=None, timeout=600, env=None):
    return subprocess.run([str(value) for value in argv], cwd=cwd, check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, timeout=timeout, env=env).stdout.strip()


def _diff_digest(checkout, patch_files=()):
    digest = hashlib.sha256(subprocess.run(["git", "diff", "--binary", "HEAD"], cwd=checkout,
                                         check=True, stdout=subprocess.PIPE, timeout=30).stdout)
    for name in sorted(patch_files):
        path = checkout / name
        digest.update(name.encode())
        if path.is_symlink():
            raise ExtensionError("patched native files must not be symlinks")
        digest.update(path.read_bytes() if path.is_file() else b"<deleted>")
    return digest.hexdigest()


def _expected_patch_files(checkout, patches):
    """Build the exact patch tree in a private Git index, never the user's index."""
    with tempfile.TemporaryDirectory(prefix="hermes-patch-index-") as temporary:
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = str(Path(temporary) / "index")
        run(["git", "read-tree", "HEAD"], cwd=checkout, env=env)
        for patch in patches:
            run(["git", "apply", "--cached", str(patch.resolve())], cwd=checkout, env=env)
        names = run(["git", "diff", "--name-only", "--cached", "HEAD"], cwd=checkout, env=env).splitlines()
        result = {}
        for name in names:
            path = Path(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name:
                raise ExtensionError("unsafe native patch path")
            staged = run(["git", "ls-files", "--stage", "--", name], cwd=checkout, env=env)
            result[name] = staged.split()[1] if staged else None
        return result


def _matches_patch_tree(checkout, expected):
    changed = set(run(["git", "diff", "--name-only", "HEAD"], cwd=checkout).splitlines())
    if not changed <= expected.keys():
        return False
    for name, blob in expected.items():
        path = checkout / name
        if path.is_symlink():
            return False
        actual = run(["git", "hash-object", "--no-filters", "--", name], cwd=checkout) if path.is_file() else None
        if actual != blob:
            return False
    return True


def cache_checkout(spec, cache_dir, patch_dir=None):
    """A downstream patch upgrade gets its own source, preserving old checkouts."""
    digest = hashlib.sha256(spec["commit"].encode())
    patches = sorted(Path(patch_dir).glob("*.patch")) if patch_dir and Path(patch_dir).is_dir() else []
    for patch in patches:
        if patch.is_symlink():
            raise ExtensionError("native patches must be regular files")
        digest.update(patch.name.encode())
        digest.update(hashlib.sha256(patch.read_bytes()).digest())
    return Path(cache_dir).expanduser().resolve() / ("misaka-" + spec["commit"][:12] + "-" + digest.hexdigest()[:12])


def prepare_checkout(*, spec, cache_dir, upstream=None, patch_dir=None):
    commit = spec["commit"]
    checkout = Path(upstream).expanduser().resolve() if upstream else cache_checkout(spec, cache_dir, patch_dir)
    if not checkout.exists():
        checkout.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--filter=blob:none", "--no-checkout", "--no-tags", "--depth", "1",
             spec["repository"], checkout])
        run(["git", "fetch", "--depth", "1", "origin", commit], cwd=checkout)
        run(["git", "checkout", "--detach", commit], cwd=checkout)
    if run(["git", "rev-parse", "HEAD"], cwd=checkout) != commit:
        raise ExtensionError("native checkout HEAD differs from the locked upstream commit")
    marker = checkout / ".orchestrator-native-state.json"
    if marker.is_symlink():
        raise ExtensionError("unsafe preparation marker")
    patches = sorted(Path(patch_dir).glob("*.patch")) if patch_dir and Path(patch_dir).is_dir() else []
    if any(path.is_symlink() for path in patches):
        raise ExtensionError("native patches must be regular files")
    expected = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in patches}
    patch_files = _expected_patch_files(checkout, patches)
    if marker.exists():
        previous = json.loads(marker.read_text())
        if previous.get("commit") != commit or previous.get("patches") != expected or previous.get("tracked_diff_sha256") != _diff_digest(checkout, patch_files):
            raise ExtensionError("prepared native source or patch set changed; use a fresh cache checkout")
        return checkout
    if patches and _matches_patch_tree(checkout, patch_files):
        _atomic_json(marker, {"commit": commit, "patches": expected,
                              "tracked_diff_sha256": _diff_digest(checkout, patch_files)})
        return checkout
    if run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=checkout):
        raise ExtensionError("native checkout has unrelated tracked changes; use a clean pinned checkout")
    applied = []
    try:
        for patch in patches:
            run(["git", "apply", "--check", str(patch.resolve())], cwd=checkout)
            run(["git", "apply", str(patch.resolve())], cwd=checkout)
            applied.append(patch)
        _atomic_json(marker, {"commit": commit, "patches": expected,
                              "tracked_diff_sha256": _diff_digest(checkout, patch_files)})
    except BaseException:
        for patch in reversed(applied):
            run(["git", "apply", "--reverse", str(patch.resolve())], cwd=checkout)
        raise
    return checkout


def runtime_environment(hermes_home):
    env = os.environ.copy()
    env["HERMES_HOME"] = str(Path(hermes_home).expanduser().resolve())
    # Windows setup otherwise defaults the tool store to the real OS home.
    env["HERMES_RUNTIME_DIR"] = str(Path(env["HERMES_HOME"]) / "tools")
    for variable in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        env.pop(variable, None)
    return env


def initialize_profile(hermes_home):
    """Fresh MISAKA profiles wait for the user's provider choice before inference."""
    directory = Path(hermes_home).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "config.yaml"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    payload = json.dumps({"nous": {"guest": False}, "onboarding": {"run": False}}, indent=2).encode() + b"\n"
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return True


def runtime_setup_command(checkout, *, windows=None):
    windows = os.name == "nt" if windows is None else windows
    if windows:
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if powershell is None:
            raise ExtensionError("PowerShell is required for the native Windows PM bootstrap")
        return [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-File",
                str(Path(checkout) / "setup-hermes.ps1"), "-RuntimeOnly"]
    bash = shutil.which("bash")
    if bash is None:
        raise ExtensionError("bash is required for the official native PM bootstrap")
    return [bash, str(Path(checkout) / "setup-hermes.sh"), "--runtime-only"]


def prepare_runtime(checkout, hermes_home, *, skip_runtime=False):
    env = runtime_environment(hermes_home)
    if not skip_runtime:
        # Official runtime-only avoids user PATH/shell edits, credentials and
        # onboarding while publishing the checkout's own durable PM launchers.
        run(runtime_setup_command(checkout), cwd=checkout, env=env, timeout=2400)
    node = shutil.which("node")
    if node is None:
        raise ExtensionError("Node.js is required for the actual native backend readiness probe")
    result = json.loads(run([node, str(ROOT / "scripts/probe_native_backend.mjs"),
                             "--upstream", str(checkout), "--hermes-home", env["HERMES_HOME"]],
                            cwd=checkout, env=env, timeout=180))
    if result.get("ready") is not True or Path(result.get("root", "")).resolve() != Path(checkout).resolve():
        raise ExtensionError("official native backend probe did not establish readiness")
    return result


def install_js_dependencies(checkout, *, skip_deps=False):
    npm = shutil.which("npm")
    if npm is None:
        raise ExtensionError("install a Node/npm version accepted by the pinned Hermes package.json")
    if not skip_deps:
        run([npm, "ci"], cwd=checkout, timeout=1200)
    return npm


def build_native(checkout, *, skip_deps=False):
    npm = install_js_dependencies(checkout, skip_deps=skip_deps)
    run([npm, "--workspace", "apps/desktop", "run", "build"], cwd=checkout, timeout=1200)


def enable_client_core(checkout, hermes_home, runtime):
    """Admit this client's core through native CLI; retain explicit user denials."""
    import yaml
    try:
        config = yaml.safe_load((Path(hermes_home) / "config.yaml").read_text(encoding="utf-8-sig")) or {}
        plugins = config.get("plugins")
        if plugins is None:
            plugins = {}
        if not isinstance(plugins, dict):
            raise ValueError("invalid plugin preferences")
        enabled, disabled = plugins.get("enabled", []), plugins.get("disabled", [])
        enabled = [] if enabled is None else enabled
        disabled = [] if disabled is None else disabled
        if not all(isinstance(values, list) and all(isinstance(item, str) for item in values)
                   for values in (enabled, disabled)):
            raise ValueError("invalid plugin preferences")
    except (OSError, ValueError, TypeError, AttributeError, yaml.YAMLError):
        raise ExtensionError("无法读取本机插件启用偏好，请先修复 config.yaml 后重试。") from None

    def matches(name):
        return name == "orchestrator-core" or name.endswith(("/orchestrator-core", ":orchestrator-core"))

    if any(matches(name) for name in disabled):
        return {"enabled": False, "changed": False, "reason": "explicitly-disabled"}
    if any(matches(name) for name in enabled):
        return {"enabled": True, "changed": False}
    launcher = str(Path(runtime["launcher"]).resolve())
    command = [launcher, "--profile", "default", "plugins", "enable", "orchestrator-core"]
    via_shell = os.name == "nt" and Path(launcher).suffix.lower() in {".cmd", ".bat"}
    if via_shell:
        if any(character in launcher for character in '"%&|<>^\r\n'):
            raise ExtensionError("Windows 原生启动器路径包含不支持的命令字符。")
        command = subprocess.list2cmdline(command)
    environment = runtime_environment(hermes_home)
    environment["HERMES_INSTALL_ROOT"] = str(Path(checkout).resolve())
    subprocess.run(command, cwd=checkout, env=environment, shell=via_shell,
                   check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                   encoding="utf-8", errors="replace", timeout=120)
    return {"enabled": True, "changed": True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=Path.home() / ".cache/misaka-agent")
    parser.add_argument("--upstream", type=Path, help="reuse an existing clean checkout of the exact pin")
    parser.add_argument("--hermes-home", type=Path, required=True, help="explicit native profile home; credentials are never copied")
    parser.add_argument("--packages-dir", type=Path, default=ROOT / "dist/extensions")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--skip-deps", action="store_true", help="use dependencies already installed at the upstream root")
    parser.add_argument("--skip-runtime", action="store_true", help="skip PM bootstrap only for a preprepared source; still requires the real native launcher probe")
    parser.add_argument("--launch", action="store_true", help="launch the complete native app after preparation")
    args = parser.parse_args(argv)
    try:
        spec = json.loads((ROOT / "integrations/hermes-client/upstream.json").read_text())
        print("MISAKA: preparing the pinned native source and patches", file=sys.stderr, flush=True)
        checkout = prepare_checkout(spec=spec, cache_dir=args.cache_dir, upstream=args.upstream,
                                    patch_dir=ROOT / "integrations/hermes-client/patches")
        # SDK compilation and the real resolver probe both consume locked JS dependencies.
        print("MISAKA: preparing locked desktop dependencies", file=sys.stderr, flush=True)
        install_js_dependencies(checkout, skip_deps=args.skip_deps)
        initialized = initialize_profile(args.hermes_home)
        print("MISAKA: preparing the official runtime and probing its launcher", file=sys.stderr, flush=True)
        runtime = prepare_runtime(checkout, args.hermes_home, skip_runtime=args.skip_runtime)
        if args.build:
            print("MISAKA: building the native desktop", file=sys.stderr, flush=True)
            build_native(checkout, skip_deps=True)
        print("MISAKA: packaging and installing core; optional packages remain separate", file=sys.stderr, flush=True)
        core = build_one(spec["core_id"], output_dir=args.packages_dir, upstream=checkout)
        installed = ExtensionManager(args.hermes_home, hermes_commit=spec["commit"]).install(core["archive"], replace=True)
        print("MISAKA: enabling the client core while preserving explicit disable choices", file=sys.stderr, flush=True)
        activation = enable_client_core(checkout, args.hermes_home, runtime)
        print(json.dumps({"checkout": str(checkout), "core": installed,
                          "core_activation": activation,
                          "new_profile_defaults_initialized": initialized,
                          "optional_packages_installed": [], "native_built": args.build, "native_runtime": runtime,
                          "next": "启动后可从侧栏打开额度总览；可选扩展按需安装。明确关闭核心的设置会保留。"}, indent=2), flush=True)
        if args.launch:
            npm = shutil.which("npm")
            if npm is None:
                raise ExtensionError("npm is not installed")
            env = runtime_environment(args.hermes_home)
            env["HERMES_DESKTOP_HERMES_ROOT"] = str(checkout)
            env["HERMES_DESKTOP_HERMES"] = runtime["launcher"]
            subprocess.run([npm, "--workspace", "apps/desktop", "run", "start"], cwd=checkout, env=env, check=True)
        return 0
    except (ExtensionError, OSError, subprocess.SubprocessError, ValueError) as exc:
        if isinstance(exc, subprocess.CalledProcessError):
            # Build logs are useful, but do not echo arbitrary command env/argv.
            print((exc.stderr or "")[-8000:], file=sys.stderr)
            parser.exit(1, "native preparation command failed\n")
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
