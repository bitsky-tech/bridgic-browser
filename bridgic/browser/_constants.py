"""
Bridgic Browser — shared constants.

Central place for values referenced across multiple modules.
"""
from enum import Enum
from pathlib import Path

# Root directory for all Bridgic user data: ~/.bridgic
BRIDGIC_HOME = Path.home() / ".bridgic"

# Product-specific directory: ~/.bridgic/bridgic-browser
BRIDGIC_BROWSER_HOME = BRIDGIC_HOME / "bridgic-browser"

# Default directory for temporary files (video recordings, etc.)
BRIDGIC_TMP_DIR = BRIDGIC_BROWSER_HOME / "tmp"

# Default directory for snapshot overflow files
BRIDGIC_SNAPSHOT_DIR = BRIDGIC_BROWSER_HOME / "snapshot"


class ToolCategory(Enum):
    """Browser tool categories.

    Each member's *value* is the human-readable section title used in
    ``bridgic-browser -h`` output.  The member *name* (upper-case) is the
    stable identifier used in code / SDK APIs.
    """

    NAVIGATION = "Navigation"
    SNAPSHOT = "Snapshot"
    ELEMENT_INTERACTION = "Element Interaction"
    TABS = "Tabs"
    EVALUATE = "Evaluate"
    KEYBOARD = "Keyboard"
    MOUSE = "Mouse"
    WAIT = "Wait"
    CAPTURE = "Capture"
    NETWORK = "Network"
    DIALOG = "Dialog"
    STORAGE = "Storage"
    VERIFY = "Verify"
    DEVELOPER = "Developer"
    LIFECYCLE = "Lifecycle"
    ALL = "__all__"
