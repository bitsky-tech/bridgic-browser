"""Tests for bridgic.browser._constants — BRIDGIC_HOME env var support.

Uses subprocess to avoid import-cache pollution: each test spawns a fresh
Python process where the env var is set *before* _constants is imported,
so every derived path is computed from scratch.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


_PROBE_SCRIPT = """\
import json, os, sys
from bridgic.browser._constants import (
    BRIDGIC_HOME,
    BRIDGIC_BROWSER_HOME,
    BRIDGIC_TMP_DIR,
    BRIDGIC_SNAPSHOT_DIR,
    BRIDGIC_USER_DATA_DIR,
    BRIDGIC_DOWNLOADS_DIR,
)
print(json.dumps({
    "home": str(BRIDGIC_HOME),
    "browser_home": str(BRIDGIC_BROWSER_HOME),
    "tmp": str(BRIDGIC_TMP_DIR),
    "snapshot": str(BRIDGIC_SNAPSHOT_DIR),
    "user_data": str(BRIDGIC_USER_DATA_DIR),
    "downloads": str(BRIDGIC_DOWNLOADS_DIR),
}))
"""


def _run_probe(env_override: dict[str, str] | None = None) -> dict[str, str]:
    """Run the probe script in a subprocess and return parsed JSON."""
    env = os.environ.copy()
    if env_override:
        env.update(env_override)
    result = subprocess.run(
        [sys.executable, "-c", _PROBE_SCRIPT],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, f"probe failed: {result.stderr}"
    return json.loads(result.stdout)


def test_default_bridgic_home() -> None:
    """Without BRIDGIC_HOME env var, defaults to ~/.bridgic."""
    env = os.environ.copy()
    env.pop("BRIDGIC_HOME", None)
    result = subprocess.run(
        [sys.executable, "-c", _PROBE_SCRIPT],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["home"] == str(Path.home() / ".bridgic")


def test_bridgic_home_env_var(tmp_path: Path) -> None:
    """BRIDGIC_HOME env var overrides the default, and all derived paths follow."""
    data = _run_probe({"BRIDGIC_HOME": str(tmp_path)})
    assert data["home"] == str(tmp_path)
    assert data["browser_home"] == str(tmp_path / "bridgic-browser")
    assert data["tmp"] == str(tmp_path / "bridgic-browser" / "tmp")
    assert data["snapshot"] == str(tmp_path / "bridgic-browser" / "snapshot")
    assert data["user_data"] == str(tmp_path / "bridgic-browser" / "user_data")
    assert data["downloads"] == str(tmp_path / "bridgic-browser" / "downloads")


def test_bridgic_home_tilde_expansion() -> None:
    """BRIDGIC_HOME with ~ is expanded via expanduser()."""
    data = _run_probe({"BRIDGIC_HOME": "~/custom-bridgic"})
    assert data["home"] == str(Path.home() / "custom-bridgic")
    assert "~" not in data["home"]
