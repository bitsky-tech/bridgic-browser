"""
Bridgic Browser — shared constants.

Central place for values referenced across multiple modules.
"""
from enum import Enum
from pathlib import Path

# Root directory for all Bridgic user data: ~/.bridgic
BRIDGIC_HOME = Path.home() / ".bridgic"

# Default directory for temporary files (video recordings, etc.)
BRIDGIC_TMP_DIR = BRIDGIC_HOME / "tmp"


class ToolPreset(str, Enum):
    """Pre-defined tool presets for common use cases.

    Shared by both the Python SDK (BrowserToolSetBuilder) and the CLI.
    Each preset is designed for a specific scenario, containing only
    the tools necessary for that use case.
    """

    MINIMAL = "minimal"
    """Absolute minimum: navigate, click, input, snapshot"""

    NAVIGATION = "navigation"
    """Navigation only: search, navigate, back/forward"""

    SCRAPING = "scraping"
    """Web scraping: navigation + snapshot + scroll"""

    FORM_FILLING = "form_filling"
    """Form automation: navigation + input + dropdown + checkbox"""

    TESTING = "testing"
    """E2E testing: form_filling + verification + screenshot"""

    INTERACTIVE = "interactive"
    """Full interaction: all action tools + mouse + keyboard"""

    DEVELOPER = "developer"
    """Developer tools: network + console + tracing"""

    COMPLETE = "complete"
    """All available tools — use sparingly"""
