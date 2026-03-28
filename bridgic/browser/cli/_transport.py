"""Platform-agnostic transport layer for the Bridgic Browser daemon.

POSIX: Unix domain socket (existing behaviour, unchanged).
Windows: TCP (127.0.0.1) + random port + token authentication.

get_transport() is the only platform-sensitive code path; all other modules
use BaseTransport exclusively.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
import stat
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .._constants import BRIDGIC_HOME

# ── Run info file ─────────────────────────────────────────────────────────────

RUN_INFO_PATH = BRIDGIC_HOME / "run" / "bridgic-browser.json"


def _ensure_run_dir() -> None:
    """Create the run directory with private permissions."""
    run_dir = RUN_INFO_PATH.parent
    run_dir.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        try:
            os.chmod(run_dir, 0o700)
        except OSError:
            pass


def write_run_info(info: Dict[str, Any]) -> None:
    """Atomically write run info using write-then-replace.

    Uses Path.replace() rather than rename() so that the operation succeeds
    even when the destination already exists (required on Windows).
    """
    _ensure_run_dir()
    tmp_path = RUN_INFO_PATH.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(info), encoding="utf-8")
    tmp_path.replace(RUN_INFO_PATH)
    if sys.platform != "win32":
        try:
            os.chmod(RUN_INFO_PATH, 0o600)
        except OSError:
            pass


def read_run_info() -> Optional[Dict[str, Any]]:
    """Return the run info dict, or None if missing or malformed."""
    try:
        return json.loads(RUN_INFO_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def remove_run_info() -> None:
    """Remove the run info file; no-op if already gone."""
    try:
        RUN_INFO_PATH.unlink()
    except OSError:
        pass


# ── Socket helpers (migrated from _daemon.py) ────────────────────────────────

def _default_socket_path() -> str:
    """Return per-user default socket path."""
    return str(BRIDGIC_HOME / "run" / "bridgic-browser.sock")


def _ensure_socket_parent_dir(path: str) -> None:
    """Create socket parent dir with private permissions when possible."""
    parent = Path(path).parent
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(parent, 0o700)
    except OSError:
        # Custom socket directories (e.g. /tmp) may not be chmod-able by us.
        pass


def _safe_remove_socket(path: str) -> None:
    """Remove a socket file only if it is owned by the current user."""
    socket_path = Path(path)
    try:
        st = socket_path.stat()
    except FileNotFoundError:
        return

    if not stat.S_ISSOCK(st.st_mode):
        raise RuntimeError(f"Refusing to remove non-socket path: {path}")

    if hasattr(os, "getuid"):
        current_uid = os.getuid()
        if st.st_uid != current_uid:
            raise PermissionError(
                f"Socket path {path} is owned by uid={st.st_uid}, current uid={current_uid}"
            )

    try:
        socket_path.unlink()
    except FileNotFoundError:
        # Another process may remove the socket between stat() and unlink().
        return


# ── Abstract base ─────────────────────────────────────────────────────────────

class BaseTransport(ABC):
    """Platform-agnostic transport interface for daemon communication."""

    @abstractmethod
    async def start_server(
        self,
        connection_cb: Callable,
        *,
        stream_limit: int,
    ) -> asyncio.AbstractServer:
        """Start listening and return an asyncio server."""

    @abstractmethod
    def build_run_info(self, *, pid: int) -> Dict[str, Any]:
        """Return the dict to persist in the run info file."""

    @abstractmethod
    async def open_connection(self, *, stream_limit: int) -> tuple:
        """Open a connection to the daemon; returns (reader, writer)."""

    def inject_auth(self, request_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Return a (possibly augmented) request dict. No-op on POSIX."""
        return request_dict

    def verify_auth(self, _request_dict: Dict[str, Any]) -> bool:
        """Return True if the request is authorised. Always True on POSIX."""
        return True

    @abstractmethod
    def probe(self) -> bool:
        """Synchronously probe whether the daemon is reachable."""

    def cleanup(self) -> None:
        """Clean up transport resources (e.g., remove socket file)."""


# ── POSIX — Unix domain socket ────────────────────────────────────────────────

