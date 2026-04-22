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

from ._constants import BRIDGIC_HOME, BRIDGIC_BROWSER_HOME

logger = logging.getLogger(__name__)


class ConfigValidationError(ValueError):
    """Raised when a config file contains a value of the wrong type."""


# Type expectations for well-known top-level config keys. Only keys listed
# here are validated — anything else is passed through untouched so users
# can still experiment with new Playwright options without touching this
# table. Tuple values allow "bool-or-int", "str-or-dict" etc.
_EXPECTED_TYPES: Dict[str, Any] = {
    "headless": bool,
    "stealth": bool,
    "clear_user_data": bool,
    "user_data_dir": str,
    "cdp": str,
    "channel": str,
    "executable_path": str,
    "timeout": (int, float),
    "slow_mo": (int, float),
    "devtools": bool,
    "user_agent": str,
    "locale": str,
    "timezone_id": str,
    "ignore_https_errors": bool,
    "offline": bool,
    "color_scheme": str,
    "chromium_sandbox": bool,
    "downloads_path": str,
    "args": list,
    "ignore_default_args": (bool, list),
    "viewport": dict,
    "proxy": dict,
    "extra_http_headers": dict,
}


def _type_name(expected: Any) -> str:
    if isinstance(expected, tuple):
        return " | ".join(t.__name__ for t in expected)
    return expected.__name__


def _validate_config_entry(key: str, value: Any) -> None:
    expected = _EXPECTED_TYPES.get(key)
    if expected is None:
        return
    # Allow explicit nulls to clear an option; downstream Browser() treats
    # None as "unset".
    if value is None:
        return
    # Python bool is a subclass of int — exclude that pitfall for int-typed
    # config entries so `"timeout": true` is still rejected.
    if expected in (int, float) or (isinstance(expected, tuple) and int in expected):
        if isinstance(value, bool):
            raise ConfigValidationError(
                f"Config '{key}' must be {_type_name(expected)}, got bool"
            )
    if not isinstance(value, expected):
        raise ConfigValidationError(
            f"Config '{key}' must be {_type_name(expected)}, "
            f"got {type(value).__name__}"
        )


def _validate_config_schema(cfg: Dict[str, Any]) -> None:
    """Validate top-level config keys against the known schema.

    Raises :class:`ConfigValidationError` on the first mismatch; the caller
    is expected to surface the error early (at Browser() construction)
    rather than let a miscast value propagate into Playwright later and
    produce a confusing runtime failure.
    """
    for key, value in cfg.items():
        _validate_config_entry(key, value)

# Old config path (pre-0.0.3): ~/.bridgic/bridgic-browser.json
_LEGACY_CONFIG_PATH = BRIDGIC_HOME / "bridgic-browser.json"

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

    # Warn if legacy config path exists (moved in 0.0.3)
    if _LEGACY_CONFIG_PATH.is_file():
        new_path = BRIDGIC_BROWSER_HOME / _CONFIG_FILENAME
        logger.warning(
            "Found config at deprecated path %s — "
            "please move it to %s",
            _LEGACY_CONFIG_PATH, new_path,
        )

    # 1. User persistent config: ~/.bridgic/bridgic-browser/bridgic-browser.json
    user_cfg = BRIDGIC_BROWSER_HOME / _CONFIG_FILENAME
    if user_cfg.is_file():
        try:
            parsed = json.loads(user_cfg.read_text(encoding="utf-8"))
            if not isinstance(parsed, dict):
                logger.warning("user config %s: expected JSON object, got %s", user_cfg, type(parsed).__name__)
            else:
                cfg.update(parsed)
        except Exception:
            logger.warning("failed to parse user config %s", user_cfg, exc_info=True)

    # 2. Project-local config: ./bridgic-browser.json
    local_cfg = Path(_CONFIG_FILENAME)
    if local_cfg.is_file():
        try:
            parsed = json.loads(local_cfg.read_text(encoding="utf-8"))
            if not isinstance(parsed, dict):
                logger.warning("local config %s: expected JSON object, got %s", local_cfg, type(parsed).__name__)
            else:
                cfg.update(parsed)
        except Exception:
            logger.warning("failed to parse local config %s", local_cfg, exc_info=True)

    # 3. BRIDGIC_BROWSER_JSON env var — full JSON override
    raw = os.environ.get(_ENV_VAR)
    if raw:
        try:
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                logger.warning("%s: expected JSON object, got %s", _ENV_VAR, type(parsed).__name__)
            else:
                cfg.update(parsed)
        except Exception:
            logger.warning("failed to parse %s: %s", _ENV_VAR, raw, exc_info=True)

    # Surface config schema errors as early as possible (at Browser()
    # construction) instead of letting them turn into Playwright runtime
    # errors deep inside _start().
    _validate_config_schema(cfg)

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
