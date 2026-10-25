# MISAKA AGENT skill

Extract the separate `misaka-agent` directory into the host's skills directory:

| Host | Typical location |
| --- | --- |
| Claude Code | `~/.claude/skills/misaka-agent` or the project's `.claude/skills/misaka-agent` |
| Codex | `~/.agents/skills/misaka-agent`; use the configured skills location when the host version differs |
| Hermes | The selected `HERMES_HOME/skills/misaka-agent` |

Keep `SKILL.md`, `scripts/` and `references/` together. The ZIP contains code and
instructions only. It contains no runtime, models, credentials, user profile,
gateway ticket or account data. It requires Python 3.10+ but no pip dependency
for its CLI. A MISAKA desktop/runtime with `orchestrator-core` enabled must
already exist. Install optional extensions individually when desired.

Configure the exact installation once (replace the example paths):

```sh
python /path/to/misaka-agent/scripts/misaka.py --connection local \
  --launcher /path/to/hermes-launcher --root /path/to/native-source \
  --home /path/to/hermes-root setup
python /path/to/misaka-agent/scripts/misaka.py --connection local status
python /path/to/misaka-agent/scripts/misaka.py --connection local quota
```

`--home` is the explicit root containing the default home and `profiles/`.
Use `--profile name` for a named profile; the default is explicitly `default`,
so a sticky active profile is never selected. `--root` is the matching native
source installation, containing `hermes_cli/main.py`. `--launcher` must be an
absolute path to that installation's launcher with `--print-runtime-command`
support. Windows installations can use the original `hermes.cmd` launcher;
quote paths with spaces in the invoking shell.

Setup writes only these three nonsecret paths to a named-connection JSON file,
under the platform's config directory. Use `--config /absolute/connections.json`
to select another file. Subsequent commands require the explicit connection
name. Directly supplying all three paths remains supported. The tool does not
search PATH, enroll nodes, log into providers, copy account keys or change the
host agent's permission rules. Setup itself does not start the gateway.

The first runtime command invokes the official `gateway ensure --json`, which
may start the selected existing native gateway. It never starts an agent turn.
If the core was just enabled but its HTTP routes are absent, restart that native
backend as explained in the MISAKA desktop documentation, then retry a read.

See [commands](references/commands.md) and [transport](references/transport.md).
