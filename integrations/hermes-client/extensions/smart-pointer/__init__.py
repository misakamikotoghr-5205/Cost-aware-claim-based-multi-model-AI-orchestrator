"""Opt-in Hermes plugin. The native loader owns install/enable consent.

No tools, hooks, screenshot capture or model calls are registered here. The
Desktop extension and dashboard API implement an explicit user-driven draft UI.
"""


def register(ctx):
    """No runtime side effects when this optional plugin is enabled."""
    return None
