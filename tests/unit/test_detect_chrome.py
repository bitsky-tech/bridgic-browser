"""Unit tests for _detect_system_chrome() cross-platform detection."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from bridgic.browser.session._browser import _detect_system_chrome


# ---------------------------------------------------------------------------
# macOS: /Applications vs ~/Applications (B-8)
# ---------------------------------------------------------------------------

class TestDetectMacOS:
    def test_detects_system_wide_install(self) -> None:
        """Drag-and-drop install under /Applications is detected."""
        real_isfile = os.path.isfile

        def fake_isfile(path: str) -> bool:
            if path == "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome":
                return True
            return False

        with patch.object(sys, "platform", "darwin"):
            with patch("os.path.isfile", side_effect=fake_isfile):
                assert _detect_system_chrome() is True

    def test_detects_user_level_install_under_home_applications(self, tmp_path: Path) -> None:
        """B-8: ``~/Applications/Google Chrome.app`` (non-admin user) is detected."""
        expected = str(
            tmp_path / "Applications" / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome"
        )

        def fake_isfile(path: str) -> bool:
            return path == expected

        with patch.object(sys, "platform", "darwin"):
            with patch.object(Path, "home", classmethod(lambda cls: tmp_path)):
                with patch("os.path.isfile", side_effect=fake_isfile):
                    assert _detect_system_chrome() is True

    def test_returns_false_when_neither_install_present(self, tmp_path: Path) -> None:
        with patch.object(sys, "platform", "darwin"):
            with patch.object(Path, "home", classmethod(lambda cls: tmp_path)):
                with patch("os.path.isfile", return_value=False):
                    assert _detect_system_chrome() is False


# ---------------------------------------------------------------------------
# Linux: extended binary list (B-7 smoke check)
# ---------------------------------------------------------------------------

class TestDetectLinux:
    """Smoke coverage for the Linux binary list extended in B-7.

    The function returns True if ANY of a list of known Chromium-flavored
    binaries resolves on PATH.
    """

    @pytest.mark.parametrize(
        "binary",
        [
            "google-chrome",
            "google-chrome-stable",
            "chromium",
            "chromium-browser",
            "microsoft-edge",
            "brave-browser",
        ],
    )
    def test_detects_each_known_binary(self, binary: str) -> None:
        with patch.object(sys, "platform", "linux"):
            with patch(
                "shutil.which",
                side_effect=lambda b: f"/usr/bin/{b}" if b == binary else None,
            ):
                assert _detect_system_chrome() is True

    def test_returns_false_when_no_chromium_family_on_path(self) -> None:
        with patch.object(sys, "platform", "linux"):
            with patch("shutil.which", return_value=None):
                assert _detect_system_chrome() is False


# ---------------------------------------------------------------------------
# Windows: Program Files + LOCALAPPDATA
# ---------------------------------------------------------------------------

class TestDetectWindows:
    def test_detects_chrome_under_program_files(self, tmp_path: Path) -> None:
        program_files = tmp_path / "Program Files"
        chrome_path = program_files / "Google" / "Chrome" / "Application" / "chrome.exe"
        chrome_path.parent.mkdir(parents=True)
        chrome_path.touch()

        env_patch = {
            "PROGRAMFILES": str(program_files),
            "LOCALAPPDATA": "",
            "PROGRAMFILES(X86)": "",
        }
        with patch.object(sys, "platform", "win32"):
            with patch.dict(os.environ, env_patch):
                assert _detect_system_chrome() is True

    def test_returns_false_when_no_install_found(self) -> None:
        # Must mock all three tiers. On a Windows CI runner with Chrome
        # installed, the shutil.which (Tier 2) and winreg App Paths (Tier 3)
        # fallbacks would otherwise find the real installation.
        fake_winreg = type(sys)("winreg")
        fake_winreg.HKEY_LOCAL_MACHINE = 0
        fake_winreg.HKEY_CURRENT_USER = 1

        def _open_key(*_args, **_kwargs):  # type: ignore[no-untyped-def]
            raise OSError("not found")

        fake_winreg.OpenKey = _open_key
        fake_winreg.QueryValueEx = lambda *_a, **_kw: ("", 0)  # unused

        with patch.object(sys, "platform", "win32"):
            with patch.dict(
                os.environ,
                {"LOCALAPPDATA": "", "PROGRAMFILES": "", "PROGRAMFILES(X86)": ""},
            ):
                with patch("shutil.which", return_value=None):
                    with patch.dict(sys.modules, {"winreg": fake_winreg}):
                        assert _detect_system_chrome() is False
