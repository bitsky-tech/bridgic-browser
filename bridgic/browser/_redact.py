"""
Shared redaction helpers.

CDP URLs can carry secrets (Playwright Service tokens in ``?token=``, bespoke
authentication headers encoded in the path, etc.).  Any code path that emits
a CDP URL to a log record, a status file, or a CLI error message should pass
it through :func:`redact_cdp_url` first so secrets never reach an observer.

The redaction rules:

- ``ws(s)://localhost[:port]/...`` / ``127.0.0.1`` / ``::1`` → just the port
  string (or ``"9222"`` if the URL omitted the port). Local URLs have no
  secrets worth hiding but a bare port is a more readable display value.
- Remote URLs → ``<scheme>://<host>[:<port>]`` — path, query and fragment are
  dropped so tokens, session IDs, and similar query parameters never leak.
"""
from __future__ import annotations

from urllib.parse import urlparse


def redact_cdp_url(cdp: str) -> str:
    """Return a display-safe form of a CDP URL (see module docstring)."""
    _parsed = urlparse(cdp)
    _host = (_parsed.hostname or "").lower()
    if _host in ("localhost", "127.0.0.1", "::1"):
        return str(_parsed.port or 9222)
    _port = f":{_parsed.port}" if _parsed.port is not None else ""
    return f"{_parsed.scheme}://{_parsed.hostname or _parsed.netloc}{_port}"


__all__ = ["redact_cdp_url"]
