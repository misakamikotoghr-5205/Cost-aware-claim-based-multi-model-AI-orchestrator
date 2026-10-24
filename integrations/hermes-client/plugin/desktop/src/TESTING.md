# Native plugin UI verification

Use the pinned Hermes source checkout with its desktop Node dependencies installed.
No extension/model account or weights are required for these tests.

```bash
ORCHESTRATOR_HERMES_SOURCE=/path/to/hermes \
  /path/to/hermes/node_modules/.bin/vitest run \
  --config integrations/hermes-client/plugin/desktop/src/vitest.config.mts
```

The interaction tests cover:

- Canonical local worker requests while native chat is connected to a cloud host.
- No controls for extensions absent from the installed inventory.
- Model installation only after the explicit download gesture and confirmed job progress.
- Native screen crops and preview-image drag crops, including pixel scaling.
- Multiple intent choices, draft preview and insertion into the exact native composer.
- No screenshot upload, automatic submit, or stale response after a new selection.
- Connection changes refusing insertion into a different conversation owner.

SDK primitives are presentation mocks in these interaction tests. Production source
uses the actual `@hermes/plugin-sdk` types. Native capture, backend protocol and
installation/packaging contracts have separate tests in their owning packages.
The packager compiles each `desktop/src/plugin.tsx` independently and omits source
and tests from the ZIP; React and the SDK stay host-provided externals.
