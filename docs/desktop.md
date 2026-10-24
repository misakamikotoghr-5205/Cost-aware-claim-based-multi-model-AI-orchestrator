# Native desktop client

The primary application is the complete Hermes Electron desktop at the source
revision in `integrations/hermes-client/upstream.json`, plus a narrow native
capture patch and official plugin packages. Its original conversation, streaming,
provider OAuth/API, history, approvals, tools, Skills, MCP and profile interfaces
remain intact. This is no longer the standalone Tk task form.

```bash
python -m pip install -r requirements.txt
python -m src.desktop --hermes-home /path/to/native/profile
```

The first launch prepares and builds the pinned native source. Node/npm and the
native upstream build prerequisites are required. See [native-client.md](native-client.md).
A basic local OAuth/API conversation uses native provider settings and does not
require the fleet server, an orchestrator quota observation, or a verifier.

## Optional installation packages

All three packages are delivered, and each can be installed separately:

- `local-judge`: setup and local Ollama model judgment, with deterministic fallback.
- `free-models`: explicit free/limited-free discovery, evidence import and TTL cache.
- `smart-pointer`: screen region + keywords, intent suggestions, refinement and a
  draft in the existing native chat. Selecting an intent never automatically sends it.

Uninstalled packages have no plugin entry, settings page, hook or background
service. Native Plugins settings control installed plugin enable/disable; no
package silently activates another. Model weights are downloaded only after an
explicit setup action. [native-extensions.md](native-extensions.md) documents setup.

## Cloud and local machines

One owner can pair multiple nodes. Each node receives its own revocable transport
token, advertises named tools/workspace aliases, and initiates outbound HTTPS.
Owner controls can list/select nodes, send constrained tasks, and revoke access.
Nodes cannot claim another node's work or access another owner's quota.

Node controls are pinned to the local canonical Hermes gateway even when the
foreground chat is connected to a remote model gateway. Local provider credentials
stay on their own machine; cloud tokens are not model OAuth/API credentials.
See [fleet-protocol.md](fleet-protocol.md) for pairing and communications.

## Verification and platform scope

Tests cover the package lifecycle, native SDK integration, local HTTP inference
fixtures, screen-selection geometry, and real loopback multi-node communications.
Live provider logins and user-machine Windows/X11/Wayland capture still require
platform acceptance. A source build is distinct from a signed Windows installer;
use the native build guide for platform packaging commands.

The old task form remains accessible with `python -m src.desktop --legacy` for
backwards compatibility. Its documentation is [legacy-desktop.md](legacy-desktop.md).
