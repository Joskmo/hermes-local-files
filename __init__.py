"""Hermes Local Files does not expose model-facing tools."""


def register(ctx):
    """Keep the portable plugin importable without registering agent tools."""

    del ctx
