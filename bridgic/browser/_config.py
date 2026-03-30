"""
Bridgic Browser — config file loading.

Loads Browser constructor kwargs from config files and environment variables
using a layered priority chain (lowest to highest):

  1. ~/.bridgic/bridgic-browser/bridgic-browser.json   — user persistent config
  2. ./bridgic-browser.json            — project-local config
  3. BRIDGIC_BROWSER_JSON env var      — runtime override (full JSON)
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict

from ._constants import BRIDGIC_BROWSER_HOME

logger = logging.getLogger(__name__)

# Config file name, shared between user home and project directory
_CONFIG_FILENAME = "bridgic-browser.json"

# Environment variable name for JSON overrides
_ENV_VAR = "BRIDGIC_BROWSER_JSON"


def _load_config_sources() -> Dict[str, Any]:
    """Load config from files and environment variable only (no defaults).

    Priority (lowest → highest):
      1. ~/.bridgic/bridgic-browser/bridgic-browser.json  — user persistent config
      2. ./bridgic-browser.json           — project-local config
      3. BRIDGIC_BROWSER_JSON env var     — runtime override (full JSON)

    Returns
    -------
    Dict[str, Any]
        Merged config from all sources. Empty dict if no sources found.
    """
    cfg: Dict[str, Any] = {}

    # 1. User persistent config: ~/.bridgic/bridgic-browser/bridgic-browser.json
    user_cfg = BRIDGIC_BROWSER_HOME / _CONFIG_FILENAME
    if user_cfg.is_file():
        try:
            cfg.update(json.loads(user_cfg.read_text()))
        except Exception:
            logger.warning("failed to parse user config %s", user_cfg, exc_info=True)

    # 2. Project-local config: ./bridgic-browser.json
    local_cfg = Path(_CONFIG_FILENAME)
    if local_cfg.is_file():
        try:
            cfg.update(json.loads(local_cfg.read_text()))
        except Exception:
            logger.warning("failed to parse local config %s", local_cfg, exc_info=True)

    # 3. BRIDGIC_BROWSER_JSON env var — full JSON override
    raw = os.environ.get(_ENV_VAR)
    if raw:
        try:
            cfg.update(json.loads(raw))
        except Exception:
            logger.warning("failed to parse %s: %s", _ENV_VAR, raw, exc_info=True)

    return cfg


def load_browser_config(**overrides: Any) -> Dict[str, Any]:
    """Load Browser kwargs from config files, env vars, and explicit overrides.

    Priority (lowest → highest):
      1. Defaults (``headless=True``)
      2. ~/.bridgic/bridgic-browser/bridgic-browser.json  — user persistent config
      3. ./bridgic-browser.json           — project-local config
      4. BRIDGIC_BROWSER_JSON env var     — runtime override (full JSON)
      5. ``**overrides``                  — explicit keyword arguments

    Parameters
    ----------
    **overrides
        Keyword arguments that override all other sources.
        These have the highest priority.

    Returns
    -------
    Dict[str, Any]
        Merged kwargs suitable for ``Browser(**kwargs)``.
    """
    kwargs: Dict[str, Any] = {"headless": True}

    kwargs.update(_load_config_sources())

    # Explicit overrides (highest priority)
    kwargs.update(overrides)

    # Post-processing: headed mode defaults
    if kwargs.get("headless") is False:
        kwargs.setdefault("chromium_sandbox", True)

    return kwargs
