# MISAKA AGENT desktop identity

The downstream desktop presents MISAKA AGENT in its window titles, empty chat,
onboarding, translated product labels, OS application identity, executable
metadata, and icons. `0003-misaka-branding.patch` contains the authored electric
blue M mark and the actual PNG, ICO, ICNS, macOS layered, and Windows AppX assets.

The welcome screen offers local models, existing OAuth accounts, and API or
self-hosted endpoints. It does not feature a default commercial provider.
The four Chinese starter suggestions only fill the native composer draft;
they never send a message. Providers keep their real names and their original
authentication implementation.

The underlying runtime remains Hermes Agent. Its Python modules, CLI command,
`HERMES_*` compatibility variables, and registered `hermes://` provider callback
scheme retain their original identifiers. About explicitly credits Nous Research
and the MIT license. Original upstream licenses remain in the distribution.
Third-party services and catalog origins are identified as such.

Client updates are disabled at all three native entry points: check, apply,
and channel selection. Packaging publishes no upstream desktop feed. Install
future MISAKA builds from this project's repository until it has its own signed
release feed. Development MSIX metadata uses MISAKA's own publisher name; this
does not establish signing, Store registration, or public installer trust.

Product documentation, downloads, issue reporting, and contribution links target
this project's repository. Third-party provider documentation and the upstream
runtime commit link continue to identify their actual source. The inherited
diagnostics upload remains an explicit third-party consent flow.

The patch is applied after the capture and composer-image patches to the pinned
upstream source. Build the final combined source once it is stable: Hermes's
build freshness check correctly rejects edits made during compilation.
