# Logging Configuration

This package provides a simple logging configuration system with configurable log levels and standardized format.

## Quick Start

The logging is automatically configured when you import the package:

```python
import bridgic.browser  # Logging is configured automatically
```

## Configuration

### Via Environment Variable

Set the `BRIDGIC_LOG_LEVEL` environment variable:

```bash
export BRIDGIC_LOG_LEVEL=DEBUG
python your_script.py
```

Supported levels: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`

### Via Code

```python
from bridgic.browser.utils import configure_logging

# Set log level
configure_logging(level="DEBUG")

# Custom format
configure_logging(
    level="INFO",
    format_string="%(levelname)s: %(message)s"
)
```

## Default Format

The default log format is:
```
%(asctime)s [%(levelname)-8s] %(name)s: %(message)s
```

Example output:
```
2026-01-28 10:30:45 [INFO    ] bridgic.browser.session._browser: Starting playwright
```

## Usage in Code

Use standard Python logging in your code:

```python
import logging

logger = logging.getLogger(__name__)

logger.debug("Debug message")
logger.info("Info message")
logger.warning("Warning message")
logger.error("Error message")
```