class UnixTransport(BaseTransport):
    """Unix domain socket transport for POSIX systems."""

    def __init__(self, socket_path: str) -> None:
        self._path = socket_path

    @property
    def socket_path(self) -> str:
        return self._path

    async def start_server(
        self,
        connection_cb: Callable,
        *,
        stream_limit: int,
    ) -> asyncio.AbstractServer:
        _ensure_socket_parent_dir(self._path)
        if os.path.exists(self._path):
            _safe_remove_socket(self._path)
        server = await asyncio.start_unix_server(
            connection_cb, path=self._path, limit=stream_limit
        )
        try:
            os.chmod(self._path, 0o600)
        except OSError:
            pass
        return server

    def build_run_info(self, *, pid: int) -> Dict[str, Any]:
        return {"transport": "unix", "socket": self._path, "pid": pid}

    async def open_connection(self, *, stream_limit: int) -> tuple:
        return await asyncio.open_unix_connection(self._path, limit=stream_limit)

    def probe(self) -> bool:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(2)
            sock.connect(self._path)
            return True
        except (ConnectionRefusedError, FileNotFoundError, OSError):
            return False
        finally:
            sock.close()

    def cleanup(self) -> None:
        if os.path.exists(self._path):
            try:
                _safe_remove_socket(self._path)
            except Exception:
                pass


# ── Windows — TCP loopback ────────────────────────────────────────────────────

class TcpTransport(BaseTransport):
    """TCP loopback transport with token authentication for Windows."""

    def __init__(
        self,
        port: Optional[int] = None,
        token: Optional[str] = None,
    ) -> None:
        self._port = port
        self._token = token

    async def start_server(
        self,
        connection_cb: Callable,
        *,
        stream_limit: int,
    ) -> asyncio.AbstractServer:
        server = await asyncio.start_server(
            connection_cb,
            host="127.0.0.1",
            port=0,
            limit=stream_limit,
        )
        self._port = server.sockets[0].getsockname()[1]
        self._token = secrets.token_hex(32)
        return server

    def build_run_info(self, *, pid: int) -> Dict[str, Any]:
        return {
            "transport": "tcp",
            "port": self._port,
            "token": self._token,
            "pid": pid,
        }

    async def open_connection(self, *, stream_limit: int) -> tuple:
        # Use port/token already injected by the factory when available so that
        # a daemon restart between get_transport() and open_connection() does
        # not silently pick up a mismatched token from the new run info.
        if self._port is None or self._token is None:
            info = read_run_info()
            if info is None or info.get("transport") != "tcp":
                raise ConnectionRefusedError("No valid TCP run info found")
            self._port = info["port"]
            self._token = info.get("token")
        return await asyncio.open_connection("127.0.0.1", self._port, limit=stream_limit)

    def inject_auth(self, request_dict: Dict[str, Any]) -> Dict[str, Any]:
        if self._token is None:
            info = read_run_info()
            self._token = info.get("token") if info else None
        return {**request_dict, "_token": self._token}

    def verify_auth(self, request_dict: Dict[str, Any]) -> bool:
        if self._token is None:
            return False  # token not yet initialised — reject all
        client_token = request_dict.get("_token") or ""
        return secrets.compare_digest(client_token, self._token)

    def probe(self) -> bool:
        info = read_run_info()
        if not info or info.get("transport") != "tcp":
            return False
        port = info.get("port")
        if not port:
            return False
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.settimeout(2)
            sock.connect(("127.0.0.1", port))
            return True
        except (ConnectionRefusedError, OSError):
            return False
        finally:
            sock.close()

    def cleanup(self) -> None:
        pass  # No file cleanup needed for TCP


# ── Factory ───────────────────────────────────────────────────────────────────

def get_transport() -> BaseTransport:
    """Return the appropriate transport for the current platform.

    sys.platform check only occurs in this one function; all other code is
    platform-agnostic.
    """
    if sys.platform == "win32":
        info = read_run_info()
        if info and info.get("transport") == "tcp":
            port = info.get("port")
            token = info.get("token")
            if port is not None and token is not None:
                return TcpTransport(port=port, token=token)
        return TcpTransport()
    socket_path = os.environ.get("BRIDGIC_SOCKET", _default_socket_path())
    return UnixTransport(socket_path)
