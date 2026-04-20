"""Internal error-raising helpers.

Thin wrappers around the ``BridgicBrowserError`` hierarchy that strip
Playwright's "Call Log:" appendix from messages and short-circuit when an
existing bridgic error is already in-flight (so callers don't re-wrap their
own exceptions).
"""

import sys
from typing import Any, Dict, NoReturn, Optional

from ..errors import (
    BridgicBrowserError,
    InvalidInputError,
    OperationError,
    StateError,
    VerificationError,
)


def _strip_playwright_call_log(message: str) -> str:
    marker = "Call Log:"
    idx = message.find(marker)
    if idx == -1:
        marker = "Call log:"
        idx = message.find(marker)
    if idx == -1:
        return message
    return message[:idx].rstrip()


def _raise_invalid_input(
    message: str,
    *,
    code: str = "INVALID_INPUT",
    details: Optional[Dict[str, Any]] = None,
    retryable: bool = False,
) -> NoReturn:
    raise InvalidInputError(
        message,
        code=code,
        details=details,
        retryable=retryable,
    )


def _raise_state_error(
    message: str,
    *,
    code: str = "INVALID_STATE",
    details: Optional[Dict[str, Any]] = None,
    retryable: bool = True,
) -> NoReturn:
    raise StateError(
        message,
        code=code,
        details=details,
        retryable=retryable,
    )


def _raise_operation_error(
    message: str,
    *,
    code: str = "OPERATION_FAILED",
    details: Optional[Dict[str, Any]] = None,
    retryable: bool = False,
) -> NoReturn:
    current_exc = sys.exc_info()[1]
    if isinstance(current_exc, BridgicBrowserError):
        raise current_exc

    message = _strip_playwright_call_log(message)
    raise OperationError(
        message,
        code=code,
        details=details,
        retryable=retryable,
    )


def _raise_verification_error(
    message: str,
    *,
    code: str = "VERIFICATION_FAILED",
    details: Optional[Dict[str, Any]] = None,
    retryable: bool = False,
) -> NoReturn:
    current_exc = sys.exc_info()[1]
    if isinstance(current_exc, BridgicBrowserError):
        raise current_exc

    message = _strip_playwright_call_log(message)
    raise VerificationError(
        message,
        code=code,
        details=details,
        retryable=retryable,
    )
