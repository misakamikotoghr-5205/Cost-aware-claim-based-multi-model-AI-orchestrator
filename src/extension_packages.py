"""Hash-verified, data-only installation of optional Hermes plugin archives.

Install/uninstall never import package Python, invoke a process, access model
credentials, enable a plugin, or modify Hermes config. The native host's explicit
plugin-enable operation remains the point at which plugin code may execute.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import uuid
import zipfile

HERMES_COMMIT = "4dcef215cfe41bd2d02c2d1dad6e257a18101216"
CORE_ID = "orchestrator-core"
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_EXPANDED_BYTES = 64 * 1024 * 1024
MAX_FILES = 2000
_ID = re.compile(r"[a-z][a-z0-9-]{0,62}\Z")
_VERSION = re.compile(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\Z")
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")
_SECRET_NAMES = {".env", "auth.json", "credentials.json", "oauth.json", "tokens.json", "id_rsa", "id_ed25519", "kubeconfig"}


class ExtensionError(ValueError):
    pass


def _json(raw):
    def object_pairs(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ExtensionError("duplicate JSON key")
            obj[key] = value
        return obj
    try:
        return json.loads(raw, object_pairs_hook=object_pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(ExtensionError("non-finite JSON")))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ExtensionError("invalid package JSON") from exc


def _path(name: str) -> str:
    if not isinstance(name, str) or not name or len(name) > 240 or "\\" in name or ":" in name or "\x00" in name:
        raise ExtensionError("invalid package path")
    path = PurePosixPath(name)
    if path.is_absolute() or path.as_posix() != name or any(p in {"", ".", ".."} for p in path.parts):
        raise ExtensionError("package path escapes its root")
    for part in path.parts:
        lowered = part.lower()
        if (lowered in _SECRET_NAMES or lowered.startswith(".env.") or lowered.endswith((".pem", ".p12", ".key"))
                or part.endswith((".", " ")) or lowered.split(".")[0] in {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}):
            raise ExtensionError("credential or unsafe platform filename is forbidden")
    return name


def validate_manifest(manifest: dict) -> dict:
    if not isinstance(manifest, dict) or set(manifest) != {"schema_version", "id", "version", "requires", "files"}:
        raise ExtensionError("unsupported extension manifest fields")
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1:
        raise ExtensionError("unsupported extension schema")
    if not isinstance(manifest["id"], str) or not _ID.fullmatch(manifest["id"]):
        raise ExtensionError("invalid extension ID")
    if not isinstance(manifest["version"], str) or not _VERSION.fullmatch(manifest["version"]):
        raise ExtensionError("version must be a three-component release version")
    required = manifest["requires"]
    fields = {"hermes_commit"} if manifest["id"] == CORE_ID else {"hermes_commit", "core_version"}
    if not isinstance(required, dict) or set(required) != fields:
        raise ExtensionError("invalid compatibility declaration")
    if not isinstance(required["hermes_commit"], str) or not re.fullmatch(r"[a-f0-9]{40}", required["hermes_commit"]):
        raise ExtensionError("Hermes requires an exact commit pin")
    if manifest["id"] != CORE_ID and (not isinstance(required["core_version"], str) or not _VERSION.fullmatch(required["core_version"])):
        raise ExtensionError("extension requires an exact core version")
    files = manifest["files"]
    if not isinstance(files, dict) or not files or len(files) > MAX_FILES:
        raise ExtensionError("package must declare a bounded nonempty file set")
    names = set()
    for name, digest in files.items():
        _path(name)
        if name == "extension.json" or name.casefold() in names or not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
            raise ExtensionError("duplicate path or invalid file digest")
        names.add(name.casefold())
    if "plugin.yaml" not in files or "__init__.py" not in files:
        raise ExtensionError("a Hermes package requires plugin.yaml and __init__.py")
    return manifest


def inspect_package(archive: str | Path) -> dict:
    archive = Path(archive)
    if archive.is_symlink() or archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ExtensionError("archive must be a bounded regular file")
    try:
        with zipfile.ZipFile(archive) as bundle:
            infos = bundle.infolist()
            if len(infos) > MAX_FILES + 1 or not infos:
                raise ExtensionError("too many package members")
            members, total = {}, 0
            folded = set()
            for info in infos:
                name = _path(info.filename)
                mode = info.external_attr >> 16
                if (info.is_dir() or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in {0, stat.S_IFREG})
                        or info.flag_bits & 1 or name.casefold() in folded):
                    raise ExtensionError("encrypted, duplicate, directory or nonregular ZIP member")
                folded.add(name.casefold())
                total += info.file_size
                if total > MAX_EXPANDED_BYTES:
                    raise ExtensionError("expanded package exceeds limit")
                members[name] = info
            if "extension.json" not in members or members["extension.json"].file_size > 1024 * 1024:
                raise ExtensionError("missing or oversized extension manifest")
            manifest = validate_manifest(_json(bundle.read("extension.json")))
            if set(members) != set(manifest["files"]) | {"extension.json"}:
                raise ExtensionError("archive contains undeclared or missing files")
            for name, digest in manifest["files"].items():
                if hashlib.sha256(bundle.read(name)).hexdigest() != digest:
                    raise ExtensionError("package content hash mismatch: " + name)
            # Only read data. Never load plugin code during validation.
            import yaml
            try:
                native = yaml.safe_load(bundle.read("plugin.yaml"))
            except yaml.YAMLError as exc:
                raise ExtensionError("invalid native plugin manifest") from exc
            if not isinstance(native, dict) or native.get("name") != manifest["id"] or str(native.get("version")) != manifest["version"]:
                raise ExtensionError("Hermes plugin identity/version differs from extension manifest")
            return manifest
    except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
        raise ExtensionError("invalid ZIP package") from exc


def _digest(manifest):
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _atomic_json(path: Path, value):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _safe_directory(path: Path):
    if path.is_symlink():
        raise ExtensionError("plugin/state directory must not be a symlink")
    path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise ExtensionError("expected a directory")


class ExtensionManager:
    def __init__(self, hermes_home: str | Path, *, hermes_commit: str = HERMES_COMMIT):
        if not re.fullmatch(r"[a-f0-9]{40}", hermes_commit):
            raise ExtensionError("invalid runtime commit")
        self.home = Path(hermes_home).expanduser().resolve()
        self.plugins = self.home / "plugins"
        self.state = self.home / "orchestrator-extensions"
        self.hermes_commit = hermes_commit

    @contextmanager
    def _lock(self):
        _safe_directory(self.home)
        _safe_directory(self.plugins)
        _safe_directory(self.state)
        path = self.state / "install.lock"
        if path.is_symlink():
            raise ExtensionError("unsafe install lock")
        with path.open("a+b") as stream:
            stream.write(b"\0")
            stream.flush()
            stream.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise ExtensionError("another package operation is active") from exc
            try:
                self._recover()
                yield
            finally:
                if os.name == "nt":
                    import msvcrt
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _registry(self):
        path = self.state / "installed.json"
        if path.is_symlink():
            raise ExtensionError("unsafe installed registry")
        registry = _json(path.read_bytes()) if path.exists() else {}
        if not isinstance(registry, dict):
            raise ExtensionError("invalid installed registry")
        for key, record in registry.items():
            if (not isinstance(key, str) or not _ID.fullmatch(key) or not isinstance(record, dict)
                    or not isinstance(record.get("version"), str) or not _VERSION.fullmatch(record["version"])
                    or not isinstance(record.get("manifest_sha256"), str) or not _DIGEST.fullmatch(record["manifest_sha256"])
                    or not isinstance(record.get("requires"), dict)):
                raise ExtensionError("invalid installed package record")
        return registry

    def _present(self, package_id, record):
        target = self.plugins / package_id
        path = target / "extension.json"
        if target.is_symlink() or path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
            return False
        try:
            manifest = validate_manifest(_json(path.read_bytes()))
            if manifest["id"] != package_id or _digest(manifest) != record["manifest_sha256"]:
                return False
            for name, digest in manifest["files"].items():
                part_path = target
                for part in PurePosixPath(name).parts:
                    part_path = part_path / part
                    if part_path.is_symlink():
                        return False
                if not part_path.is_file() or part_path.stat().st_size > MAX_EXPANDED_BYTES:
                    return False
                if hashlib.sha256(part_path.read_bytes()).hexdigest() != digest:
                    return False
        except (ExtensionError, OSError):
            return False
        return True

    def _recover(self):
        journal = self.state / "transaction.json"
        if journal.is_symlink():
            raise ExtensionError("unsafe transaction journal")
        if not journal.exists():
            return
        txn = _json(journal.read_bytes())
        if (not isinstance(txn, dict) or not _ID.fullmatch(txn.get("id", ""))
                or not re.fullmatch(r"[a-f0-9]{32}", txn.get("nonce", ""))):
            raise ExtensionError("invalid recovery journal")
        target = self.plugins / txn["id"]
        stage = self.plugins / (".stage-" + txn["nonce"])
        backup = self.plugins / (".backup-" + txn["nonce"])
        if any(p.is_symlink() for p in (target, stage, backup)):
            raise ExtensionError("unsafe recovery paths")
        committed = self._registry().get(txn["id"]) == txn["after"]
        if not committed:
            if backup.exists():
                if target.exists():
                    shutil.rmtree(target)
                os.replace(backup, target)
            elif not txn["existed"] and target.exists():
                shutil.rmtree(target)
        for path in (stage, backup):
            if path.exists():
                shutil.rmtree(path)
        journal.unlink()

    def installed(self) -> dict:
        with self._lock():
            return self._registry()

    def install(self, archive: str | Path, *, replace: bool = False) -> dict:
        manifest = inspect_package(archive)
        if manifest["requires"]["hermes_commit"] != self.hermes_commit:
            raise ExtensionError("package targets a different Hermes revision")
        with self._lock():
            registry = self._registry()
            package_id = manifest["id"]
            previous = registry.get(package_id)
            target = self.plugins / package_id
            if target.is_symlink() or (target.exists() and not target.is_dir()):
                raise ExtensionError("unsafe existing package path")
            if target.exists() and previous is None:
                raise ExtensionError("refusing to overwrite an unmanaged Hermes plugin")
            if package_id != CORE_ID:
                core = registry.get(CORE_ID)
                if not core or not self._present(CORE_ID, core) or core["version"] != manifest["requires"]["core_version"]:
                    raise ExtensionError("install the required orchestrator-core version first")
            else:
                for name, entry in registry.items():
                    if name != CORE_ID and entry["requires"]["core_version"] != manifest["version"]:
                        raise ExtensionError("installed extensions are incompatible with this core version")
            record = {"version": manifest["version"], "requires": manifest["requires"], "manifest_sha256": _digest(manifest)}
            if previous and all(previous.get(k) == v for k, v in record.items()) and target.exists():
                if self._present(package_id, previous):
                    return {"id": package_id, "version": manifest["version"], "replayed": True}
                if not replace:
                    raise ExtensionError("installed files changed; use explicit replace to repair")
            if previous and not replace:
                old_version = tuple(map(int, previous["version"].split(".")))
                version = tuple(map(int, manifest["version"].split(".")))
                if version <= old_version:
                    raise ExtensionError("same-version replacement or downgrade requires explicit replace")
            nonce = uuid.uuid4().hex
            record["installation_id"] = nonce
            stage, backup = self.plugins / (".stage-" + nonce), self.plugins / (".backup-" + nonce)
            stage.mkdir()
            try:
                with zipfile.ZipFile(archive) as bundle:
                    for name in ["extension.json", *manifest["files"]]:
                        dest = stage / name
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        with dest.open("xb") as stream:
                            stream.write(bundle.read(name))
                # Check again after staging: archive replacement between validation
                # and extraction must not install unchecked bytes.
                staged_manifest = _json((stage / "extension.json").read_bytes())
                if staged_manifest != manifest or any(hashlib.sha256((stage / name).read_bytes()).hexdigest() != digest
                                                      for name, digest in manifest["files"].items()):
                    raise ExtensionError("archive changed during staging")
                txn = {"id": package_id, "nonce": nonce, "existed": target.exists(), "after": record}
                _atomic_json(self.state / "transaction.json", txn)
                if target.exists():
                    os.replace(target, backup)
                os.replace(stage, target)
                registry[package_id] = record
                _atomic_json(self.state / "installed.json", registry)
            except BaseException:
                self._recover()
                if stage.exists():
                    shutil.rmtree(stage)
                raise
            self._recover()
            return {"id": package_id, "version": manifest["version"], "replayed": False}

    def uninstall(self, package_id: str) -> dict:
        if not isinstance(package_id, str) or not _ID.fullmatch(package_id):
            raise ExtensionError("invalid extension ID")
        with self._lock():
            registry = self._registry()
            if package_id not in registry:
                return {"id": package_id, "removed": False}
            if package_id == CORE_ID and set(registry) != {CORE_ID}:
                raise ExtensionError("remove optional extensions before removing core")
            target = self.plugins / package_id
            if target.is_symlink():
                raise ExtensionError("unsafe installed package path")
            nonce = uuid.uuid4().hex
            backup = self.plugins / (".backup-" + nonce)
            txn = {"id": package_id, "nonce": nonce, "existed": target.exists(), "after": None}
            _atomic_json(self.state / "transaction.json", txn)
            try:
                if target.exists():
                    os.replace(target, backup)
                del registry[package_id]
                _atomic_json(self.state / "installed.json", registry)
            except BaseException:
                self._recover()
                raise
            self._recover()
            return {"id": package_id, "removed": True}


def build_package(source: str | Path, output: str | Path, *, package_id: str,
                  version: str = "0.1.0", core_version: str = "0.1.0",
                  hermes_commit: str = HERMES_COMMIT) -> dict:
    """Build a reproducible archive from an already prepared plugin directory."""
    source, output = Path(source).resolve(strict=True), Path(output)
    files = {}
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise ExtensionError("package source contains a symlink")
        if path.is_dir():
            continue
        relative = path.relative_to(source).as_posix()
        if relative == "extension.json":
            continue
        _path(relative)
        files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    required = {"hermes_commit": hermes_commit}
    if package_id != CORE_ID:
        required["core_version"] = core_version
    manifest = validate_manifest({"schema_version": 1, "id": package_id, "version": version,
                                  "requires": required, "files": files})
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            payload = {name: (source / name).read_bytes() for name in files}
            payload["extension.json"] = json.dumps(manifest, sort_keys=True, indent=2).encode()
            for name, content in sorted(payload.items()):
                info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                bundle.writestr(info, content)
        inspect_package(temporary)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return manifest


def install_archive(path, *, hermes_home):
    return ExtensionManager(hermes_home).install(path)


def remove_extension(extension_id, *, hermes_home):
    return ExtensionManager(hermes_home).uninstall(extension_id)


def inventory(hermes_home):
    manager = ExtensionManager(hermes_home)
    with manager._lock():
        return [{"id": key, **value, "installed": manager._present(key, value)}
                for key, value in sorted(manager._registry().items())]


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hermes-home", required=True)
    sub = parser.add_subparsers(dest="action", required=True)
    install = sub.add_parser("install")
    install.add_argument("archive", type=Path)
    install.add_argument("--replace", action="store_true")
    remove = sub.add_parser("uninstall")
    remove.add_argument("id")
    sub.add_parser("list")
    args = parser.parse_args(argv)
    try:
        manager = ExtensionManager(args.hermes_home)
        result = (manager.install(args.archive, replace=args.replace) if args.action == "install" else
                  manager.uninstall(args.id) if args.action == "uninstall" else manager.installed())
        print(json.dumps(result, indent=2))
        return 0
    except (ExtensionError, OSError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
