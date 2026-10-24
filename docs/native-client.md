# Full native Hermes client and separate extension packages

The native client is the **complete upstream Hermes Electron/React application**,
not the earlier Tk form launcher. It keeps Hermes chat and streaming tools,
conversation history, Skills/Hub, MCP and its OAuth flows, model/provider setup,
API keys/OAuth, profiles, file/browser panes, voice, and local/SSH/URL/Cloud
connections. The Orchestrator is an additive native plugin. Provider credentials
continue to belong to Hermes; the package tools never copy them or log in.

## Exact upstream base

`integrations/hermes-client/upstream.json` locks the official repository at
`4dcef215cfe41bd2d02c2d1dad6e257a18101216`. The repo is MIT licensed; retain its
license and copyright notices in distributed native artifacts. The upstream
Desktop package's `0.0.0` version is not a unique compatibility identifier, so
this integration checks the full commit SHA. Native source is cloned into a
cache, not copied as 20,000 vendored files into this repository.

Preparation validates `HEAD`, applies only local `patches/*.patch` in order,
and fingerprints the patch bytes, tracked diff, and all changed patch files
(including new files not yet tracked by Git). An already applied patch is
accepted only when the worktree content matches a private-index reconstruction
of that exact patch. The real index is untouched. A subsequent source or patch
change is rejected; use a fresh cache rather than silently resetting local work.

Follow upstream `AGENTS.md`, `apps/desktop/AGENTS.md` and `src/AGENTS.md` for
changes. The current canonical local backend is `hermes gateway ensure --json`
with private connection tickets. This project does not substitute a one-shot
CLI or another turn loop for that native gateway.

## Prepare, build and launch

Use Python with this repository's requirements, Git, and Node/npm versions
accepted by the pinned upstream `package.json` engines. Source-native builds
also need the platform's compiler/SDK and libraries. No model credentials are
needed to build or run the offline tests.

```bash
python scripts/prepare_native_client.py \
  --hermes-home /path/to/your/chosen-hermes-home --build --launch
```

To reuse the already installed workspace dependencies of an exact upstream
checkout:

```bash
python scripts/prepare_native_client.py \
  --upstream /path/to/pinned-hermes-checkout \
  --hermes-home /path/to/your/chosen-hermes-home --build --skip-deps --launch
```

The same Python commands work in PowerShell; use Windows paths. Choose a short
cache path on Windows. The preparation script never resets an existing checkout.
It uses the upstream `npm ci` and native workspace build, then launches the full
native app with the selected `HERMES_HOME`. `--launch` runs the upstream `start`
script, which builds before opening Electron. Without `--build`/`--launch`,
preparation verifies the base and packages/installs core; upstream dependencies
must already exist to compile its SDK entry.

Only `orchestrator-core` is installed by preparation. Native Capabilities →
Plugins controls activation; package installation itself does not enable or
execute Python/JavaScript. A fresh isolated Hermes home has no provider login;
use the existing native onboarding and OAuth/API-key interfaces. No credentials
are copied from another home.

For upstream source checks and native release packaging:

```bash
npm --workspace apps/desktop run typecheck
npm --workspace apps/desktop run lint
npm --workspace apps/desktop run test:ui
npm --workspace apps/desktop run test:desktop:platforms
npm --workspace apps/desktop run build
```

Upstream `scripts/build-bundle.sh` creates local Linux/macOS native artifacts;
`scripts/build-bundle.ps1` creates Windows MSIX artifacts. Full payload builders
require a clean checkout and their own tag/build provenance rules. Preparation
creates a patched development checkout; do not claim an unsigned development
build is a signed Windows/macOS release. Follow upstream `apps/desktop/BUILDING.md`
for release packaging and its Windows minimum version and platform SDKs.

## Optional packages are separate ZIPs

The optional features are separate Hermes packages, not preinstalled plugin
entries whose UI is merely hidden:

| Package | Installed files come from |
|---|---|
| `orchestrator-core` | `integrations/hermes-client/plugin/` plus an explicit allowlist of common runtime/scoring/ledger modules |
| `local-judge` | `integrations/hermes-client/extensions/local-judge/` |
| `free-models` | `integrations/hermes-client/extensions/free-models/` |
| `smart-pointer` | `integrations/hermes-client/extensions/smart-pointer/` plus its own `_impl/pointer_intents.py` |

Core does not contain the optional plugins' backend/UI/network-refresh entry
points or the pointer engine. Older common routing libraries may expose a local
judge helper, but core does not activate an absent extension from a configuration
flag. A native package must actually be installed and enabled before its feature
is available. Discovery/model downloads and local inference are not performed by
the build or package manager.

