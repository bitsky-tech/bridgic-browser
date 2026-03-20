"""Structured error types for bridgic-browser.

These exceptions form the stable error protocol used across SDK, daemon,
client, and CLI layers.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


class BridgicBrowserError(RuntimeError):
    """Base error for all structured bridgic-browser failures."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.retryable = retryable

    def to_dict(self) -> Dict[str, Any]:
        """Serialize this error to a JSON-safe dictionary."""
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
            "retryable": self.retryable,
        }


class InvalidInputError(BridgicBrowserError):
    """Raised when caller input is invalid."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "INVALID_INPUT",
        details: Optional[Dict[str, Any]] = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(
            code=code,
            message=message,
            details=details,
            retryable=retryable,
        )


class StateError(BridgicBrowserError):
    """Raised when browser/page state is not ready for an operation."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "INVALID_STATE",
        details: Optional[Dict[str, Any]] = None,
        retryable: bool = True,
    ) -> None:
        super().__init__(
            code=code,
            message=message,
            details=details,
            retryable=retryable,
        )


class OperationError(BridgicBrowserError):
    """Raised when an operation fails unexpectedly."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "OPERATION_FAILED",
        details: Optional[Dict[str, Any]] = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(
            code=code,
            message=message,
            details=details,
            retryable=retryable,
        )


class VerificationError(BridgicBrowserError):
    """Raised when an assertion/verification fails."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "VERIFICATION_FAILED",
        details: Optional[Dict[str, Any]] = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(
            code=code,
            message=message,
            details=details,
            retryable=retryable,
        )


class BridgicBrowserCommandError(BridgicBrowserError):
    """Raised by CLI client when daemon returns a structured command failure."""

    def __init__(
        self,
        *,
        command: str,
        code: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
        retryable: bool = False,
        daemon_meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            code=code,
            message=message,
            details=details,
            retryable=retryable,
        )
        self.command = command
        self.daemon_meta = daemon_meta or {}
