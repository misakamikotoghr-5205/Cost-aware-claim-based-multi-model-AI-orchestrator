#!/usr/bin/env python3
"""Build separate, self-contained plugin ZIPs; never install or enable extras."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.extension_packages import CORE_ID, ExtensionError, build_package


CORE_MODULES = (
    "__init__.py", "registry.py", "scoring_engine.py", "task_card.py",
    "quota_manager.py", "client_router.py", "discovery.py", "executors.py",
    "runtime_cli.py", "quota_ledger.py", "quota_sources.py", "cloud_queue.py",
    "cloud_server.py", "rerouter.py", "orchestrator.py", "claim_engine.py",
    "voting_engine.py", "extension_packages.py", "fleet_client.py", "node_registry.py",
)

IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".git", ".env", ".env.*", "node_modules", "extension.json")


def build_one(package_id, *, output_dir, upstream=None, root=ROOT):
    spec = json.loads((root / "integrations/hermes-client/upstream.json").read_text())
    known = [CORE_ID, *spec["optional_packages"]]
    if package_id not in known:
        raise ExtensionError("unsupported package ID")
    source = root / "integrations/hermes-client" / ("plugin" if package_id == CORE_ID else "extensions/" + package_id)
    if not source.is_dir():
        raise ExtensionError("package source is missing: " + str(source))
    if any(path.is_symlink() for path in source.rglob("*")):
        raise ExtensionError("plugin source must not contain symlinks")
    with tempfile.TemporaryDirectory(prefix="hermes-extension-build-") as temp:
        staged = Path(temp) / "plugin"
        shutil.copytree(source, staged, ignore=IGNORE, symlinks=True)
        if package_id == CORE_ID:
            (staged / "_orchestrator").mkdir()
            for module in CORE_MODULES:
                shutil.copy2(root / "src" / module, staged / "_orchestrator" / module)
            shutil.copytree(root / "configs", staged / "configs", ignore=IGNORE, symlinks=True)
            shutil.copy2(root / "src/extension_packages.py", staged / "package_manager.py")
        if package_id == "smart-pointer":
            (staged / "_impl").mkdir(exist_ok=True)
            shutil.copy2(root / "src/pointer_intents.py", staged / "_impl/pointer_intents.py")
        if not (staged / "LICENSE").exists():
            shutil.copy2(root / "LICENSE", staged / "LICENSE")
        entries = [staged / "desktop/src/plugin.tsx", staged / "desktop/plugin.tsx"]
        entry = next((path for path in entries if path.is_file()), None)
        if entry:
            if upstream is None:
                raise ExtensionError("--upstream is required to compile desktop SDK entries")
            subprocess.run(["node", str(root / "scripts/build_desktop_plugins.mjs"),
                            "--upstream", str(Path(upstream).resolve()), "--entry", str(entry),
                            "--outfile", str(staged / "desktop/plugin.js")], check=True, timeout=120)
            # Only the compiled entry is installed, not the build sources.
            if (staged / "desktop/src").exists():
                shutil.rmtree(staged / "desktop/src")
            (staged / "desktop/plugin.tsx").unlink(missing_ok=True)
        target = Path(output_dir) / f"{package_id}-{spec['core_version']}.zip"
        manifest = build_package(staged, target, package_id=package_id,
                                 version=spec["core_version"], core_version=spec["core_version"],
                                 hermes_commit=spec["commit"])
        return {"archive": str(target.resolve()), "id": package_id, "files": len(manifest["files"])}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ids", nargs="+", default=[CORE_ID], help="Explicit package IDs; 'all' builds separate ZIPs")
    parser.add_argument("--output", type=Path, default=ROOT / "dist/extensions")
    parser.add_argument("--upstream", type=Path)
    args = parser.parse_args(argv)
    spec = json.loads((ROOT / "integrations/hermes-client/upstream.json").read_text())
    ids = [CORE_ID, *spec["optional_packages"]] if args.ids == ["all"] else args.ids
    try:
        results = [build_one(package_id, output_dir=args.output, upstream=args.upstream) for package_id in ids]
        print(json.dumps(results, indent=2))
        return 0
    except (ExtensionError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
