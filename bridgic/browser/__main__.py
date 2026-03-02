"""
Entry point for ``python -m bridgic.browser``.

Supports an internal ``daemon`` sub-command used by the CLI client to
start the background browser process:

    python -m bridgic.browser daemon

All other invocations are forwarded to the Click CLI:

    python -m bridgic.browser open https://example.com
    python -m bridgic.browser snapshot
    ...
"""
import sys


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == "daemon":
        from bridgic.browser.cli._daemon import main as daemon_main
        daemon_main()
    else:
        from bridgic.browser.cli import main as cli_main
        cli_main()


if __name__ == "__main__":
    main()
