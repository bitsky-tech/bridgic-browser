# Core module initialization

from importlib.metadata import version

from .utils._logging import configure_logging
from .session._browser import Browser
from .errors import (
    BridgicBrowserError,
    BridgicBrowserCommandError,
    InvalidInputError,
    StateError,
    OperationError,
    VerificationError,
)
from .tools import BrowserToolSetBuilder, BrowserToolSpec, ToolCategory
from ._constants import BRIDGIC_HOME, BRIDGIC_TMP_DIR

__version__ = version("bridgic-browser")
__all__ = [
    "__version__",
    "configure_logging",
    "Browser",
    "BridgicBrowserError",
    "BridgicBrowserCommandError",
    "InvalidInputError",
    "StateError",
    "OperationError",
    "VerificationError",
    "BrowserToolSetBuilder",
    "BrowserToolSpec",
    "ToolCategory",
    "BRIDGIC_HOME",
    "BRIDGIC_TMP_DIR",
]
