# Native runtime transport

The runtime is the complete Hermes-based MISAKA installation. This skill's
transport is compatible with the pinned upstream commit
`4dcef215cfe41bd2d02c2d1dad6e257a18101216` and MISAKA core 0.2.0.

The CLI uses the explicitly selected installation launcher to obtain its real
Python interpreter with `--print-runtime-command`, then calls the original
`--profile NAME gateway ensure --json --timeout 60` command. It requires a ready
endpoint for the exact resolved selected home, runtime protocol 1, and matching
client/runtime code identity. It does not parse secrets from discovery files.
The child environment explicitly binds `HERMES_INSTALL_ROOT` to the selected
source. Before starting the gateway, the CLI parses the canonical launch command
without executing its code and checks its actual `sys.path.insert` source root;
a launcher naming another tree is refused even if that tree has the same version.

For each fixed API operation a child runs under that interpreter and source
root. It uses the official `hermes_cli.gateway_client._session_ticket` with
purpose `native-http`. That implementation verifies the same-user control
socket on POSIX and the owner/PID/SID-protected native control transport on
Windows, including multiplexer `control_home` handling. The ticket is bound to
the endpoint instance and profile, expires after 30 seconds, and is redeemed
once via `X-Hermes-Gateway-Ticket`. A fresh ticket is minted for each request.

Only the discovered numeric `127.0.0.1` or `::1` HTTP origin is accepted, with
no URL credentials, query, fragment or path. HTTP proxies and redirects are
disabled. The bridge has a fixed operation allowlist; it cannot proxy arbitrary
native APIs. It supplies no browser `Origin` header, matching the native private
HTTP boundary. Tickets never leave that runtime child's memory via output or
files. No API key or OAuth credential is placed in this package or transmitted
to a fleet cloud. Only the native backend can resolve configured credentials.

The `POST /agent/fleet` route delegates to the same function used by the native
fleet model tool: explicit service enablement, owner identity, permitted node,
tools, workspace aliases, action budget and bounded explicit plans are checked
there. The target node performs its independent local admission and verification.
The native tool retains its approval hook; an external host applies its own
permissions to its CLI calls. This skill does not pretend to override host policy.
