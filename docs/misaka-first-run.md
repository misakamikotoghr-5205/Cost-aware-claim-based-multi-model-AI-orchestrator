# MISAKA first run and usage statistics

`0006-misaka-first-run.patch` applies after `0001` through `0005` at the pinned
Hermes source revision in `integrations/hermes-client/upstream.json`.

The provider picker replaces only the runtime's known blank-install sentences
with localized MISAKA configuration guidance. Chinese reads: “MISAKA AGENT
尚未连接模型。请选择本地模型、OAuth 或 API，也可以稍后配置。” Other caller-supplied
errors keep their diagnostic text. Provider names, `HERMES_*` variables, CLI
commands, and the `hermes://` OAuth protocol retain their upstream identities.

After setup settles, the Desktop consent host reads `shared_metrics.status` for
the focused profile. Only a fresh, undecided state with both `enabled` and `send`
off, and no legacy `reask`, is initialized through `shared_metrics.set` with
`enabled: false`, `send: false`, and `first_run: false`. The existing backend
writes both explicit values and `offer_version` into that profile's
`telemetry.shared_metrics` config section. It also closes the upload consent
window and purges pending install/update receipts. The shipped backend defaults
already keep collection and uploading off before initialization completes.

The renderer publishes the confirmed backend result only after a successful
write, so a fresh installation shows no Nous statistics offer. An existing
opt-in, opt-out, collect-only answer, or legacy opt-out is not rewritten. MISAKA
does not re-ask a legacy explicit refusal. Settings → Privacy retains the real
Nous third-party attribution and displays an existing enabled sharing switch.

Initialization checks the selected connection ID, gateway instance, activation epoch, profile effect
lifetime, and refresh generation before writing or publishing. A late response
after a connection/profile switch cannot initialize the new owner. The existing
gateway dispatcher also binds transport retries to their original route. A
failed write leaves consent unknown and shows the actual error with a retry
action; window focus retries the read and initialization.

Validation uses the official Desktop UI Vitest project for first-run guidance,
consent initialization, existing choices, scope changes, failure/retry, the Nous
settings switch, the original consent strip, and runtime-readiness diagnostics.
The backend RPC tests exercise real profile config persistence, closed consent
windows, and receipt purging. No full Desktop build is needed for this patch's
targeted validation.