Build only core (the default), or explicitly produce independent optional ZIPs:

```bash
python scripts/build_extensions.py --upstream /path/to/pinned-hermes
python scripts/build_extensions.py --upstream /path/to/pinned-hermes \
  --ids local-judge free-models smart-pointer --output dist/extensions
```

`--ids all` builds four separate archives; it never installs the optional three.
The SDK entry is `desktop/src/plugin.tsx` (or `desktop/plugin.tsx`). The build uses
**the pinned upstream's installed esbuild**, bundles local code, and leaves
`@hermes/plugin-sdk`, `react`, and `react/jsx-runtime` external so the native
host supplies its singletons. The installed entry is `desktop/plugin.js`.
It never downloads an `npx` compiler. Existing plain ESM `desktop/plugin.js` is
also supported. Disk plugins must inline their CSS or use the native UI SDK;
the host does not scan their source for Tailwind classes.

Native YAML reads use Hermes' official `hermes_yaml` API when present. The
standalone project falls back to its existing PyYAML dependency; a native
installation does not need an undeclared PyYAML install.

Core's generic Python modules are bundled as `_orchestrator/`; configs live at
`configs/` so `Registry` keeps its relative config lookup. The installer is also
copied to `package_manager.py` for the native plugin's install/remove/inventory
API. Optional ZIPs do not contain another copy of core or another optional
package. The builder includes a license file and deterministic ZIP timestamps.

## Archive contract

A ZIP has files at its root (no directory records), including `plugin.yaml`,
`__init__.py`, native plugin payloads, and this generated manifest:

```json
{
  "schema_version": 1,
  "id": "local-judge",
  "version": "0.1.0",
  "requires": {
    "hermes_commit": "4dcef215cfe41bd2d02c2d1dad6e257a18101216",
    "core_version": "0.1.0"
  },
  "files": {
    "plugin.yaml": "<64 lowercase SHA-256 hex characters>",
    "__init__.py": "<64 lowercase SHA-256 hex characters>"
  }
}
```

`files` declares **every payload file**, excluding `extension.json` itself.
`plugin.yaml` name/version must agree. Core omits `requires.core_version`;
optional packages require an exact installed core version and exact upstream
SHA. Payload files are hash-verified both before and after staging. Hashes detect
corruption; they do not authenticate an unknown author's code. Install packages
from a source you trust, then enable them through native Hermes controls.

Limits: 32 MiB compressed, 64 MiB expanded, at most 2,000 payload files and a
1 MiB manifest. Duplicate/case-colliding members, encrypted records, directory
records, absolute/traversal/backslash/drive/ADS paths, symlinks and device files,
reserved Windows filenames, and common credential/private-key filenames are
rejected. Installation uses explicit file writes, never `extractall`, imports,
package hooks, shell commands, or network calls. It does not read a model-key
store, rewrite `config.yaml`/`.env`, alter native sessions, or enable plugins.

Install, inspect or remove from the selected Hermes home:

```bash
python -m src.extension_packages --hermes-home /path/to/home \
  install dist/extensions/local-judge-0.1.0.zip
python -m src.extension_packages --hermes-home /path/to/home list
python -m src.extension_packages --hermes-home /path/to/home uninstall local-judge
```

Newer versions upgrade atomically. Downgrades, changed same-version archives and
repairs require explicit CLI `--replace`; identical intact installs are
idempotent. An OS advisory lock serializes operations. Staging, backup and a
durable transaction journal keep upgrades/removals recoverable; the next package
operation finishes committed cleanup or restores the previous directory after
an interrupted operation. A registry-write failure restores the old plugin.
Unmanaged existing Hermes plugin directories are never overwritten. Remove
optional dependants before removing core or moving core to an incompatible
version. The manager reports physical installed-file integrity separately from
native enablement; core UI must consult both before reporting availability.

Disable a running plugin through native Capabilities before removal. Native
plugin/config state is retained, and already imported Python modules require
native backend reload/restart to unload fully. Plugin-owned durable user data
outside the package directory is retained. This prevents uninstall from deleting
user files or provider credentials.

Run offline framework checks:

```bash
python -m pytest -q tests/test_extension_packages.py
```

Tests cover corrupt/malicious ZIPs, dependency checks, unchanged native settings,
no payload execution, reproducible archives, atomic failure rollback, recovery,
physical-file checks, and exact/idempotent patch preparation. Native screenshot,
GUI, model and OAuth acceptance still require the relevant host and account;
passing package tests does not assert those platform capabilities were exercised.
