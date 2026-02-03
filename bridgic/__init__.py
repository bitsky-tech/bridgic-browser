# bridgic namespace package - allows multiple packages to contribute to the bridgic namespace
# bridgic-browser provides bridgic.browser
# bridgic-core provides bridgic.core

# PEP 420 compatible namespace package using pkgutil
__path__ = __import__("pkgutil").extend_path(__path__, __name__)
