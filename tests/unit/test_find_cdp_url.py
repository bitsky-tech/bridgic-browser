"""Unit tests for find_cdp_url() — CDP WebSocket URL discovery."""

import io
import json
import sys
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bridgic.browser.session._browser import _CDP_SCAN_DIRS, find_cdp_url


# ---------------------------------------------------------------------------
# mode="port" — netloc rewrite (B-3 / B-9)
# ---------------------------------------------------------------------------

def _mock_version_response(ws_url: str) -> MagicMock:
    """Return a mock that acts like `urlopen(...).read()` for /json/version."""
    body = json.dumps({
        "Browser": "Chrome/127.0.0.0",
        "webSocketDebuggerUrl": ws_url,
    }).encode()
    m = MagicMock()
    m.read = MagicMock(return_value=body)
    return m


class TestFindCdpUrlPortNetlocRewrite:
    """B-3: when the caller's port differs from the port Chrome advertises in
    webSocketDebuggerUrl (SSH tunnel, container port-forward, reverse proxy),
    we must rewrite the netloc to (caller_host:caller_port).
    """

    def test_rewrites_port_for_ssh_tunnel(self) -> None:
        """SSH -L 12345:host:9222 → caller port 12345, Chrome reports 9222."""
        chrome_reported = (
            "ws://localhost:9222/devtools/browser/abc-def-123"
        )
        fake_resp = _mock_version_response(chrome_reported)
        # Loopback path uses build_opener(...).open(...)
        with patch("urllib.request.build_opener") as mock_opener:
            mock_opener.return_value.open.return_value = fake_resp
            result = find_cdp_url(mode="port", host="localhost", port=12345)
        # Port must be rewritten from 9222 → 12345 (the port the caller can
        # actually reach), while the path is preserved verbatim.
        assert result == "ws://localhost:12345/devtools/browser/abc-def-123"

    def test_rewrites_port_for_ipv4_loopback(self) -> None:
        chrome_reported = "ws://127.0.0.1:9222/devtools/browser/xxx"
        fake_resp = _mock_version_response(chrome_reported)
        with patch("urllib.request.build_opener") as mock_opener:
            mock_opener.return_value.open.return_value = fake_resp
            result = find_cdp_url(mode="port", host="127.0.0.1", port=18888)
        assert result == "ws://127.0.0.1:18888/devtools/browser/xxx"

    def test_strips_bracketed_ipv6_input(self) -> None:
        """B-9: caller-supplied ``[::1]`` must not become ``[[::1]]``."""
        chrome_reported = "ws://[::1]:9222/devtools/browser/vvv"
        fake_resp = _mock_version_response(chrome_reported)
        with patch("urllib.request.build_opener") as mock_opener:
            mock_opener.return_value.open.return_value = fake_resp
            result = find_cdp_url(mode="port", host="[::1]", port=55555)
        # Brackets preserved exactly once in the result.
        assert result == "ws://[::1]:55555/devtools/browser/vvv"
        assert "[[" not in result

    def test_remote_host_uses_urlopen_and_rewrites_port(self) -> None:
        """Non-loopback host must NOT strip proxy (uses urlopen directly)."""
        chrome_reported = "ws://localhost:9222/devtools/browser/foo"
        fake_resp = _mock_version_response(chrome_reported)
        with patch("urllib.request.urlopen", return_value=fake_resp) as mock_urlopen:
            result = find_cdp_url(
                mode="port", host="remote.example.com", port=9222,
            )
        mock_urlopen.assert_called_once()
        # Chrome reported its local hostname ("localhost"), but the result
        # must carry the caller's host (the address that actually answered).
        assert result == "ws://remote.example.com:9222/devtools/browser/foo"


# ---------------------------------------------------------------------------
# mode="port" / mode="file" — stale-port liveness probe (B-4)
# ---------------------------------------------------------------------------

