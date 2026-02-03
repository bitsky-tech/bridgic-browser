"""Logging configuration for bridgic-browser.

This module provides a simple logging setup with configurable log level
and standardized format.
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Optional


def configure_logging(
    level: Optional[str] = None,
    format_string: Optional[str] = None,
) -> None:
    """Configure logging for bridgic-browser package.

    Parameters
    ----------
    level : Optional[str], optional
        Log level name (e.g., "DEBUG", "INFO", "WARNING", "ERROR").
        Defaults to `BRIDGIC_LOG_LEVEL` environment variable, or "INFO".
    format_string : Optional[str], optional
        Custom log format string. If not provided, uses a standard format.
        Default format includes: timestamp, level, file:line, and message.

    Notes
    -----
    This function configures the ``bridgic`` logger namespace (e.g. loggers whose
    names start with ``bridgic.``) instead of the global root logger, so that
    setting ``BRIDGIC_LOG_LEVEL`` 不会影响到宿主应用或其他第三方库的日志输出。

    Examples
    --------
    >>> configure_logging(level="DEBUG")
    >>> configure_logging(level="INFO", format_string="%(levelname)s: %(message)s")
    """
    # Get log level from parameter or environment variable
    if level is None:
        level = os.getenv("BRIDGIC_LOG_LEVEL", "INFO").upper()
    
    # Validate and set log level
    numeric_level = getattr(logging, level.upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError(f"Invalid log level: {level}")
    
    # Default format (common/clean): timestamp(ms), level, file:line, message
    if format_string is None:
        # Format with brackets for clarity:
        # [2026-01-28 15:49:29.123] [INFO] [_browser.py:321] Page size info: ...
        format_string = "[%(asctime)s.%(msecs)03d] [%(levelname)-5s] [%(filename)s:%(lineno)d] %(message)s"
    
    # Configure bridgic package logger (do not touch root logger to avoid
    # affecting host application and third‑party libraries).
    logger = logging.getLogger("bridgic.browser")
    logger.setLevel(numeric_level)
    # Prevent double logging if the host app configures root handlers.
    logger.propagate = False

    # Remove existing handlers on bridgic.browser logger to avoid duplicates
    logger.handlers.clear()

    # Create console handler
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(numeric_level)
    
    # Set formatter
    formatter = logging.Formatter(
        format_string,
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    handler.setFormatter(formatter)

    # Add handler to bridgic logger
    logger.addHandler(handler)
