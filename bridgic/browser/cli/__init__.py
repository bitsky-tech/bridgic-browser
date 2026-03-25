"""
bridgic.browser.cli — command-line interface for bridgic-browser.

Entry point: ``bridgic-browser`` (configured in pyproject.toml).
"""
from ._commands import cli


def main() -> None:
    """CLI entry point called by the ``bridgic-browser`` script."""
    cli()


__all__ = ["main", "cli"]
