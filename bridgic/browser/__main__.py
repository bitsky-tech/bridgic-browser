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
    # Surface any catalog consistency failure with a clean exit code instead
    # of crashing inside import machinery, which would otherwise produce a
    # confusing traceback on a fresh install.
    from bridgic.browser._cli_catalog import CATALOG_VALIDATION_ERROR
    if CATALOG_VALIDATION_ERROR is not None:
        print(
            f"bridgic-browser: internal CLI catalog inconsistency: "
            f"{CATALOG_VALIDATION_ERROR}",
            file=sys.stderr,
        )
        sys.exit(3)

    if len(sys.argv) >= 2 and sys.argv[1] == "daemon":
        from bridgic.browser.cli._daemon import main as daemon_main
        daemon_main()
    else:
        from bridgic.browser.cli import main as cli_main
        cli_main()


if __name__ == "__main__":
    main()
