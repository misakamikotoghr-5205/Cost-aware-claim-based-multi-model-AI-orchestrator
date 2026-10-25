# MISAKA AGENT as an Agent Skill

The separate [misaka-agent skill](../skills/misaka-agent/README.md) connects
Claude Code, Codex, Hermes and other Agent Skills hosts to the existing desktop
runtime. Copy its directory into the host's configured skills location. It does
not require a ChatGPT plugin, a replacement agent loop, or model credentials in
the skill package.

Build the standalone installation ZIP with:

```sh
python scripts/build_agent_skill.py
```

The archive includes its `SKILL.md`, stdlib CLI, native runtime bridge, command
and transport references, license and file-hash manifest. Configure an explicit
named installation once, then use it with the intended profile. Quota, route
preview, node tasks and installed optional extensions all use native
authenticated APIs. The core agent exposure gate and target node policy remain
in force. Missing optional extensions report unavailable and are never installed
implicitly. Pointer commands return drafts from supplied context hints; screen
capture remains a desktop gesture.
