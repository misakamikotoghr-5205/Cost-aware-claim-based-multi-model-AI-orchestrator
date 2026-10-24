"""Offline package boundary, rollback and reproducible artifact tests."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import sys
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import extension_packages as ep
from scripts import prepare_native_client as prep


def source(tmp_path, package_id=ep.CORE_ID, version="0.1.0", text="value = 'initial'\n"):
    root = tmp_path / (package_id + "-" + version)
    root.mkdir(exist_ok=True)
    (root / "plugin.yaml").write_text(f"name: {package_id}\nversion: {version}\n")
    (root / "__init__.py").write_text(text)
    return root


def package(tmp_path, package_id=ep.CORE_ID, version="0.1.0", **kwargs):
    root = source(tmp_path, package_id, version)
    archive = tmp_path / f"{package_id}-{version}.zip"
    ep.build_package(root, archive, package_id=package_id, version=version, **kwargs)
    return archive


def rewrite(archive, change):
    with zipfile.ZipFile(archive) as bundle:
        members = {info.filename: (info, bundle.read(info)) for info in bundle.infolist()}
    change(members)
    with zipfile.ZipFile(archive, "w") as bundle:
        for name, (info, data) in members.items():
            bundle.writestr(info, data)


def test_install_is_data_only_and_uninstall_preserves_native_state(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".env").write_text("MODEL_KEY=untouched\n")
    (home / "config.yaml").write_text("model: native-model\nplugins:\n  enabled: []\n")
    manager = ep.ExtensionManager(home)
    archive = package(tmp_path)
    before = {p.name: p.read_bytes() for p in home.iterdir()}
    result = manager.install(archive)
    assert result["id"] == ep.CORE_ID
    assert (home / "plugins/orchestrator-core/__init__.py").read_text() == "value = 'initial'\n"
    assert ep.inventory(home)[0]["installed"] is True
    assert manager.install(archive)["replayed"] is True
    assert manager.uninstall(ep.CORE_ID)["removed"] is True
    assert manager.uninstall(ep.CORE_ID)["removed"] is False
    assert {key: (home / key).read_bytes() for key in before} == before


def test_payload_is_never_imported_or_executed(tmp_path):
    root = source(tmp_path, text="raise RuntimeError('must never execute during install')\n")
    archive = tmp_path / "core.zip"
    ep.build_package(root, archive, package_id=ep.CORE_ID)
    assert ep.ExtensionManager(tmp_path / "home").install(archive)["id"] == ep.CORE_ID


def test_core_dependency_and_uninstall_order(tmp_path):
    manager = ep.ExtensionManager(tmp_path / "home")
    extra = package(tmp_path, "local-judge")
    with pytest.raises(ep.ExtensionError, match="core"):
        manager.install(extra)
    manager.install(package(tmp_path))
    manager.install(extra)
    assert set(manager.installed()) == {ep.CORE_ID, "local-judge"}
    with pytest.raises(ep.ExtensionError, match="optional"):
        manager.uninstall(ep.CORE_ID)
    manager.uninstall("local-judge")
    assert manager.uninstall(ep.CORE_ID)["removed"]


def test_missing_physical_core_cannot_be_replaced_by_registry_flag(tmp_path):
    manager = ep.ExtensionManager(tmp_path / "home")
    manager.install(package(tmp_path))
    (manager.plugins / ep.CORE_ID / "__init__.py").unlink()
    assert ep.inventory(manager.home)[0]["installed"] is False
    with pytest.raises(ep.ExtensionError, match="core"):
        manager.install(package(tmp_path, "free-models"))


def test_exact_compatibility_and_version_checks(tmp_path):
    manager = ep.ExtensionManager(tmp_path / "home")
    with pytest.raises(ep.ExtensionError, match="revision"):
        manager.install(package(tmp_path, hermes_commit="a" * 40))
    core = package(tmp_path)
    manager.install(core)
    with pytest.raises(ep.ExtensionError, match="core version"):
        manager.install(package(tmp_path, "local-judge", core_version="0.2.0"))
    manager.install(package(tmp_path, "local-judge"))
    with pytest.raises(ep.ExtensionError, match="incompatible"):
        manager.install(package(tmp_path, version="0.2.0"))


def test_atomic_upgrade_and_explicit_downgrade(tmp_path):
    manager = ep.ExtensionManager(tmp_path / "home")
    old = package(tmp_path)
    manager.install(old)
    newer = package(tmp_path, version="0.2.0")
    assert manager.install(newer)["version"] == "0.2.0"
    with pytest.raises(ep.ExtensionError, match="downgrade"):
        manager.install(old)
    assert manager.install(old, replace=True)["version"] == "0.1.0"
    assert not list(manager.plugins.glob(".*-*"))


def test_registry_write_failure_rolls_back_upgrade_and_same_version_repair(tmp_path, monkeypatch):
    manager = ep.ExtensionManager(tmp_path / "home")
    core = package(tmp_path)
    manager.install(core)
    installed = manager.plugins / ep.CORE_ID / "__init__.py"
    installed.write_text("damaged but preserved on rollback")
    original = ep._atomic_json
    def fail_registry(path, data):
        if path.name == "installed.json":
            raise OSError("disk full")
        return original(path, data)
    monkeypatch.setattr(ep, "_atomic_json", fail_registry)
    with pytest.raises(OSError):
        manager.install(core, replace=True)
    assert installed.read_text() == "damaged but preserved on rollback"
    assert manager.installed()[ep.CORE_ID]["version"] == "0.1.0"
    assert not list(manager.plugins.glob(".*-*"))


def test_uninstall_failure_restores_package(tmp_path, monkeypatch):
    manager = ep.ExtensionManager(tmp_path / "home")
    manager.install(package(tmp_path))
    original = ep._atomic_json
    def fail_registry(path, data):
        if path.name == "installed.json":
            raise OSError("disk full")
        return original(path, data)
    monkeypatch.setattr(ep, "_atomic_json", fail_registry)
    with pytest.raises(OSError):
        manager.uninstall(ep.CORE_ID)
    assert (manager.plugins / ep.CORE_ID / "plugin.yaml").is_file()


def test_crashed_install_recovers_prior_directory(tmp_path):
    manager = ep.ExtensionManager(tmp_path / "home")
    manager.install(package(tmp_path))
    nonce = "b" * 32
    target = manager.plugins / ep.CORE_ID
    backup = manager.plugins / (".backup-" + nonce)
    target.rename(backup)
    target.mkdir()
    (target / "partial").write_text("incomplete")
    ep._atomic_json(manager.state / "transaction.json", {
        "id": ep.CORE_ID, "nonce": nonce, "existed": True,
        "after": {"version": "0.9.0", "installation_id": nonce}})
    manager.installed()
    assert (target / "plugin.yaml").exists() and not (target / "partial").exists()
    assert not backup.exists()


@pytest.mark.parametrize("name", ["../escape", "/absolute", "C:/escape", "a\\escape", "a//b", "a/../b", "a/./b", "NUL.txt", "foo:stream", ".env", "nested/credentials.json", "key.pem", "folder./file"])
def test_unsafe_archive_names_rejected(tmp_path, name):
    archive = package(tmp_path)
    def add(members):
        info = zipfile.ZipInfo(name)
        members[name] = (info, b"untrusted")
    rewrite(archive, add)
    with pytest.raises(ep.ExtensionError):
        ep.inspect_package(archive)


def test_symlinks_and_duplicate_zip_entries_rejected(tmp_path):
    archive = package(tmp_path)
    def symlink(members):
        info = zipfile.ZipInfo("link")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        members["link"] = (info, b"../../escape")
    rewrite(archive, symlink)
    with pytest.raises(ep.ExtensionError):
        ep.inspect_package(archive)
    archive = package(tmp_path)
    with pytest.warns(UserWarning, match="Duplicate name"):
        with zipfile.ZipFile(archive, "a") as bundle:
            bundle.writestr("__init__.py", "duplicate")
    with pytest.raises(ep.ExtensionError):
        ep.inspect_package(archive)


def test_hash_mismatch_undeclared_files_and_identity_mismatch(tmp_path):
    archive = package(tmp_path)
    rewrite(archive, lambda members: members.update({"__init__.py": (members["__init__.py"][0], b"tampered")}))
    with pytest.raises(ep.ExtensionError, match="hash"):
        ep.inspect_package(archive)
    root = source(tmp_path, "local-judge")
    with pytest.raises(ep.ExtensionError, match="identity"):
        ep.build_package(root, tmp_path / "bad.zip", package_id="free-models")


def test_install_rejects_symlinked_plugins_and_unmanaged_existing_plugin(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (home / "plugins").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ep.ExtensionError, match="symlink"):
        ep.ExtensionManager(home).install(package(tmp_path))
    (home / "plugins").unlink()
    (home / "plugins/orchestrator-core").mkdir(parents=True)
    with pytest.raises(ep.ExtensionError, match="unmanaged"):
        ep.ExtensionManager(home).install(package(tmp_path))
    assert not list(outside.iterdir())


def test_reproducible_archives_and_core_only_default(tmp_path):
    root = source(tmp_path)
    a, b = tmp_path / "a.zip", tmp_path / "b.zip"
    ep.build_package(root, a, package_id=ep.CORE_ID)
    ep.build_package(root, b, package_id=ep.CORE_ID)
    assert a.read_bytes() == b.read_bytes()
    from scripts.build_extensions import CORE_MODULES
    assert "pointer_intents.py" not in CORE_MODULES
    assert "desktop_service.py" not in CORE_MODULES


def test_prepare_checkout_refuses_wrong_pin_without_mutating_source(tmp_path, monkeypatch):
    checkout = tmp_path / "native"
    checkout.mkdir()
    calls = []
    def fake_run(argv, **kwargs):
        calls.append(argv)
        return "b" * 40
    monkeypatch.setattr(prep, "run", fake_run)
    with pytest.raises(ep.ExtensionError, match="HEAD"):
        prep.prepare_checkout(spec={"commit": ep.HERMES_COMMIT, "repository": "unused"}, cache_dir=tmp_path, upstream=checkout)
    assert calls == [["git", "rev-parse", "HEAD"]]
    assert not list(checkout.iterdir())


@pytest.mark.parametrize("autocrlf", ["false", "true"])
def test_prepare_applies_new_file_patch_idempotently_and_detects_tampering(tmp_path, autocrlf):
    import subprocess
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    def git(*args):
        return subprocess.run(["git", *args], cwd=checkout, check=True, capture_output=True, text=True).stdout.strip()
    git("init", "-q")
    git("config", "core.autocrlf", autocrlf)
    # Git patch context is byte-exact. Upstream enforces LF for native source;
    # Windows write_text() would turn these test patch bytes into CRLF.
    (checkout / "base.txt").write_bytes(b"base\n")
    git("add", "base.txt")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "base")
    commit = git("rev-parse", "HEAD")
    patches = tmp_path / "patches"
    patches.mkdir()
    (patches / "0001.patch").write_bytes(b"diff --git a/new.txt b/new.txt\nnew file mode 100644\n--- /dev/null\n+++ b/new.txt\n@@ -0,0 +1 @@\n+patched\n")
    spec = {"commit": commit, "repository": "unused"}
    assert prep.prepare_checkout(spec=spec, cache_dir=tmp_path, upstream=checkout, patch_dir=patches) == checkout
    assert (checkout / "new.txt").read_text() == "patched\n"
    assert prep.prepare_checkout(spec=spec, cache_dir=tmp_path, upstream=checkout, patch_dir=patches) == checkout
    # New patch files are untracked in Git until commit: the prep fingerprint
    # must nevertheless include their complete content.
    (checkout / "new.txt").write_text("tampered\n")
    with pytest.raises(ep.ExtensionError, match="changed"):
        prep.prepare_checkout(spec=spec, cache_dir=tmp_path, upstream=checkout, patch_dir=patches)


@pytest.mark.parametrize("autocrlf", ["false", "true"])
def test_prepare_accepts_only_exact_already_applied_patch(tmp_path, autocrlf):
    import subprocess
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    def git(*args):
        return subprocess.run(["git", *args], cwd=checkout, check=True, capture_output=True, text=True).stdout.strip()
    git("init", "-q")
    git("config", "core.autocrlf", autocrlf)
    # Git patch context is byte-exact. Upstream enforces LF for native source;
    # Windows write_text() would turn these test patch bytes into CRLF.
    (checkout / "base.txt").write_bytes(b"base\n")
    git("add", "base.txt")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "base")
    commit = git("rev-parse", "HEAD")
    patches = tmp_path / "patches"
    patches.mkdir()
    (patches / "0001.patch").write_bytes(b"diff --git a/base.txt b/base.txt\n--- a/base.txt\n+++ b/base.txt\n@@ -1 +1 @@\n-base\n+patched\n")
    (checkout / "base.txt").write_bytes(b"patched\n")
    prep.prepare_checkout(spec={"commit": commit, "repository": "unused"}, cache_dir=tmp_path, upstream=checkout, patch_dir=patches)
    assert (checkout / "base.txt").read_text() == "patched\n"


def test_inventory_never_follows_nested_payload_symlink(tmp_path):
    root = source(tmp_path)
    (root / "nested").mkdir()
    (root / "nested/file.txt").write_text("original")
    archive = tmp_path / "core.zip"
    ep.build_package(root, archive, package_id=ep.CORE_ID)
    manager = ep.ExtensionManager(tmp_path / "home")
    manager.install(archive)
    folder = manager.plugins / ep.CORE_ID / "nested"
    (folder / "file.txt").unlink()
    folder.rmdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "file.txt").write_text("original")
    folder.symlink_to(outside, target_is_directory=True)
    assert ep.inventory(manager.home)[0]["installed"] is False


def test_runtime_setup_uses_official_windows_and_posix_entrypoints(tmp_path, monkeypatch):
    commands = {'pwsh': '/tools/pwsh', 'powershell': '/tools/powershell', 'bash': '/bin/bash'}
    monkeypatch.setattr(prep.shutil, 'which', commands.get)
    assert prep.runtime_setup_command(tmp_path, windows=False) == ['/bin/bash', str(tmp_path / 'setup-hermes.sh'), '--runtime-only']
    assert prep.runtime_setup_command(tmp_path, windows=True) == [
        '/tools/pwsh', '-NoLogo', '-NoProfile', '-NonInteractive', '-File', str(tmp_path / 'setup-hermes.ps1'), '-RuntimeOnly']


def test_runtime_environment_isolates_home_and_store_and_preserves_network(tmp_path, monkeypatch):
    monkeypatch.setenv('PYTHONPATH', '/unrelated/source')
    monkeypatch.setenv('PYTHONHOME', '/unrelated/python')
    monkeypatch.setenv('VIRTUAL_ENV', '/unrelated/venv')
    monkeypatch.setenv('HTTPS_PROXY', 'http://test-proxy.invalid:8080')
    env = prep.runtime_environment(tmp_path / 'chosen-home')
    assert env['HERMES_HOME'] == str(tmp_path / 'chosen-home')
    assert env['HERMES_RUNTIME_DIR'] == str(tmp_path / 'chosen-home/tools')
    assert all(name not in env for name in ['PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV'])
    assert env['HTTPS_PROXY'] == 'http://test-proxy.invalid:8080'


def test_runtime_bootstrap_is_default_and_skip_still_probes_actual_resolver(tmp_path, monkeypatch):
    monkeypatch.setattr(prep.shutil, 'which', lambda name: '/tools/' + name)
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return json.dumps({'ready': True, 'kind': 'command', 'root': str(tmp_path), 'launcher': 'native-hermes'})
    monkeypatch.setattr(prep, 'run', run)
    assert prep.prepare_runtime(tmp_path, tmp_path / 'home')['ready'] is True
    assert calls[0][0] == prep.runtime_setup_command(tmp_path)
    assert calls[0][1]['env']['HERMES_HOME'] == str(tmp_path / 'home')
    assert calls[1][0][1].endswith('probe_native_backend.mjs')
    calls.clear()
    prep.prepare_runtime(tmp_path, tmp_path / 'home', skip_runtime=True)
    assert len(calls) == 1 and calls[0][0][1].endswith('probe_native_backend.mjs')
    monkeypatch.setattr(prep, 'run', lambda *a, **k: json.dumps({'ready': False, 'root': str(tmp_path)}))
    with pytest.raises(ep.ExtensionError, match='readiness'):
        prep.prepare_runtime(tmp_path, tmp_path / 'home', skip_runtime=True)
