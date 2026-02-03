# Core module initialization

from importlib.metadata import version

from .utils._logging import configure_logging

# Auto-configure logging on package import
configure_logging()

__version__ = version("bridgic-browser")
__all__ = ["__version__", "configure_logging"]