class TestFindCdpUrlLivenessProbe:
    """B-4: scan / file mode must skip candidates where /json/version doesn't
    answer.  Chrome removes DevToolsActivePort on graceful exit but leaves it
    behind on crash / kill -9 — a stale file would otherwise feed a dead ws://
    URL to connect_over_cdp and surface as an opaque connection error much later.
    """

    def test_scan_skips_stale_profile_and_returns_next_live(self, tmp_path: Path) -> None:
        stale = tmp_path / "stale_profile"
        live = tmp_path / "live_profile"
        stale.mkdir()
        live.mkdir()
        (stale / "DevToolsActivePort").write_text("9998\n/devtools/browser/dead\n")
        (live / "DevToolsActivePort").write_text("9999\n/devtools/browser/alive\n")

        # Point candidates at our two fake profile dirs (label, path).
        patched_dirs = {"darwin": [("A", str(stale)), ("B", str(live))]}

        # First _probe_cdp_alive call (for stale) returns False, second returns True.
        probe_results = iter([False, True])

        with patch.object(sys, "platform", "darwin"):
            with patch.dict(_CDP_SCAN_DIRS, patched_dirs, clear=True):
                with patch(
                    "bridgic.browser.session._browser._probe_cdp_alive",
                    side_effect=lambda *_a, **_k: next(probe_results),
                ):
                    result = find_cdp_url(mode="scan")

        assert result == "ws://localhost:9999/devtools/browser/alive"

    def test_scan_raises_when_all_candidates_stale(self, tmp_path: Path) -> None:
        p = tmp_path / "only_profile"
        p.mkdir()
        (p / "DevToolsActivePort").write_text("9999\n/devtools/browser/dead\n")

        patched_dirs = {"darwin": [("Only", str(p))]}
        with patch.object(sys, "platform", "darwin"):
            with patch.dict(_CDP_SCAN_DIRS, patched_dirs, clear=True):
                with patch(
                    "bridgic.browser.session._browser._probe_cdp_alive",
                    return_value=False,
                ):
                    with pytest.raises(RuntimeError, match="No locally running browser"):
                        find_cdp_url(mode="scan")

    def test_file_mode_raises_on_stale_port_file(self, tmp_path: Path) -> None:
        """mode='file' with explicit user_data_dir: stale file → ConnectionError."""
        (tmp_path / "DevToolsActivePort").write_text("9999\n/devtools/browser/dead\n")
        with patch(
            "bridgic.browser.session._browser._probe_cdp_alive",
            return_value=False,
        ):
            with pytest.raises(ConnectionError, match="not accepting CDP"):
                find_cdp_url(mode="file", user_data_dir=str(tmp_path))


# ---------------------------------------------------------------------------
# _CDP_SCAN_DIRS coverage (B-5 / B-7)
# ---------------------------------------------------------------------------

class TestCdpScanDirs:
    """B-5: Linux candidate list must include Snap + Flatpak paths in addition
    to native ``~/.config`` paths.
    """

    def test_linux_includes_snap_paths(self) -> None:
        linux_paths = [p for _, p in _CDP_SCAN_DIRS["linux"]]
        assert any("snap/chromium" in p for p in linux_paths), (
            f"Snap Chromium path missing from: {linux_paths}"
        )
        assert any("snap/brave" in p for p in linux_paths), (
            f"Snap Brave path missing from: {linux_paths}"
        )

    def test_linux_includes_flatpak_paths(self) -> None:
        linux_paths = [p for _, p in _CDP_SCAN_DIRS["linux"]]
        assert any(".var/app/com.google.Chrome" in p for p in linux_paths), (
            f"Flatpak Chrome path missing from: {linux_paths}"
        )
        assert any(".var/app/org.chromium.Chromium" in p for p in linux_paths), (
            f"Flatpak Chromium path missing from: {linux_paths}"
        )

    def test_linux_includes_edge_and_brave_native(self) -> None:
        linux_paths = [p for _, p in _CDP_SCAN_DIRS["linux"]]
        assert any("microsoft-edge" in p for p in linux_paths), linux_paths
        assert any("BraveSoftware" in p for p in linux_paths), linux_paths

    def test_darwin_covers_chrome_and_brave(self) -> None:
        darwin_paths = [p for _, p in _CDP_SCAN_DIRS["darwin"]]
        assert any("Google/Chrome" in p and "Canary" not in p for p in darwin_paths)
        assert any("BraveSoftware" in p for p in darwin_paths)
