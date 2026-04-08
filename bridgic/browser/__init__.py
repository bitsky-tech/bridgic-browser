# Core module initialization

from importlib.metadata import version

from .utils._logging import configure_logging
from .session._browser import Browser, find_cdp_url, resolve_cdp_input
from .session._snapshot import EnhancedSnapshot, RefData, SnapshotGenerator, SnapshotOptions
from .session._browser_model import PageDesc, PageInfo, PageSizeInfo, FullPageInfo
from .session._stealth import StealthConfig, StealthArgsBuilder, create_stealth_config
from .session._download import DownloadManager, DownloadManagerConfig, DownloadedFile
from .errors import (
    BridgicBrowserError,
    BridgicBrowserCommandError,
    InvalidInputError,
    StateError,
    OperationError,
    VerificationError,
)
from .tools import BrowserToolSetBuilder, BrowserToolSpec, ToolCategory
from ._config import load_browser_config
from ._constants import BRIDGIC_HOME, BRIDGIC_BROWSER_HOME, BRIDGIC_TMP_DIR, BRIDGIC_SNAPSHOT_DIR, BRIDGIC_USER_DATA_DIR
from .cli._commands import SectionedGroup

__version__ = version("bridgic-browser")
__all__ = [
    "__version__",
    "configure_logging",
    # Browser session
    "Browser",
    "find_cdp_url",
    "resolve_cdp_input",
    # Snapshot types
    "EnhancedSnapshot",
    "RefData",
    "SnapshotGenerator",
    "SnapshotOptions",
    # Page model types
    "PageDesc",
    "PageInfo",
    "PageSizeInfo",
    "FullPageInfo",
    # Stealth
    "StealthConfig",
    "StealthArgsBuilder",
    "create_stealth_config",
    # Download
    "DownloadManager",
    "DownloadManagerConfig",
    "DownloadedFile",
    # Errors
    "BridgicBrowserError",
    "BridgicBrowserCommandError",
    "InvalidInputError",
    "StateError",
    "OperationError",
    "VerificationError",
    # Tools
    "BrowserToolSetBuilder",
    "BrowserToolSpec",
    "ToolCategory",
    # Config
    "load_browser_config",
    # Constants
    "BRIDGIC_HOME",
    "BRIDGIC_BROWSER_HOME",
    "BRIDGIC_TMP_DIR",
    "BRIDGIC_SNAPSHOT_DIR",
    "BRIDGIC_USER_DATA_DIR",
    # CLI
    "SectionedGroup",
]
