# Logging Configuration

This package provides a simple logging configuration system with configurable log levels and standardized format.

## Quick Start

Logging is **not** auto-configured on import. Configure it explicitly once at startup:

```python
from bridgic.browser import configure_logging

configure_logging()  # Uses BRIDGIC_LOG_LEVEL or INFO by default
```

## Configuration

### Via Environment Variable

Set the `BRIDGIC_LOG_LEVEL` environment variable and call `configure_logging()`:

```bash
export BRIDGIC_LOG_LEVEL=DEBUG
python your_script.py
```

```python
from bridgic.browser import configure_logging

configure_logging()  # Reads BRIDGIC_LOG_LEVEL
```

Supported levels: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`

### Via Code

```python
from bridgic.browser import configure_logging

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
[%(asctime)s.%(msecs)03d] [%(levelname)-5s] [%(filename)s:%(lineno)d] %(message)s
```

Example output:
```
[2026-01-28 10:30:45.123] [INFO ] [_browser.py:321] Starting playwright
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
