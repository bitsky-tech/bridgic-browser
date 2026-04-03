import asyncio
import base64
import json
import logging
import os
import signal
import sys
import tempfile
from urllib.parse import urlparse
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional, Sequence, Union, NoReturn

if TYPE_CHECKING:
    try:
        from bridgic.llms.openai import OpenAILlm  # pyright: ignore[reportMissingImports]
    except ModuleNotFoundError:  # pragma: no cover - optional dependency
        OpenAILlm = Any  # type: ignore[misc,assignment]

from .._constants import BRIDGIC_TMP_DIR, BRIDGIC_SNAPSHOT_DIR, BRIDGIC_USER_DATA_DIR

from playwright.async_api import (
    async_playwright,
    Playwright,
    Browser as PlaywrightBrowser,
    BrowserContext,
    Page,
    Locator,
    ProxySettings,
)
from pydantic import BaseModel

from ._snapshot import EnhancedSnapshot, SnapshotGenerator, SnapshotOptions
from ._browser_model import FullPageInfo, PageDesc, PageInfo, PageSizeInfo
from ._stealth import StealthConfig, StealthArgsBuilder
from ._download import DownloadManager, DownloadedFile
from ..utils import find_page_by_id, generate_page_id, model_to_llm_string
from ..errors import (
    BridgicBrowserError,
    InvalidInputError,
    OperationError,
    StateError,
    VerificationError,
)

logger = logging.getLogger(__name__)

_DEFAULT_SNAPSHOT_LIMIT = 10000

_LAUNCH_DEBUG_LOG = str(BRIDGIC_TMP_DIR / "launch-debug.json")


def _detect_system_chrome() -> bool:
    """Check if system Google Chrome is installed.

    Used to auto-switch from Playwright's bundled "Chrome for Testing" (which
    Google blocks for OAuth login) to the real system Chrome in headed mode.
    """
    if sys.platform == "darwin":
        return os.path.isfile(
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        )
    elif sys.platform == "linux":
        import shutil
        return (
            shutil.which("google-chrome") is not None
            or shutil.which("google-chrome-stable") is not None
        )
    elif sys.platform == "win32":
        for env_var in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
            base = os.environ.get(env_var, "")
            if base:
                path = os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")
                if os.path.isfile(path):
                    return True
    return False


def _write_launch_debug_log(options: Dict[str, Any], mode: str) -> None:
    """Write Chrome launch args to launch-debug.json for debugging."""
    import datetime, json as _json
    try:
        os.makedirs(os.path.dirname(_LAUNCH_DEBUG_LOG), exist_ok=True)
        record = {
            "time": datetime.datetime.now().isoformat(),
            "mode": mode,
            "args": options.get("args", []),
            "ignore_default_args": options.get("ignore_default_args", []),
            "headless": options.get("headless"),
            "channel": options.get("channel"),
            "executable_path": str(options["executable_path"]) if options.get("executable_path") else None,
        }
        with open(_LAUNCH_DEBUG_LOG, "w", encoding="utf-8") as f:
            _json.dump(record, f, indent=2)
    except Exception as e:
        logger.warning("Failed to write launch debug log: %s", e)


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

def _get_page_key(page) -> str:
    """Get a unique key for a page."""
    return str(id(page))


def _get_context_key(context) -> str:
    """Get a unique key for a context."""
    return str(id(context))


def _css_attr_equals(name: str, value: str) -> str:
    """Build a CSS attribute selector with basic quote escaping."""
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"[{name}='{escaped}']"


async def _prefer_visible_locators(locators: list) -> list:
    """Keep only visible locators when possible, otherwise preserve original order."""
    visible = []
    for locator in locators:
        try:
            if await locator.is_visible():
                visible.append(locator)
        except Exception:
            continue
    return visible or locators


async def _get_dropdown_option_locators(page, locator) -> list:
    """Resolve option locators for native, embedded, and portalized dropdowns."""
    options = await locator.locator("option").all()
    if options:
        return options

    options = await locator.locator("[role='option']").all()
    if options:
        return await _prefer_visible_locators(options)

    if page is None:
        return []

    # Portalized dropdowns often link the trigger to the listbox via aria-controls
    # or aria-owns. Prefer that container before scanning the whole page.
    controlled_ids = []
    for attr_name in ("aria-controls", "aria-owns"):
        attr_value = await locator.get_attribute(attr_name)
        if attr_value:
            controlled_ids.extend(part for part in attr_value.split() if part)

    for controlled_id in controlled_ids:
        container = page.locator(_css_attr_equals("id", controlled_id))
        if await container.count() > 0:
            options = await container.locator("option, [role='option']").all()
            if options:
                return await _prefer_visible_locators(options)

    # Conservative fallback: if exactly one visible listbox is open, use it.
    listboxes = await page.locator("[role='listbox']").all()
    visible_listboxes = await _prefer_visible_locators(listboxes)
    if len(visible_listboxes) == 1:
        options = await visible_listboxes[0].locator("option, [role='option']").all()
        if options:
            return await _prefer_visible_locators(options)

    return []


async def _is_native_checkbox_or_radio(locator) -> bool:
    """Return True when locator points to <input type=checkbox|radio>."""
    try:
        tag_name = await locator.evaluate("el => el.tagName.toLowerCase()")
    except Exception:
        return False
    if tag_name != "input":
        return False
    input_type = (await locator.get_attribute("type") or "").strip().lower()
    return input_type in {"checkbox", "radio"}


async def _is_checked(locator) -> bool:
    """Check both native .checked and aria-checked state."""
    return bool(
        await locator.evaluate(
            "el => el.checked === true || el.getAttribute('aria-checked') === 'true'"
        )
    )


async def _click_checkable_target(page, locator, bbox) -> None:
    """Click a checkable target with overlay handling and shadow DOM fallback."""
    if bbox is not None:
        cx = bbox["x"] + bbox["width"] / 2
        cy = bbox["y"] + bbox["height"] / 2
        if not await locator.is_visible():
            logger.debug("_click_checkable_target: bbox present but is_visible()=False; using dispatch_event click")
            await locator.dispatch_event("click")
            return

        covered = await locator.evaluate(
            f"(el) => {{ if (window.parent !== window) return false; "
            f"const t = document.elementFromPoint({cx}, {cy}); "
            f"return !!t && t !== el && !el.contains(t) && !t.contains(el); }}"
        )
        if covered:
            logger.debug("_click_checkable_target: covered at (%.1f, %.1f), clicking intercepting element", cx, cy)
            if page:
                await page.evaluate(f"document.elementFromPoint({cx}, {cy})?.click()")
            else:
                await locator.dispatch_event("click")
        else:
            await locator.click()
        return

    if await locator.is_visible():
        await locator.click()
    else:
        logger.debug("_click_checkable_target: no bbox and is_visible()=False; using dispatch_event click")
        await locator.dispatch_event("click")


# Type aliases for Playwright types
ViewportSize = Dict[str, int]  # {"width": int, "height": int}
Geolocation = Dict[str, float]  # {"latitude": float, "longitude": float, "accuracy"?: float}
HttpCredentials = Dict[str, Any]  # {"username": str, "password": str, ...}
ClientCertificate = Dict[str, Any]


class Browser:
    """Browser wrapper for Playwright with automatic launch mode selection.

    Automatically loads configuration from config files and environment
    variables on instantiation (same priority chain as the ``bridgic-browser``
    CLI): ``~/.bridgic/bridgic-browser/bridgic-browser.json`` → ``./bridgic-browser.json`` →
    ``BRIDGIC_BROWSER_JSON`` env var. Explicit constructor parameters override
    config values.

    This class automatically chooses between ``launch_persistent_context`` and
    ``launch`` + ``new_context`` based on the ``clear_user_data`` parameter.

    - ``clear_user_data=False`` (default): Uses ``launch_persistent_context`` for
      session persistence. Uses the explicit ``user_data_dir`` if provided, otherwise
      defaults to ``~/.bridgic/bridgic-browser/user_data/``.
    - ``clear_user_data=True``: Uses ``launch`` + ``new_context`` for ephemeral sessions
      (no persistent profile; ``user_data_dir`` is ignored).

    Parameters
    ----------
    headless : bool, optional
        Whether to run browser in headless mode. Defaults to None (resolved
        from config files or True if no config present).
    viewport : ViewportSize, optional
        Viewport size. Defaults to {"width": 1600, "height": 900}.
    user_data_dir : str | Path, optional
        Path to user data directory for persistent context. Only used when
        ``clear_user_data=False`` (the default). When not provided, defaults to
        ``~/.bridgic/bridgic-browser/user_data/``. Ignored when ``clear_user_data=True``.
    clear_user_data : bool, optional
        If True, start an ephemeral browser session (``launch`` + ``new_context``,
        no persistent profile; ``user_data_dir`` is ignored). If False (default),
        use ``launch_persistent_context`` with a persistent profile. Defaults to
        None (resolved from config files or False if no config present).
    stealth : bool | StealthConfig, optional
        Stealth mode for bypassing bot detection. Defaults to None (resolved
        from config files or True if no config present).
        - True: Enable stealth with optimal StealthConfig
        - False: Disable stealth mode completely
        - StealthConfig: Custom stealth configuration

        Stealth mode includes:
        - 50+ Chrome args to disable automation detection
        - Ignoring Playwright's automation-revealing default args
    channel : str, optional
        Browser distribution channel. Use "chrome", "chrome-beta", "msedge", etc.
        for branded browsers, or "chromium" for new headless mode.
    executable_path : str | Path, optional
        Path to a browser executable to run instead of the bundled one.
    proxy : ProxySettings, optional
        Network proxy settings: {"server": str, "bypass"?: str, "username"?: str, "password"?: str}.
    timeout : float, optional
        Maximum time in seconds to wait for browser to start. Default 30.
    slow_mo : float, optional
        Slows down Playwright operations by specified milliseconds. Useful for debugging.
    args : Sequence[str], optional
        Additional arguments to pass to the browser instance.
    ignore_default_args : bool | Sequence[str], optional
        If True, only use custom args. If array, filter out specified default args.
    downloads_path : str | Path, optional
        Directory for accepted downloads.
    devtools : bool, optional
        **Chromium-only** Auto-open Developer Tools panel. Sets headless=False.
    user_agent : str, optional
        Specific user agent string for this context.
    locale : str, optional
        User locale (e.g., "en-GB", "de-DE"). Affects navigator.language.
    timezone_id : str, optional
        Timezone ID (e.g., "America/New_York"). Affects Date/time functions.
    ignore_https_errors : bool, optional
        Whether to ignore HTTPS errors. Default False.
    extra_http_headers : Dict[str, str], optional
        Additional HTTP headers sent with every request.
    offline : bool, optional
        Emulate network being offline. Default False.
    color_scheme : Literal["dark", "light", "no-preference", "null"], optional
        Emulates prefers-color-scheme media feature. Default "light".
    **kwargs : Any
        Additional Playwright launch/context parameters. These are passed directly
        to the underlying Playwright methods.

        For `launch` mode, additional options include:
        - handle_sigint, handle_sigterm, handle_sighup: Signal handling
        - env: Environment variables for browser
        - traces_dir: Directory for traces
        - chromium_sandbox: Enable Chromium sandboxing
        - firefox_user_prefs: Firefox user preferences

        For `launch_persistent_context` mode, additional options include all
        launch options plus context options:
        - screen, no_viewport: Screen/viewport settings
        - java_script_enabled, bypass_csp: JS and CSP settings
        - geolocation, permissions: Location and permissions
        - http_credentials: HTTP authentication
        - device_scale_factor, is_mobile, has_touch: Device emulation
        - reduced_motion, forced_colors, contrast: Accessibility
        - accept_downloads: Auto-accept downloads
        - record_har_*, record_video_*: Recording options
        - base_url, strict_selectors, service_workers: Navigation/selector options
        - client_certificates: TLS client authentication

    Examples
    --------
    # Default: headless with stealth (stealth is ON by default)
    >>> browser = Browser()  # stealth=True, headless=True

    # Non-headless with stealth
    >>> browser = Browser(headless=False)

    # Persistent session with stealth
    >>> browser = Browser(
    ...     headless=False,
    ...     user_data_dir="~/.browser_data",
    ...     channel="chrome",
    ... )

    # With proxy and custom viewport
    >>> browser = Browser(
    ...     viewport={"width": 1280, "height": 720},
    ...     proxy={"server": "http://proxy:8080"},
    ... )

    # Mobile emulation
    >>> browser = Browser(
    ...     viewport={"width": 375, "height": 812},
    ...     user_agent="Mozilla/5.0 (iPhone; ...)",
    ...     is_mobile=True,
    ...     has_touch=True,
    ... )

    # Disable stealth if needed
    >>> browser = Browser(stealth=False)

    # Custom stealth config
    >>> browser = Browser(
    ...     stealth=StealthConfig(
    ...         disable_security=True,    # For testing only
    ...     ),
    ... )
    """

    def __init__(
        self,
        # === Common frequently used parameters ===
        headless: Optional[bool] = None,
        viewport: Optional[ViewportSize] = None,
        user_data_dir: Optional[Union[str, Path]] = None,
        clear_user_data: Optional[bool] = None,
        # === Stealth mode (enabled by default for best anti-detection) ===
        stealth: Union[bool, StealthConfig, None] = None,
        # === Browser launch parameters (commonly used) ===
        channel: Optional[str] = None,
        executable_path: Optional[Union[str, Path]] = None,
        proxy: Optional[ProxySettings] = None,
        timeout: Optional[float] = None,
        slow_mo: Optional[float] = None,
        args: Optional[Sequence[str]] = None,
        ignore_default_args: Optional[Union[bool, Sequence[str]]] = None,
        downloads_path: Optional[Union[str, Path]] = None,
        devtools: Optional[bool] = None,
        # === Context parameters (commonly used) ===
        user_agent: Optional[str] = None,
        locale: Optional[str] = None,
        timezone_id: Optional[str] = None,
        ignore_https_errors: Optional[bool] = None,
        extra_http_headers: Optional[Dict[str, str]] = None,
        offline: Optional[bool] = None,
        color_scheme: Optional[Literal["dark", "light", "no-preference", "null"]] = None,
        # === All other parameters via kwargs ===
        **kwargs: Any,
    ):
        # --- Load config from files and environment ---
        from .._config import _load_config_sources
        _cfg = _load_config_sources()

        # Resolve parameters: explicit (non-None) > config > default.
        # Always pop named-param keys from _cfg so they don't leak into
        # _extra_kwargs (which would corrupt get_config() and Playwright options).
        headless = headless if headless is not None else _cfg.pop('headless', True)
        stealth = stealth if stealth is not None else _cfg.pop('stealth', True)
        viewport = viewport if viewport is not None else _cfg.pop('viewport', None)
        user_data_dir = user_data_dir if user_data_dir is not None else _cfg.pop('user_data_dir', None)
        clear_user_data = clear_user_data if clear_user_data is not None else _cfg.pop('clear_user_data', False)
        channel = channel if channel is not None else _cfg.pop('channel', None)
        executable_path = executable_path if executable_path is not None else _cfg.pop('executable_path', None)
        proxy = proxy if proxy is not None else _cfg.pop('proxy', None)
        timeout = timeout if timeout is not None else _cfg.pop('timeout', None)
        slow_mo = slow_mo if slow_mo is not None else _cfg.pop('slow_mo', None)
        args = args if args is not None else _cfg.pop('args', None)
        ignore_default_args = ignore_default_args if ignore_default_args is not None else _cfg.pop('ignore_default_args', None)
        downloads_path = downloads_path if downloads_path is not None else _cfg.pop('downloads_path', None)
        devtools = devtools if devtools is not None else _cfg.pop('devtools', None)
        user_agent = user_agent if user_agent is not None else _cfg.pop('user_agent', None)
        locale = locale if locale is not None else _cfg.pop('locale', None)
        timezone_id = timezone_id if timezone_id is not None else _cfg.pop('timezone_id', None)
        ignore_https_errors = ignore_https_errors if ignore_https_errors is not None else _cfg.pop('ignore_https_errors', None)
        extra_http_headers = extra_http_headers if extra_http_headers is not None else _cfg.pop('extra_http_headers', None)
        offline = offline if offline is not None else _cfg.pop('offline', None)
        color_scheme = color_scheme if color_scheme is not None else _cfg.pop('color_scheme', None)
        # Remove any named-param keys that were skipped above (explicit value won)
        for _named_key in (
            'headless', 'stealth', 'viewport', 'user_data_dir', 'clear_user_data', 'channel',
            'executable_path', 'proxy', 'timeout', 'slow_mo', 'args',
            'ignore_default_args', 'downloads_path', 'devtools', 'user_agent',
            'locale', 'timezone_id', 'ignore_https_errors', 'extra_http_headers',
            'offline', 'color_scheme',
        ):
            _cfg.pop(_named_key, None)

        # Merge remaining config into kwargs (pass-through params like chromium_sandbox)
        for k, v in _cfg.items():
            kwargs.setdefault(k, v)

        # Headed mode: auto-set chromium_sandbox=True to prevent --no-sandbox warning
        if headless is False:
            kwargs.setdefault('chromium_sandbox', True)

        # Store all parameters
        self._headless = headless
        self._no_viewport = bool(kwargs.get("no_viewport", False))
        if devtools:
            self._headless = False
        if self._no_viewport:
            if viewport is not None:
                raise InvalidInputError(
                    "viewport must be None when no_viewport=True",
                    code="VIEWPORT_CONFLICT",
                    details={"viewport": viewport},
                )
            self._viewport = None
        else:
            self._viewport = viewport or {"width": 1600, "height": 900}
        self._user_data_dir = Path(user_data_dir).expanduser() if user_data_dir else None
        self._clear_user_data: bool = clear_user_data

        # Stealth configuration
        self._stealth_config: Optional[StealthConfig] = None
        self._stealth_builder: Optional[StealthArgsBuilder] = None
        self._temp_video_dir: Optional[str] = None  # For auto-created video dir
        self._preallocated_trace_path: Optional[str] = None
        self._close_session_dir: Optional[str] = None

        if stealth is True:
            self._stealth_config = StealthConfig()
        elif isinstance(stealth, dict):
            # Config files pass stealth as a dict (e.g. {"disable_security": true}).
            # Filter out unknown keys for backwards compatibility (e.g. removed
            # "enable_extensions" from older config files).
            import dataclasses as _dc
            _known = {f.name for f in _dc.fields(StealthConfig)}
            _filtered = {k: v for k, v in stealth.items() if k in _known}
            self._stealth_config = StealthConfig(**_filtered)
        elif isinstance(stealth, StealthConfig):
            self._stealth_config = stealth

        if self._stealth_config and self._stealth_config.enabled:
            self._stealth_builder = StealthArgsBuilder(self._stealth_config)

        # Browser launch parameters
        self._channel = channel
        self._executable_path = Path(executable_path).expanduser() if executable_path else None
        self._proxy = proxy
        self._timeout = timeout
        self._slow_mo = slow_mo
        self._args = args
        self._ignore_default_args = ignore_default_args
        self._downloads_path = Path(downloads_path).expanduser() if downloads_path else None
        self._devtools = devtools

        # Context parameters
        self._user_agent = user_agent
        self._locale = locale
        self._timezone_id = timezone_id
        self._ignore_https_errors = ignore_https_errors
        self._extra_http_headers = extra_http_headers
        self._offline = offline
        self._color_scheme = color_scheme

        # Store additional kwargs for pass-through
        self._extra_kwargs = kwargs

        # Playwright instances
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[PlaywrightBrowser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

        # Download manager - handles saving files with correct filenames
        self._download_manager: Optional[DownloadManager] = None
        if self._downloads_path:
            self._download_manager = DownloadManager(downloads_path=self._downloads_path)

        # Cache for last snapshot
        self._last_snapshot: Optional[EnhancedSnapshot] = None
        self._last_snapshot_url: Optional[str] = None
        self._snapshot_generator: Optional[SnapshotGenerator] = None
        self._snapshot_lock = asyncio.Lock()
        # Artifacts auto-saved during shutdown (trace/video)
        self._last_shutdown_artifacts: Dict[str, List[str]] = {"trace": [], "video": []}
        self._last_shutdown_errors: List[str] = []

        # Page-scoped state (keyed by _get_page_key)
        self._console_messages: Dict[str, List[Dict[str, Any]]] = {}
        self._network_requests: Dict[str, List[Dict[str, Any]]] = {}
        self._console_handlers: Dict[str, Any] = {}
        self._network_handlers: Dict[str, Any] = {}
        self._dialog_handlers: Dict[str, Any] = {}
        # Context-scoped state (keyed by _get_context_key)
        self._tracing_state: Dict[str, bool] = {}
        self._video_state: Dict[str, bool] = {}
        # Deferred video save requests from stop_video(): context_key → target filename.
        # None means save to the Playwright temp path (stop_video called without filename).
        # Key absent means stop_video was not called for this context.
        self._pending_video_save_path: Dict[str, Optional[str]] = {}

    # ==================== Properties ====================

    @property
    def use_persistent_context(self) -> bool:
        """Whether to use persistent context mode (unrelated to headless/headed mode).

        Priority (highest to lowest):
        - clear_user_data=True  → always False (fresh launch+new_context, user_data_dir ignored)
        - clear_user_data=False → always True (persistent; user_data_dir if set, else default dir)
        """
        return not self._clear_user_data

    @property
    def stealth_enabled(self) -> bool:
        """Whether stealth mode is enabled."""
        return self._stealth_config is not None and self._stealth_config.enabled

    @property
    def stealth_config(self) -> Optional[StealthConfig]:
        """Current stealth configuration, or None if stealth is disabled."""
        return self._stealth_config

    @property
    def download_manager(self) -> Optional[DownloadManager]:
        """Download manager for handling file downloads with correct filenames."""
        return self._download_manager

    @property
    def downloaded_files(self) -> List[DownloadedFile]:
        """Get list of all downloaded files in this session."""
        if self._download_manager:
            return self._download_manager.downloaded_files
        return []

    @property
    def headless(self) -> bool:
        """Whether the user requested a windowless (headless) browser.

        Reflects the *user's intent*, not the internal Playwright ``headless``
        flag.  When stealth's new-headless mode is active, Playwright receives
        ``headless=False`` internally so it selects the full Chromium binary,
        but this property still returns ``True`` because Chrome itself runs
        with ``--headless=new`` and has no visible window.
        """
        return self._headless

    @property
    def viewport(self) -> Optional[ViewportSize]:
        """Current viewport size configuration (None when no_viewport=True)."""
        return self._viewport

    @property
    def user_data_dir(self) -> Optional[Path]:
        """User data directory path, or None if not using persistent context."""
        return self._user_data_dir

    @property
    def clear_user_data(self) -> bool:
        """Whether user data is cleared on each browser start (ephemeral mode)."""
        return self._clear_user_data

    @property
    def channel(self) -> Optional[str]:
        """Browser distribution channel."""
        return self._channel

    def get_config(self) -> Dict[str, Any]:
        """Get all current browser configuration.

        Returns
        -------
        Dict[str, Any]
            Dictionary containing all browser configuration options.
        """
        config = {
            "headless": self._headless,
            "viewport": self._viewport,
            "no_viewport": self._no_viewport,
            "user_data_dir": str(self._user_data_dir) if self._user_data_dir else None,
            "clear_user_data": self._clear_user_data,
            "stealth_enabled": self.stealth_enabled,
            "channel": self._channel,
            "executable_path": str(self._executable_path) if self._executable_path else None,
            "proxy": self._proxy,
            "timeout": self._timeout,
            "slow_mo": self._slow_mo,
            "args": list(self._args) if self._args else None,
            "ignore_default_args": self._ignore_default_args,
            "downloads_path": str(self._downloads_path) if self._downloads_path else None,
            "devtools": self._devtools,
            "user_agent": self._user_agent,
            "locale": self._locale,
            "timezone_id": self._timezone_id,
            "ignore_https_errors": self._ignore_https_errors,
            "extra_http_headers": self._extra_http_headers,
            "offline": self._offline,
            "color_scheme": self._color_scheme,
            "use_persistent_context": self.use_persistent_context,
            **self._extra_kwargs,
        }
        # Remove None values for cleaner output
        return {k: v for k, v in config.items() if v is not None}

    # ==================== Internal Configuration ====================

    def _get_launch_options(self) -> Dict[str, Any]:
        """Get options for browser.launch() method.

        Merges user options with stealth options when stealth is enabled.

        Returns
        -------
        Dict[str, Any]
            Options dict for playwright.chromium.launch()
        """
        options: Dict[str, Any] = {}

        # Build args list (merge stealth args with user args)
        args_list: List[str] = []

        # When using system Chrome (channel or executable_path), skip stealth
        # Chrome args — many stealth flags cause "unsupported flag" warnings.
        # Anti-detection still works via ignore_default_args
        # (removes --enable-automation) and the JS init script (patches
        # navigator.webdriver, plugins, chrome object, etc.).
        _is_system_chrome = bool(self._channel or self._executable_path)

        # In headed mode, auto-switch to system Chrome to avoid Google blocking
        # "Chrome for Testing" (the Playwright-bundled binary) for OAuth login.
        # System Chrome shows as a normal browser in the Dock (no "test" label)
        # and passes Google's browser safety checks.
        _auto_system_chrome = (
            not self._headless
            and self.stealth_enabled
            and not _is_system_chrome
            and _detect_system_chrome()
        )
        if _auto_system_chrome:
            options["channel"] = "chrome"
            logger.info(
                "Headed mode: auto-switching to system Chrome for anti-detection "
                "(Chrome for Testing is blocked by Google OAuth)"
            )

        # Add stealth args first (if enabled).
        # When user explicitly set channel/executable_path (_is_system_chrome),
        # skip stealth args entirely (existing behaviour).
        # When auto-switched to system Chrome, still apply the minimal headed
        # stealth args (they're compatible with system Chrome).
        if self._stealth_builder and not _is_system_chrome:
            fallback_viewport = {"width": 1600, "height": 900}
            viewport = self._viewport or fallback_viewport
            viewport_width = viewport.get("width", 1600)
            viewport_height = viewport.get("height", 900)
            stealth_args = self._stealth_builder.build_args(
                viewport_width,
                viewport_height,
                headless_intent=self._headless,
                locale=self._locale,
            )
            if _auto_system_chrome:
                # System Chrome shows a "unsupported command-line flag" warning
                # banner for --disable-blink-features.  --test-type= (empty value)
                # tells Chrome to suppress all bad-flag warnings without adding
                # any web-detectable side effects.
                stealth_args.append("--test-type=")
            args_list.extend(stealth_args)

        # Add user-provided args (can override/extend stealth args)
        if self._args:
            args_list.extend(self._args)

        if args_list:
            options["args"] = args_list

        # Build ignore_default_args (merge stealth with user)
        ignore_args: List[str] = []
        if self._stealth_builder:
            ignore_args.extend(self._stealth_builder.get_ignore_default_args())

        if self._ignore_default_args is True:
            # User wants to ignore ALL default args
            options["ignore_default_args"] = True
        elif isinstance(self._ignore_default_args, (list, tuple)):
            # Merge user's ignore list with stealth ignore list
            ignore_args.extend(self._ignore_default_args)
            if ignore_args:
                options["ignore_default_args"] = list(set(ignore_args))
        elif ignore_args:
            # Only stealth ignore args
            options["ignore_default_args"] = ignore_args

        # Add non-None launch parameters
        if self._executable_path is not None:
            options["executable_path"] = self._executable_path
        if self._channel is not None:
            options["channel"] = self._channel
        if self._timeout is not None:
            options["timeout"] = self._timeout * 1000.0
        if self._headless is not None:
            # When the user wants no window + stealth is active, redirect Playwright
            # to the full chromium binary by passing headless=False.  The actual
            # "no window" behaviour comes from --headless=new added in build_args().
            #
            #   self._headless      → user intent   (hide the window?)
            #   options["headless"] → Playwright arg (which binary to pick?)
            #
            # chromium-headless-shell is a stripped binary with detectable
            # fingerprint differences; full chromium + --headless=new avoids that.
            _use_full_binary = (
                self._headless is True
                and not _is_system_chrome       # system Chrome picks its own binary
                and not _auto_system_chrome      # auto-switched system Chrome too
                and self._stealth_config is not None
                and self._stealth_config.enabled
                and self._stealth_config.use_new_headless
            )
            options["headless"] = False if _use_full_binary else self._headless
        if self._devtools is not None:
            options["devtools"] = self._devtools
        if self._proxy is not None:
            options["proxy"] = self._proxy
        # NOTE: Don't pass downloads_path to Playwright - DownloadManager handles it
        # Passing downloads_path to Playwright causes files to be saved with hash names
        # Our DownloadManager uses download.save_as() to save with correct filenames
        if self._slow_mo is not None:
            options["slow_mo"] = self._slow_mo

        # Extract launch-specific kwargs
        launch_keys = {
            "handle_sigint", "handle_sigterm", "handle_sighup",
            "env", "traces_dir", "chromium_sandbox", "firefox_user_prefs"
        }
        for key in launch_keys:
            if key in self._extra_kwargs:
                options[key] = self._extra_kwargs[key]

        return options

    def _get_context_options(self) -> Dict[str, Any]:
        """Get options for browser.new_context() method.

        Merges user options with stealth options when stealth is enabled.

        Returns
        -------
        Dict[str, Any]
            Options dict for browser.new_context()
        """
        options: Dict[str, Any] = {}

        # Add stealth context options first (if enabled)
        if self._stealth_builder:
            stealth_context_opts = self._stealth_builder.get_context_options()
            options.update(stealth_context_opts)

            # Add screen size to match viewport for correct window.screen values.
            # Fall back to a standard desktop resolution when no_viewport=True.
            if self._viewport:
                options["screen"] = self._viewport.copy()
            else:
                options["screen"] = {"width": 1600, "height": 900}

        # Add non-None context parameters (user values override stealth defaults)
        if self._viewport is not None and not self._no_viewport:
            options["viewport"] = self._viewport
        if self._user_agent is not None:
            options["user_agent"] = self._user_agent
        if self._locale is not None:
            options["locale"] = self._locale
        if self._timezone_id is not None:
            options["timezone_id"] = self._timezone_id
        if self._ignore_https_errors is not None:
            options["ignore_https_errors"] = self._ignore_https_errors
        if self._extra_http_headers is not None:
            options["extra_http_headers"] = self._extra_http_headers
        if self._offline is not None:
            options["offline"] = self._offline
        if self._color_scheme is not None:
            options["color_scheme"] = self._color_scheme

        # Auto-enable downloads if downloads_path is configured
        if self._downloads_path and "accept_downloads" not in self._extra_kwargs:
            options["accept_downloads"] = True

        # Extract context-specific kwargs (user values override everything)
        context_keys = {
            "screen", "no_viewport", "java_script_enabled", "bypass_csp",
            "geolocation", "permissions", "http_credentials",
            "device_scale_factor", "is_mobile", "has_touch",
            "reduced_motion", "forced_colors", "contrast",
            "accept_downloads", "base_url", "strict_selectors", "service_workers",
            "record_har_path", "record_har_omit_content", "record_har_url_filter",
            "record_har_mode", "record_har_content",
            "record_video_dir", "record_video_size",
            "client_certificates"
        }
        for key in context_keys:
            if key in self._extra_kwargs:
                options[key] = self._extra_kwargs[key]

        # Auto-create a default video dir so video recording is always available
        if "record_video_dir" not in options:
            if not self._temp_video_dir:
                self._temp_video_dir = str(BRIDGIC_TMP_DIR)
                os.makedirs(self._temp_video_dir, exist_ok=True)
                logger.info(f"Using default video dir: {self._temp_video_dir}")
            options["record_video_dir"] = self._temp_video_dir

        return options

    def _get_persistent_context_options(self) -> Dict[str, Any]:
        """Get options for launch_persistent_context() method.

        Combines launch options, context options, and user_data_dir.

        Returns
        -------
        Dict[str, Any]
            Options dict for playwright.chromium.launch_persistent_context()
        """
        # Start with launch options
        options = self._get_launch_options()

        # Add context options
        options.update(self._get_context_options())

        # Determine user_data_dir (only reached when clear_user_data=False)
        if self._user_data_dir:
            options["user_data_dir"] = str(self._user_data_dir)
        else:
            # No custom path: use the default persistent profile directory.
            BRIDGIC_USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
            options["user_data_dir"] = str(BRIDGIC_USER_DATA_DIR)
            logger.info(f"Using default user data dir: {BRIDGIC_USER_DATA_DIR}")

        return options

    # ==================== Lifecycle ====================

    async def _start(self) -> None:
        """Start the browser.

        Automatically chooses between two launch modes:
        - If `clear_user_data=False` (default): Uses `launch_persistent_context` (persistent profile)
        - If `clear_user_data=True`: Uses `launch` + `new_context` (ephemeral, no profile)

        When stealth mode is enabled, anti-detection args are automatically
        applied.
        """
        if self._playwright is not None:
            logger.warning("Playwright has already been started")
            return

        logger.info("Starting playwright")
        if self.stealth_enabled:
            logger.info("Stealth mode enabled")

        try:
            self._playwright = await async_playwright().start()

            if self.use_persistent_context:
                # Mode 1: Persistent context (clear_user_data=False)
                logger.info("Using persistent context mode")
                persistent_options = self._get_persistent_context_options()
                logger.debug(f"Persistent context options: {persistent_options}")
                _write_launch_debug_log(persistent_options, mode="persistent_context")
                # Retry on profile lock errors (e.g. after close-then-open race
                # where the old Chrome process hasn't fully released
                # user_data_dir/SingletonLock yet).
                _max_retries = 3
                _last_exc: Optional[Exception] = None
                for _attempt in range(_max_retries + 1):
                    try:
                        self._context = await self._playwright.chromium.launch_persistent_context(
                            **persistent_options
                        )
                        break
                    except Exception as exc:
                        if _attempt < _max_retries and self._is_profile_lock_error(exc):
                            logger.warning(
                                "[_start] profile locked (attempt %d/%d), retrying in 1s: %s",
                                _attempt + 1, _max_retries, exc,
                            )
                            user_data_dir = persistent_options.get("user_data_dir")
                            if user_data_dir:
                                self._try_clear_stale_lock(str(user_data_dir))
                            await asyncio.sleep(1.0)
                            _last_exc = exc
                            continue
                        raise
                else:
                    raise _last_exc  # type: ignore[misc]
                self._browser = self._context.browser
            else:
                # Mode 2: Ephemeral launch + new_context (clear_user_data=True)
                logger.info("Using normal launch mode")
                launch_options = self._get_launch_options()
                logger.debug(f"Launch options: {launch_options}")
                _write_launch_debug_log(launch_options, mode="launch")
                self._browser = await self._playwright.chromium.launch(**launch_options)

                context_options = self._get_context_options()
                logger.debug(f"Context options: {context_options}")
                self._context = await self._browser.new_context(**context_options)

            # Inject JS stealth patches before any page script runs.
            # Headed mode (self._headless=False) skips the init script entirely
            # so Cloudflare Turnstile's challenge iframe sees original, unmodified
            # browser APIs — the same as playwright CLI (which injects nothing).
            # context.add_init_script() runs in ALL frames including challenge
            # iframes; patching window.chrome (configurable:false),
            # navigator.permissions.query, and WebGL prototype inside the
            # Turnstile iframe causes detectable API inconsistencies that fail
            # the challenge even when the browser binary is fine.
            if self._stealth_builder and self._headless:
                init_script = self._stealth_builder.get_init_script(locale=self._locale)
                if init_script:
                    await self._context.add_init_script(init_script)

            # Auto create a new page if no page is open
            pages = self._context.pages
            if len(pages) > 0:
                self._page = pages[0]
            else:
                self._page = await self._context.new_page()

            # Attach download manager to handle downloads with correct filenames
            if self._download_manager:
                self._download_manager.attach_to_context(self._context)
                logger.info(
                    f"Download manager attached, saving to: {self._download_manager.downloads_path}"
                )

            logger.info(
                f"Playwright started (persistent_context={self.use_persistent_context}, "
                f"stealth={self.stealth_enabled})"
            )
        except BaseException:
            logger.exception("Failed to start browser; rolling back partial startup state")
            try:
                await self.close()
            except BaseException:
                logger.exception("Failed to roll back browser startup state")
            raise

    async def _ensure_started(self) -> None:
        """Auto-start the browser if not yet started.

        Guarantees that both ``_playwright`` and ``_context`` are initialised
        after this call returns.  If ``_playwright`` is set but ``_context`` is
        None (inconsistent state caused by an external browser crash or a
        partial ``close()``), the browser is fully reset before restarting.
        """
        if self._playwright is None:
            await self._start()
        elif self._context is None:
            # _playwright exists but context was lost — do a clean reset.
            logger.warning(
                "[_ensure_started] inconsistent state: _playwright set but _context is None; "
                "performing clean restart"
            )
            await self.close()
            await self._start()

    # Timeout (seconds) applied to individual page.close() calls during
    # shutdown so that a hung beforeunload handler cannot block forever.
    _PAGE_CLOSE_TIMEOUT = 5.0
    _TRACE_STOP_TIMEOUT = 10.0
    _VIDEO_PATH_TIMEOUT = 10.0
    _VIDEO_SAVE_AS_TIMEOUT = 120.0  # save_as copies a file; large recordings need more time
    _CONTEXT_CLOSE_TIMEOUT = 15.0
    _BROWSER_CLOSE_TIMEOUT = 15.0
    _PLAYWRIGHT_STOP_TIMEOUT = 15.0

    @staticmethod
    async def _force_kill_playwright_driver(pw: Any) -> None:
        """Force-kill the Playwright Node driver process (and its process group when safe).

        On macOS/Linux we attempt to kill the entire process group so that
        Chrome child processes are also terminated (killing only the Node driver
        leaves Chrome as orphans on macOS).

        Safety guard — same-pgid check:
            The daemon is spawned with start_new_session=True (setsid), so its
            pgid equals its own pid. The Node driver inherits that same pgid.
            Calling killpg(pgid) without the guard would SIGKILL the daemon
            itself, aborting close-report writes and leaving the socket file
            behind. When the driver shares our pgid we fall back to killing only
            the driver process (original behaviour — Chrome children remain
            orphans in this case, but that is unavoidable without psutil).

        Windows: os.getpgid / os.killpg are POSIX-only; on Windows we always
        fall back to proc.kill() directly.

        Accesses internal Playwright transport — best-effort: silently ignored
        if internals have changed.
        """
        try:
            proc = pw._connection._transport._proc  # type: ignore[union-attr]
            if proc and proc.returncode is None:
                killed_via_group = False
                if sys.platform != "win32":
                    try:
                        pgid = os.getpgid(proc.pid)
                        # Guard: do NOT send SIGKILL to our own process group.
                        # The daemon (and direct SDK callers) share the same pgid
                        # as the Node driver because the driver is spawned without
                        # start_new_session=True and inherits the caller's pgrp.
                        if pgid != os.getpgid(os.getpid()):
                            os.killpg(pgid, signal.SIGKILL)
                            killed_via_group = True
                            logger.debug(
                                "Force-killed Playwright driver process group (pgid=%d)", pgid
                            )
                    except (ProcessLookupError, OSError):
                        pass  # process already gone or pgid lookup failed
                if not killed_via_group:
                    proc.kill()
                    logger.debug("Force-killed Playwright driver process only")
                # Short timeout: SIGKILL is immediate, but wait() depends on
                # the event loop's child watcher which may misbehave at teardown.
                await asyncio.wait_for(proc.wait(), timeout=5.0)
        except Exception as exc:
            logger.debug("_force_kill_playwright_driver skipped: %s", exc)

    @staticmethod
    def _is_profile_lock_error(exc: Exception) -> bool:
        """Check if the exception is a Chrome profile lock error (SingletonLock)."""
        msg = str(exc).lower()
        return any(pattern in msg for pattern in (
            "user data directory is already in use",
            "failed to create a processsingleton",
            "profile is already in use",
            "singletonlock",
        ))

    @staticmethod
    def _try_clear_stale_lock(user_data_dir: str) -> None:
        """Remove SingletonLock if the holding Chrome process is dead.

        Chrome creates ``user_data_dir/SingletonLock`` as a symlink whose
        target is ``hostname-pid``.  If the PID is no longer alive, the lock
        is stale and safe to remove so Chrome can reuse the profile.

        Best-effort — silently ignored on any error or on Windows (Chrome uses
        a named mutex there, not a symlink).
        """
        if sys.platform == "win32":
            return
        lock_path = Path(user_data_dir) / "SingletonLock"
        try:
            if not lock_path.is_symlink() and not lock_path.exists():
                return
            target = os.readlink(str(lock_path))
            # Format: "hostname-pid"
            pid = int(target.rsplit("-", 1)[-1])
            try:
                os.kill(pid, 0)  # check if alive
            except ProcessLookupError:
                # Process dead — stale lock, remove it
                lock_path.unlink(missing_ok=True)
                logger.info(
                    "[_try_clear_stale_lock] removed stale SingletonLock (pid=%d dead) in %s",
                    pid, user_data_dir,
                )
            except PermissionError:
                pass  # alive but different user
        except Exception:
            pass  # best-effort

    def _write_close_report(self, errors: List[str]) -> None:
        """Write close-report.json into the close session directory."""
        session_dir = self._close_session_dir
        if not session_dir:
            return
        from datetime import datetime, timezone

        if errors:
            all_timeouts = all("timeout after" in e.lower() for e in errors)
            status = "success_with_timeouts" if all_timeouts else "error"
        else:
            status = "success"

        report = {
            "status": status,
            "closed_at": datetime.now(timezone.utc).isoformat(),
            "trace_paths": self._last_shutdown_artifacts.get("trace", []),
            "video_paths": self._last_shutdown_artifacts.get("video", []),
            "warnings": [],
            "errors": list(errors),
        }
        report_path = Path(session_dir) / "close-report.json"
        try:
            report_path.write_text(
                json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8",
            )
            logger.info("close-report written: %s", report_path)
        except Exception as exc:
            logger.warning("failed to write close-report.json: %s", exc)

    def _clear_page_scoped_state(self, page: Optional[Page], errors: Optional[List[str]] = None) -> None:
        """Detach page-scoped listeners and drop cached state for one page."""
        if page is None:
            return

        page_key = _get_page_key(page)

        if page_key in self._console_handlers:
            handler = self._console_handlers.pop(page_key)
            try:
                page.remove_listener("console", handler)
            except Exception as e:
                if errors is not None:
                    errors.append(f"console.remove_listener: {e}")
        self._console_messages.pop(page_key, None)

        if page_key in self._network_handlers:
            handler = self._network_handlers.pop(page_key)
            try:
                page.remove_listener("request", handler)
            except Exception as e:
                if errors is not None:
                    errors.append(f"network.remove_listener: {e}")
        self._network_requests.pop(page_key, None)

        if page_key in self._dialog_handlers:
            handler = self._dialog_handlers.pop(page_key)
            try:
                page.remove_listener("dialog", handler)
            except Exception as e:
                if errors is not None:
                    errors.append(f"dialog.remove_listener: {e}")

    def inspect_pending_close_artifacts(self) -> Dict[str, Any]:
        """Create a unique close-session directory and pre-allocate artifact paths.

        Called by the daemon before background teardown so paths can be reported
        immediately to the client. Stores state for browser.close() and the
        post-close report writer to consume.

        Returns
        -------
        Dict with keys:
          session_dir : str         — unique per-close directory under BRIDGIC_TMP_DIR
          trace       : List[str]   — pre-created trace path (if tracing is active)
          video       : List[str]   — pre-allocated video paths in session dir
        """
        import random
        from datetime import datetime

        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        session_name = f"close-{ts}-{random.randint(0, 0xffff):04x}"
        session_dir = Path(str(BRIDGIC_TMP_DIR)) / session_name
        session_dir.mkdir(parents=True, exist_ok=True)
        self._close_session_dir = str(session_dir)

        artifacts: Dict[str, Any] = {
            "session_dir": str(session_dir),
            "trace": [],
            "video": [],
        }

        if not self._context:
            return artifacts

        context_key = _get_context_key(self._context)

        # Pre-allocate trace path inside session dir
        if self._tracing_state.get(context_key):
            trace_path = str(session_dir / "trace.zip")
            Path(trace_path).touch()          # create empty file; tracing.stop() will overwrite
            self._preallocated_trace_path = trace_path
            artifacts["trace"].append(trace_path)

        # Determine video artifact info
        _absent: Any = object()
        pending_raw = self._pending_video_save_path.get(context_key, _absent)
        has_pending = pending_raw is not _absent

        if self._video_state.get(context_key) or has_pending:
            if has_pending and pending_raw:
                artifacts["video"].append(os.path.abspath(str(pending_raw)))
            else:
                # Pre-allocate video paths inside session dir so all artifacts
                # are grouped together instead of scattered in tmp/ with hashes.
                pages_with_video = [
                    p for p in list(self._context.pages)
                    if getattr(p, "video", None) is not None
                ]
                need_suffix = len(pages_with_video) > 1
                for i in range(len(pages_with_video)):
                    suffix = f"_{i + 1}" if need_suffix else ""
                    video_path = str(session_dir / f"video{suffix}.webm")
                    artifacts["video"].append(video_path)

        return artifacts

    async def close(self) -> str:
        """Close the browser.

        Stops the browser and cleans up all resources. Automatically removes
        active page-scoped event listeners (console capture, network capture,
        dialog handlers) — no need to call ``stop_*`` / ``remove_*`` methods
        beforehand. Active tracing/video sessions are auto-finalized and their
        paths included in the result.

        Safe to call even when the browser was never started — returns
        ``"Browser closed."`` immediately without raising.

        Returns
        -------
        str
            Operation result message. Includes auto-saved trace/video paths
            when active sessions were finalized during close.
        """
        if self._playwright is None:
            return "Browser closed."

        # Ensure a close session directory exists so trace/video artifacts are
        # grouped together (e.g. close-{ts}-{rand}/trace.zip, video_1.webm).
        # The CLI daemon calls inspect_pending_close_artifacts() before close(),
        # but SDK users call close() directly — auto-create for them.
        if not self._close_session_dir:
            self.inspect_pending_close_artifacts()

        errors: List[str] = []
        shutdown_artifacts: Dict[str, List[str]] = {"trace": [], "video": []}
        context_key: Optional[str] = None
        _deferred_video_saves: List[tuple] = []  # populated in Phase 1, consumed in Phase 2
        # Deferred re-raise: if CancelledError / KeyboardInterrupt arrives during any
        # cleanup await we record it here, finish ALL cleanup steps, then re-raise at
        # the very end.  This ensures no Playwright/Chromium process is left orphaned
        # just because one step was interrupted.
        _pending_cancel: Optional[BaseException] = None

        # Auto-stop active tracing before context/page teardown so trace data is saved.
        if self._context:
            context_key = _get_context_key(self._context)
            if self._tracing_state.get(context_key):
                output_path: Optional[str] = None
                try:
                    # Reuse pre-allocated path from inspect_pending_close_artifacts() if available
                    output_path = self._preallocated_trace_path
                    self._preallocated_trace_path = None
                    if output_path is None:
                        os.makedirs(BRIDGIC_TMP_DIR, exist_ok=True)
                        fd, output_path = tempfile.mkstemp(
                            suffix=".zip",
                            prefix="browser_trace_",
                            dir=str(BRIDGIC_TMP_DIR),
                        )
                        os.close(fd)
                    await asyncio.wait_for(
                        self._context.tracing.stop(path=output_path),
                        timeout=self._TRACE_STOP_TIMEOUT,
                    )
                    shutdown_artifacts["trace"].append(os.path.abspath(output_path))
                except asyncio.TimeoutError:
                    if output_path and os.path.exists(output_path):
                        try:
                            os.remove(output_path)
                        except Exception as cleanup_exc:
                            errors.append(f"tracing.tmp_cleanup: {cleanup_exc}")
                    errors.append(
                        f"tracing.stop: timeout after {self._TRACE_STOP_TIMEOUT:.1f}s"
                    )
                except Exception as e:
                    if output_path and os.path.exists(output_path):
                        try:
                            os.remove(output_path)
                        except Exception as cleanup_exc:
                            errors.append(f"tracing.tmp_cleanup: {cleanup_exc}")
                    errors.append(f"tracing.stop: {e}")
                except BaseException as e:
                    if output_path and os.path.exists(output_path):
                        try:
                            os.remove(output_path)
                        except Exception as cleanup_exc:
                            errors.append(f"tracing.tmp_cleanup: {cleanup_exc}")
                    errors.append(f"tracing.stop: {e}")
                    if _pending_cancel is None:
                        _pending_cancel = e
                finally:
                    self._tracing_state[context_key] = False

            # Always clear page-scoped listeners/caches for every context page.
            for page in list(self._context.pages):
                self._clear_page_scoped_state(page, errors)

            # Navigate all pages to about:blank before video finalization to
            # terminate service workers and ongoing network activity.  This
            # prevents context.close() from hanging later.
            #
            # Must run BEFORE video finalization because _finalize_video()
            # calls page.close() for each page — after that the page list is
            # empty and about:blank would be a no-op.
            for _nav_page in list(self._context.pages):
                try:
                    await asyncio.wait_for(
                        _nav_page.goto("about:blank", wait_until="commit"),
                        timeout=self._PAGE_CLOSE_TIMEOUT,
                    )
                except Exception as exc:
                    logger.debug("close: about:blank navigation failed: %s", exc)
                except BaseException as e:
                    if _pending_cancel is None:
                        _pending_cancel = e

            # Save videos when: (a) video_start() was called and never stopped, or
            # (b) stop_video() deferred the save to close time.
            # Use a sentinel because pop() returns None both for "absent" and "stored None".
            _absent: Any = object()
            pending_save_raw = self._pending_video_save_path.pop(context_key, _absent)
            has_pending_save = pending_save_raw is not _absent
            pending_filename: Optional[str] = pending_save_raw if has_pending_save else None  # type: ignore[assignment]

            # Video processing is split into two phases:
            #   Phase 1 (here): close video pages to trigger Chrome to finalize
            #     the video temp files.  This must happen before context.close().
            #   Phase 2 (after context.close): call video.save_as() to copy the
            #     finalized temp files to their destinations.  save_as() only
            #     needs the Playwright Node driver (not Chrome), so it works
            #     after context.close().  This lets context.close() run sooner,
            #     releasing Chrome's SingletonLock on user_data_dir quickly —
            #     critical for close-then-open sequences.
            if self._video_state.get(context_key) or has_pending_save:
                pages_with_video = [
                    (p, p.video)
                    for p in list(self._context.pages)
                    if getattr(p, "video", None) is not None
                ]

                need_suffix = len(pages_with_video) > 1
                dest_dir: Optional[str] = None
                dest_stem: Optional[str] = None
                dest_ext = ".webm"
                if pending_filename:
                    dest_dir = os.path.dirname(pending_filename)
                    dest_stem = os.path.splitext(os.path.basename(pending_filename))[0]
                elif self._close_session_dir:
                    # No explicit filename — save into session dir so all
                    # close artifacts are grouped together.
                    dest_dir = self._close_session_dir
                    dest_stem = "video"

                # Phase 1: close video pages (triggers Chrome video finalization)
                async def _close_video_page(page_: Any, video_: Any, idx: int) -> tuple:
                    await asyncio.wait_for(page_.close(), timeout=self._PAGE_CLOSE_TIMEOUT)
                    if dest_dir is not None and dest_stem is not None:
                        suffix = f"_{idx}" if need_suffix else ""
                        dest = os.path.join(dest_dir, f"{dest_stem}{suffix}{dest_ext}")
                        return (video_, dest)
                    return (video_, None)

                results = await asyncio.gather(
                    *(_close_video_page(p, v, i + 1) for i, (p, v) in enumerate(pages_with_video)),
                    return_exceptions=True,
                )
                for r in results:
                    if isinstance(r, BaseException):
                        errors.append(f"video.page_close: {r}")
                    elif r is not None:
                        _deferred_video_saves.append(r)

                self._video_state[context_key] = False
                # We may have closed the current page above.
                self._page = None
        else:
            self._clear_page_scoped_state(self._page, errors)

        # Close page (with timeout to guard against hung beforeunload handlers)
        if self._page:
            _page = self._page
            self._page = None
            try:
                await asyncio.wait_for(
                    _page.close(), timeout=self._PAGE_CLOSE_TIMEOUT,
                )
            except BaseException as e:
                errors.append(f"page.close: {e}")
                if not isinstance(e, Exception) and _pending_cancel is None:
                    _pending_cancel = e

        # Detach download manager before context closes to remove handlers
        if self._download_manager and self._context:
            try:
                self._download_manager.detach_from_context(self._context)
            except Exception as e:
                errors.append(f"download_manager.detach: {e}")

        # Close all remaining pages in context before closing context.
        # This avoids context.close() hanging on beforeunload handlers of extra
        # tabs the user may have opened manually (or pages we didn't track).
        if self._context:
            for extra_page in list(self._context.pages):
                try:
                    await asyncio.wait_for(
                        extra_page.close(run_before_unload=False),
                        timeout=self._PAGE_CLOSE_TIMEOUT,
                    )
                except BaseException as e:
                    if not isinstance(e, Exception) and _pending_cancel is None:
                        _pending_cancel = e
                    # best-effort; context.close() will handle remaining pages

        # Close context
        # NOTE: In persistent context mode, closing context will auto close browser
        if self._context:
            _context = self._context
            self._context = None
            try:
                await asyncio.wait_for(
                    _context.close(),
                    timeout=self._CONTEXT_CLOSE_TIMEOUT,
                )
            except asyncio.TimeoutError:
                errors.append(
                    f"context.close: timeout after {self._CONTEXT_CLOSE_TIMEOUT:.1f}s"
                )
                # context.close() hung — force-kill the entire Playwright driver
                # so browser.close() and playwright.stop() don't also time out.
                if self._playwright:
                    _playwright = self._playwright
                    self._playwright = None
                    self._browser = None  # browser dies with driver
                    await self._force_kill_playwright_driver(_playwright)
            except Exception as e:
                errors.append(f"context.close: {e}")
            except BaseException as e:
                errors.append(f"context.close: {e}")
                if _pending_cancel is None:
                    _pending_cancel = e

        # Close browser (only needed in normal launch mode, not persistent context)
        # In persistent context mode, browser is None or already closed
        if self._browser:
            _browser = self._browser
            self._browser = None
            try:
                await asyncio.wait_for(
                    _browser.close(),
                    timeout=self._BROWSER_CLOSE_TIMEOUT,
                )
            except asyncio.TimeoutError:
                errors.append(
                    f"browser.close: timeout after {self._BROWSER_CLOSE_TIMEOUT:.1f}s"
                )
            except Exception as e:
                errors.append(f"browser.close: {e}")
            except BaseException as e:
                errors.append(f"browser.close: {e}")
                if _pending_cancel is None:
                    _pending_cancel = e

        # Phase 2: save video files now that Chrome has exited and the profile
        # lock is released.  save_as() only needs the Playwright Node driver
        # (still alive) to copy the finalized temp files.
        for video_obj, dest in _deferred_video_saves:
            try:
                if dest:
                    await asyncio.wait_for(
                        video_obj.save_as(dest),
                        timeout=self._VIDEO_SAVE_AS_TIMEOUT,
                    )
                    shutdown_artifacts["video"].append(dest)
                else:
                    vp = await asyncio.wait_for(
                        video_obj.path(),
                        timeout=self._VIDEO_PATH_TIMEOUT,
                    )
                    shutdown_artifacts["video"].append(os.path.abspath(str(vp)))
            except asyncio.TimeoutError:
                errors.append(
                    f"video.save_as: timeout after {self._VIDEO_SAVE_AS_TIMEOUT:.1f}s"
                )
            except Exception as e:
                errors.append(f"video.save_as: {e}")
            except BaseException as e:
                errors.append(f"video.save_as: {e}")
                if _pending_cancel is None:
                    _pending_cancel = e

        if self._playwright:
            _playwright = self._playwright
            self._playwright = None
            try:
                await asyncio.wait_for(
                    _playwright.stop(),
                    timeout=self._PLAYWRIGHT_STOP_TIMEOUT,
                )
            except asyncio.TimeoutError:
                errors.append(
                    f"playwright.stop: timeout after {self._PLAYWRIGHT_STOP_TIMEOUT:.1f}s"
                )
                await self._force_kill_playwright_driver(_playwright)
            except Exception as e:
                errors.append(f"playwright.stop: {e}")
            except BaseException as e:
                errors.append(f"playwright.stop: {e}")
                if _pending_cancel is None:
                    _pending_cancel = e

        # Clear snapshot cache
        self._last_snapshot = None
        self._last_snapshot_url = None
        self._last_shutdown_artifacts = shutdown_artifacts
        self._last_shutdown_errors = list(errors)

        # Clear context-scoped state caches once the context is gone.
        if context_key is not None:
            self._tracing_state.pop(context_key, None)
            self._video_state.pop(context_key, None)

        # Flush all remaining state so a stopped instance holds no stale refs.
        # NOTE: _close_session_dir is intentionally preserved (like
        # _last_shutdown_artifacts / _last_shutdown_errors) so the daemon's
        # _write_close_report() can read it after close() returns.
        self._console_messages.clear()
        self._network_requests.clear()
        self._console_handlers.clear()
        self._network_handlers.clear()
        self._dialog_handlers.clear()
        self._tracing_state.clear()
        self._video_state.clear()
        self._pending_video_save_path.clear()

        trace_paths = shutdown_artifacts.get("trace", [])
        video_paths = shutdown_artifacts.get("video", [])
        if errors:
            lines = ["Browser closed with warnings", "Shutdown warnings:"]
            lines.extend(errors)
        else:
            lines = ["Browser closed successfully"]
        if trace_paths:
            lines.append("Auto-saved trace files:")
            lines.extend(trace_paths)
        if video_paths:
            lines.append("Auto-saved video files:")
            lines.extend(video_paths)
        result = "\n".join(lines)

        if errors:
            logger.warning(f"Browser closed with errors: {errors}")
        else:
            logger.info("Browser closed")

        # Write close-report.json into the session dir so SDK and CLI
        # produce identical artifacts.  The daemon's _write_close_report()
        # may overwrite this later with additional daemon-level info
        # (e.g. browser.close() overall timeout).
        self._write_close_report(errors)

        if _pending_cancel is not None:
            raise _pending_cancel

        return result

    async def __aenter__(self) -> "Browser":
        """Async context manager entry - starts the browser.

        Usage:
            async with Browser(headless=True) as browser:
                await browser.navigate_to("https://example.com")
                # Browser is automatically closed when exiting the context
        """
        await self._start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit - closes the browser."""
        await self.close()

    # ==================== Page Management ====================

    async def navigate_to(
        self,
        url: str,
        wait_until: Literal["domcontentloaded", "load", "networkidle", "commit"] = "domcontentloaded",
        timeout: Optional[float] = None,
    ) -> str:
        """Navigate to URL in current tab.

        Parameters
        ----------
        url : str
            URL to navigate to. Auto-prepends "http://" if missing protocol.
            Schemes "data:", "about:", "javascript:", and "vbscript:" are passed
            through unchanged. URLs starting with "/" are passed as-is.
        wait_until : str, default "domcontentloaded"
            When to consider navigation complete.
            - "domcontentloaded": DOM is parsed (fast, recommended for SPAs).
            - "load": Full page load including images/styles.
            - "networkidle": No network activity for 500ms (may timeout on SPAs).
            - "commit": Response received from server.
        timeout : float, optional
            Maximum time in seconds. Defaults to Playwright's 30s.

        Returns
        -------
        str
            "Navigated to: <actual_url>" where actual_url is the final URL
            after any redirects.

        Raises
        ------
        InvalidInputError
            If url is empty.
        StateError
            If context is unavailable after auto-start (should not normally occur).
        OperationError
            If navigation fails (network error, timeout, etc.).
        """
        try:
            await self._ensure_started()
            logger.info(f"[navigate_to] start url={url}")

            url = url.strip()
            if not url:
                _raise_invalid_input("URL cannot be empty", code="URL_EMPTY")

            url_lower = url.lower()
            has_scheme = "://" in url or url_lower.startswith(("data:", "about:", "javascript:", "vbscript:"))
            if not has_scheme:
                if not url.startswith("/"):
                    url = f"http://{url}"
                # else: URLs starting with '/' are absolute paths; passed as-is and will
                # fail at navigation time with a clear Playwright error (intentional).

            if not self._page:
                # All tabs were closed (e.g. via close_tab); _context is still alive.
                logger.info("No page is open, creating a new page in existing context")
                self._page = await self._context.new_page()

            kwargs: Dict[str, Any] = {"wait_until": wait_until}
            if timeout is not None:
                kwargs["timeout"] = timeout * 1000.0
            await self._page.goto(url, **kwargs)
            # Update cache
            self._last_snapshot = None
            self._last_snapshot_url = None
            page = await self.get_current_page()
            actual_url = page.url if page else url
            result = f"Navigated to: {actual_url}"

            logger.info(f"[navigate_to] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Navigation failed: {str(e)}"
            logger.error(f"[navigate_to] {error_msg}")
            _raise_operation_error(error_msg)

    async def _new_page(
        self,
        url: Optional[str] = None,
        wait_until: Literal["domcontentloaded", "load", "networkidle", "commit"] = "domcontentloaded",
        timeout: Optional[float] = None,
    ) -> Page:
        if self._context is None:
            _raise_state_error(
                "No browser context is open. Use navigate_to() to start the browser first.",
                code="NO_BROWSER_CONTEXT",
            )
        self._page = await self._context.new_page()
        if url:
            await self.navigate_to(url, wait_until=wait_until, timeout=timeout)
        await self._page.bring_to_front()
        return self._page

    async def get_page_desc(self, page: Optional[Page] = None) -> Optional[PageDesc]:
        if not page:
            page = self._page
        if not page:
            logger.warning("No page is open")
            return None
        page_id = generate_page_id(page)
        title = await page.title()
        page_desc = PageDesc(
            url=page.url,
            title=title,
            page_id=page_id,
        )
        return page_desc

    async def get_all_page_descs(self) -> List[PageDesc]:
        pages = self.get_pages()
        page_descs = []
        for page in pages:
            page_desc = await self.get_page_desc(page)
            if page_desc:
                page_descs.append(page_desc)
        return page_descs

    def get_pages(self) -> List[Page]:
        if not self._context:
            return []
        return self._context.pages

    async def switch_to_page(self, page_id: str) -> tuple[bool, str]:
        """Switch to a page by its page_id.

        Parameters
        ----------
        page_id : str
            The page identifier of the target page.

        Returns
        -------
        tuple[bool, str]
            A tuple of ``(success, message)``.
        """
        if not self._context:
            logger.warning("No context is open, can't switch to page")
            return False, "No context is open, can't switch to page"
        pages = self.get_pages()
        page = find_page_by_id(pages=pages, page_id=page_id)
        if not page:
            logger.warning(f"Page with page_id '{page_id}' not found")
            return False, f"Page with page_id '{page_id}' not found"
        await page.bring_to_front()
        self._page = page
        # Clear snapshot cache after switching pages
        self._last_snapshot = None
        self._last_snapshot_url = None
        title = await page.title()
        return True, f"Switched to tab {page_id}: {page.url} (title: {title})"

    async def _close_page(self, page: Page | str) -> tuple[bool, str]:
        """Close a page by Page object or page_id.

        Parameters
        ----------
        page : playwright.async_api.Page | str
            Either a `Page` object or a page_id string.

        Returns
        -------
        tuple[bool, str]
            A tuple of ``(success, message)``.
        """
        if not self._context:
            logger.warning("No context is open, can't close page")
            return False, "No context is open, can't close page"
        if isinstance(page, str):
            page_id = page
            pages = self.get_pages()
            page = find_page_by_id(pages=pages, page_id=page_id)
            if not page:
                logger.warning(f"Page with page_id '{page_id}' not found")
                return False, f"Page with page_id '{page_id}' not found"
        else:
            # If a Page object is passed, generate page_id
            page_id = generate_page_id(page)
        if not page:
            logger.warning("Page is None, can't close")
            return False, "Page is None, can't close"
        await page.close()

        # If the closed page is the current page, switch to another
        if self._page == page:
            pages = self._context.pages
            self._page = pages[0] if pages else None
            # Clear snapshot cache
            self._last_snapshot = None
            self._last_snapshot_url = None

        if self._page:
            now_id = generate_page_id(self._page)
            now_title = await self._page.title()
            return True, f"Closed tab {page_id}. Now on {now_id}: {self._page.url} (title: {now_title})"
        return True, f"Closed tab {page_id}. No tabs remaining"

    async def get_page_size_info(self) -> Optional[PageSizeInfo]:
        if not self._page:
            logger.warning("No page is open")
            return None
        # use CDP to get page size info
        if self._context:
            cdp_session = None
            try:
                # NOTE: CDP sessions are only supported on Chromium-based browsers.
                # create cdp session for the page
                cdp_session = await self._context.new_cdp_session(self._page)
                # get page size info：more information see https://chromedevtools.github.io/devtools-protocol/tot/Page/#method-getLayoutMetrics
                result = await cdp_session.send("Page.getLayoutMetrics")
                logger.debug(f"Page size info: {result}")
                # use modern css properties if available
                layout_viewport = result.get('cssLayoutViewport') or result.get('layoutViewport', {})
                content_size = result.get('cssContentSize') or result.get('contentSize', {})
                visual_viewport = result.get('cssVisualViewport') or result.get('visualViewport')
                # viewport size (visualViewport is more accurate, considering zoom)
                if visual_viewport:
                    viewport_width = int(visual_viewport.get('clientWidth') or 0)
                    viewport_height = int(visual_viewport.get('clientHeight') or 0)
                else:
                    viewport_width = int(layout_viewport.get('clientWidth') or 0)
                    viewport_height = int(layout_viewport.get('clientHeight') or 0)

                # scroll position (get pageX/pageY from layoutViewport)
                scroll_x = int(layout_viewport.get('pageX') or 0)
                scroll_y = int(layout_viewport.get('pageY') or 0)

                # page total size (contentSize contains all scrollable content)
                page_width = int(content_size.get('width') or viewport_width)
                page_height = int(content_size.get('height') or viewport_height)

                # calculate scrollable distance
                pixels_above = scroll_y
                pixels_below = max(0, page_height - viewport_height - scroll_y)
                pixels_left = scroll_x
                pixels_right = max(0, page_width - viewport_width - scroll_x)

                return PageSizeInfo(
                    viewport_width=viewport_width,
                    viewport_height=viewport_height,
                    page_width=page_width,
                    page_height=page_height,
                    scroll_x=scroll_x,
                    scroll_y=scroll_y,
                    pixels_above=pixels_above,
                    pixels_below=pixels_below,
                    pixels_left=pixels_left,
                    pixels_right=pixels_right,
                )
            except Exception as e:
                logger.debug(f"Failed to get page size info: {e}")
            finally:
                # Always detach CDP session to prevent resource leak
                if cdp_session:
                    try:
                        await cdp_session.detach()
                    except Exception:
                        pass

        # fallback to js to get page size info
        try:
            page_size_info = await self._page.evaluate("""() => {
                // 1. viewport size (without scrollbar, aligned with cssLayoutViewport in CDP)
                const viewportWidth = document.documentElement.clientWidth || window.innerWidth;
                const viewportHeight = document.documentElement.clientHeight || window.innerHeight;
                
                // 2. page total size (most reliable in standard mode)
                const pageWidth = document.documentElement.scrollWidth;
                const pageHeight = document.documentElement.scrollHeight;
                
                // 3. scroll position (modern browser universal API)
                const scrollX = window.scrollX || window.pageXOffset;
                const scrollY = window.scrollY || window.pageYOffset;
                
                return {
                    viewport_width: viewportWidth,
                    viewport_height: viewportHeight,
                    page_width: pageWidth,
                    page_height: pageHeight,
                    scroll_x: scrollX,
                    scroll_y: scrollY
                };
            }""")
            logger.debug(f"Page size info: {page_size_info}")

            viewport_width = page_size_info.get('viewport_width', 0)
            viewport_height = page_size_info.get('viewport_height', 0)
            page_width = page_size_info.get('page_width', 0)
            page_height = page_size_info.get('page_height', 0)
            scroll_x = page_size_info.get('scroll_x', 0)
            scroll_y = page_size_info.get('scroll_y', 0)

            pixels_above = scroll_y
            pixels_below = max(0, page_height - viewport_height - scroll_y)
            pixels_left = scroll_x
            pixels_right = max(0, page_width - viewport_width - scroll_x)
            
            return PageSizeInfo(
                viewport_width=viewport_width,
                viewport_height=viewport_height,
                page_width=page_width,
                page_height=page_height,
                scroll_x=scroll_x,
                scroll_y=scroll_y,
                pixels_above=pixels_above,
                pixels_below=pixels_below,
                pixels_left=pixels_left,
                pixels_right=pixels_right,
            )
        except Exception as e:
            logger.debug(f"Failed to get page size info: {e}")
            return None
    
    async def get_current_page(self) -> Optional[Page]:
        return self._page
    
    def get_current_page_url(self) -> Optional[str]:
        return self._page.url if self._page else None
    
    async def get_current_page_title(self) -> Optional[str]:
        """Get the title of the current page.

        Returns
        -------
        Optional[str]
            Page title, or None if no page is open.
        """
        return await self._page.title() if self._page else None

    async def _get_page_info(self) -> Optional[PageInfo]:
        if not self._page:
            logger.warning("No page is open")
            return None

        page_size_info, title = await asyncio.gather(self.get_page_size_info(), self.get_current_page_title())

        if page_size_info is None:
            logger.warning("Failed to get page size info")
            return None
        page_info = PageInfo(
            url=self.get_current_page_url(),
            title=title,
            **page_size_info.model_dump(),
        )
        return page_info

    async def get_full_page_info(self,
        interactive: bool = False,
        full_page: bool = True,
    ) -> Optional[FullPageInfo]:
        if not self._page:
            logger.warning("No page is open, can't get full page info")
            return None
        try:
            snapshot = await self.get_snapshot(
                interactive=interactive,
                full_page=full_page,
            )
            if snapshot is None:
                logger.warning("Failed to get snapshot")
                return None
            page_info = await self._get_page_info()
            if page_info is None:
                logger.warning("Failed to get page info")
                return None
            full_page_info = FullPageInfo(
                url=page_info.url,
                title=page_info.title,
                **page_info.model_dump(),
                tree=snapshot.tree,
            )
            return full_page_info
        except Exception as e:
            logger.debug(f"Failed to get full page info: {e}")
            return None
    

    #########################################################
    # screenshot
    #########################################################
    async def _take_screenshot_raw(
        self,
        path: Optional[str | Path] = None,
        full_page: bool = False,
        **kwargs,
    ) -> Optional[bytes]:
        """Take a screenshot of the current page (raw bytes).

        Parameters
        ----------
        path : Optional[str | pathlib.Path], optional
            Optional file path to save the screenshot.
        full_page : bool, optional
            Whether to capture the full page or just the viewport. Default is False.
        **kwargs
            Additional screenshot options forwarded to Playwright.

        Returns
        -------
        Optional[bytes]
            Screenshot bytes, or None if no page is open.
        """
        if not self._page:
            logger.warning("No page is open, can't take screenshot")
            return None
        screenshot = await self._page.screenshot(
            path=path,
            full_page=full_page,
            **kwargs
        )
        return screenshot

    # ==================== Snapshot & Element Refs ====================

    async def get_snapshot(
        self,
        interactive: bool = False,
        full_page: bool = True,
    ) -> EnhancedSnapshot:
        """Get accessibility snapshot of the current page (low-level API).

        This is the underlying snapshot method.  For LLM agents and CLI use,
        prefer :meth:`get_snapshot_text` which returns a formatted, paginated
        string with a page header and truncation notice.

        The result's ``refs`` dict is the source of truth for all ``*_by_ref``
        tools.  After this call, element refs in the returned snapshot can be
        passed to :meth:`get_element_by_ref`, :meth:`click_element_by_ref`, etc.

        Parameters
        ----------
        interactive : bool, default False
            If True, only include interactive elements (buttons, links, inputs,
            checkboxes, elements with cursor:pointer, etc.) with a flattened
            single-level output.  Best for action selection.
        full_page : bool, default True
            If True (default), include all elements regardless of viewport
            position.  If False, only include elements within the viewport.

        Returns
        -------
        EnhancedSnapshot
            Object with:

            - ``.tree`` : str — accessibility tree as a multi-line string
              (lines like ``- button "Submit" [ref=8d4b03a9]``).
            - ``.refs`` : Dict[str, RefData] — maps ref IDs to locator data.

        Raises
        ------
        StateError
            If no active page is available.
        OperationError
            If snapshot generation fails.
        """
        try:
            if not self._page:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")
            async with self._snapshot_lock:
                options = SnapshotOptions(
                    interactive=interactive,
                    full_page=full_page,
                )
                if self._snapshot_generator is None:
                    self._snapshot_generator = SnapshotGenerator()
                current_url = self.get_current_page_url()
                self._last_snapshot = await self._snapshot_generator.get_enhanced_snapshot_async(
                    self._page, options
                )
                self._last_snapshot_url = current_url
                return self._last_snapshot
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to get snapshot: {str(e)}"
            logger.error(f"[get_snapshot] {error_msg}", exc_info=True)
            _raise_operation_error(error_msg)
    
    async def get_element_by_ref(self, ref: str, _fallback_depth: int = 0) -> Optional[Locator]:
        """Resolve a snapshot ref to a Playwright Locator.

        Parameters
        ----------
        ref : str
            Element ref from the last snapshot (e.g., "1f79fe5e", "8d4b03a9").
            Obtain refs by calling :meth:`get_snapshot` or :meth:`get_snapshot_text` first.
        _fallback_depth : int, optional
            Internal recursion guard for the recovery path. Do not pass this parameter.

        Returns
        -------
        Optional[Locator]
            Resolved Playwright ``Locator``, or ``None`` when:
            - No page is open (``start()`` not called or browser closed).
            - No snapshot has been taken yet.
            - ``ref`` is not present in the last snapshot.
            Returns ``None`` instead of raising so callers can decide how to handle
            a stale or unknown ref.

        Notes
        -----
        - When multiple elements share the same role+name, an automatic recovery
          path selects the nth visible match from the snapshot; a fresh snapshot
          via :meth:`get_snapshot` is the preferred fix for persistent ambiguity.
        - For elements inside iframes, the locator is scoped through the correct
          ``frame_locator`` chain derived from ``RefData.frame_path``.
        """
        if not self._page:
            logger.warning("No page is open, can't get element by ref")
            return None
        if self._last_snapshot is None:
            logger.warning("No snapshot is available, can't get element by ref, please get snapshot first")
            return None
        try:
            if self._snapshot_generator is None:
                self._snapshot_generator = SnapshotGenerator()

            ref_data = self._last_snapshot.refs.get(ref)

            # ── aria-ref fast-path ─────────────────────────────────────────────────
            # Playwright's aria-ref engine maps ephemeral IDs (e.g. "e369", "f1e5")
            # directly to live DOM element pointers populated during snapshotForAI.
            # O(1) lookup — no CSS reconstruction needed.
            #
            # Each frame stores its own _lastAriaSnapshotForQuery keyed by the FULL
            # prefixed ref (e.g. L1 frame stores "f1e5" → element).  For iframe
            # elements we therefore scope the locator to the correct frame first via
            # frame_locator chain — this ensures locator.evaluate() and all other
            # locator operations run in the element's own frame context, not the
            # main frame.  Main-frame elements (frame_path=None) use page directly.
            #
            # Falls through silently if stale (count=0) or engine unavailable.
            if ref_data and ref_data.playwright_ref:
                try:
                    ar_scope = self._page
                    if ref_data.frame_path:
                        for local_nth in ref_data.frame_path:
                            ar_scope = ar_scope.frame_locator("iframe").nth(local_nth)
                    ar_locator = ar_scope.locator(f"aria-ref={ref_data.playwright_ref}")
                    ar_count = await ar_locator.count()
                    if ar_count == 1:
                        logger.debug(
                            "[get_element_by_ref] aria-ref fast-path hit: ref=%s playwright_ref=%s frame_path=%s",
                            ref, ref_data.playwright_ref, ref_data.frame_path,
                        )
                        return ar_locator
                    # ar_count == 0 → snapshot is stale (DOM changed) — fall through
                    # ar_count > 1  → should never happen for a direct pointer — fall through
                    logger.debug(
                        "[get_element_by_ref] aria-ref stale (count=%d), falling through to CSS: ref=%s playwright_ref=%s",
                        ar_count, ref, ref_data.playwright_ref,
                    )
                except Exception as _ar_exc:
                    logger.debug(
                        "[get_element_by_ref] aria-ref exception (%s), falling through to CSS: ref=%s",
                        _ar_exc, ref,
                    )
            # ── aria-ref fast-path end ─────────────────────────────────────────────

            if ref_data is None:
                logger.debug("[get_element_by_ref] ref not found in snapshot: %s", ref)
            else:
                logger.debug(
                    "[get_element_by_ref] CSS path: ref=%s role=%s name=%r nth=%s frame_path=%s",
                    ref,
                    ref_data.role,
                    ref_data.name,
                    ref_data.nth,
                    ref_data.frame_path,
                )
            locator = self._snapshot_generator.get_locator_from_ref_async(
                self._page, ref, self._last_snapshot.refs
            )
            if locator:
                # Validate locator and expose ambiguity explicitly for debugging.
                count = await locator.count()
                if count == 1:
                    return locator
                elif count > 1:
                    can_recover_by_role_name = (
                        bool(ref_data and ref_data.name)
                        and ref_data.role not in SnapshotGenerator.ROLE_TEXT_MATCH_ROLES
                        and ref_data.role not in SnapshotGenerator.STRUCTURAL_NOISE_ROLES
                        and ref_data.role not in SnapshotGenerator.TEXT_LEAF_ROLES
                    )
                    if can_recover_by_role_name and ref_data:
                        scope = self._page
                        if ref_data.frame_path:
                            for local_nth in ref_data.frame_path:
                                scope = scope.frame_locator("iframe").nth(local_nth)
                        role_name_locator = scope.get_by_role(
                            ref_data.role,
                            name=ref_data.name,
                            exact=True,
                        )
                        role_name_count = await role_name_locator.count()
                        if role_name_count == 1:
                            logger.warning(
                                "Ref %s resolved to %d elements; recovered unique locator via role+name",
                                ref,
                                count,
                            )
                            return role_name_locator
                        if (
                            role_name_count > 1
                            and ref_data.nth is not None
                            and ref_data.nth < role_name_count
                        ):
                            logger.warning(
                                "Ref %s resolved to %d elements; recovered locator via role+name nth=%d",
                                ref,
                                count,
                                ref_data.nth,
                            )
                            return role_name_locator.nth(ref_data.nth)

                    # Only apply nth fallback when the locator key space matches
                    # the role:name key space used to compute nth.  For unnamed
                    # STRUCTURAL_NOISE_ROLES (child_text anchor) and TEXT_LEAF_ROLES
                    # the locator key space doesn't match.  Named STRUCTURAL_NOISE
                    # elements use CSS-scoped locators with nth already applied,
                    # so they won't reach this recovery path (count will be 0 or 1).
                    nth_keyspace_matches = (
                        ref_data
                        and ref_data.role not in SnapshotGenerator.STRUCTURAL_NOISE_ROLES
                        and ref_data.role not in SnapshotGenerator.TEXT_LEAF_ROLES
                    )
                    if (
                        nth_keyspace_matches
                        and ref_data.nth is not None
                        and ref_data.nth < count
                    ):
                        logger.warning(
                            "Ref %s resolved to %d elements; using snapshot nth=%d",
                            ref,
                            count,
                            ref_data.nth,
                        )
                        return locator.nth(ref_data.nth)

                    visible_matches: List[Locator] = []
                    for idx in range(count):
                        candidate = locator.nth(idx)
                        try:
                            if await candidate.is_visible():
                                visible_matches.append(candidate)
                        except Exception:
                            # Ignore transient visibility failures and keep probing.
                            continue

                    if len(visible_matches) == 1:
                        logger.warning(
                            "Ref %s resolved to %d elements; using the only visible match",
                            ref,
                            count,
                        )
                        return visible_matches[0]
                    if len(visible_matches) > 1:
                        logger.warning(
                            "Ref %s resolved to %d elements (%d visible); using first visible match",
                            ref,
                            count,
                            len(visible_matches),
                        )
                        return visible_matches[0]

                    logger.warning(
                        "Ref %s resolved to %d elements with no visible match; using first match",
                        ref,
                        count,
                    )
                    return locator.first
                else:
                    logger.warning("No element found by ref: %s (count=0)", ref)
                    if _fallback_depth == 0:
                        return await self._fallback_to_child_ref(ref)
                    return None
            else:
                logger.warning(f"Failed to get locator by ref: {ref}")
                if _fallback_depth == 0:
                    return await self._fallback_to_child_ref(ref)
                return None
        except Exception as e:
            logger.debug(f"Failed to get element by ref: {e}")
            return None

    async def _fallback_to_child_ref(self, parent_ref: str) -> Optional[Locator]:
        """Try to find a usable child ref when the parent ref's locator fails.

        Only activates for structural noise roles (generic, group, etc.)
        without name/text, where the locator is inherently fragile.
        """
        if self._last_snapshot is None:
            return None
        refs = self._last_snapshot.refs
        parent_data = refs.get(parent_ref)
        if not parent_data:
            return None

        has_text_signal = bool(parent_data.name or parent_data.text_content)
        if parent_data.role not in SnapshotGenerator.STRUCTURAL_NOISE_ROLES or has_text_signal:
            return None

        children = [
            (child_ref, child_data)
            for child_ref, child_data in refs.items()
            if child_data.parent_ref == parent_ref
        ]
        if not children:
            return None

        def _score(data) -> int:
            """Higher = better candidate for interaction."""
            s = 0
            if data.role in SnapshotGenerator.INTERACTIVE_ROLES:
                s += 10
            if data.name:
                s += 5
            elif data.text_content:
                s += 3
            if data.role not in SnapshotGenerator.STRUCTURAL_NOISE_ROLES:
                s += 2
            return s

        children.sort(key=lambda c: _score(c[1]), reverse=True)
        best_ref, best_data = children[0]

        if _score(best_data) == 0:
            return None

        if len(children) > 1 and _score(children[0][1]) == _score(children[1][1]):
            candidates = ", ".join(
                f"{r} ({d.name or d.text_content or d.role})"
                for r, d in children
                if _score(d) == _score(children[0][1])
            )
            logger.warning(
                "Ref %s (container) failed; multiple child candidates with equal priority: %s",
                parent_ref,
                candidates,
            )

        logger.info(
            "Ref %s (container) failed; falling back to child ref %s (%s)",
            parent_ref,
            best_ref,
            best_data.name or best_data.text_content or best_data.role,
        )
        return await self.get_element_by_ref(best_ref, _fallback_depth=1)

    async def get_element_by_prompt(self, prompt: str, llm: "OpenAILlm") -> Optional[Locator]:
        """Find element by natural language prompt and return Locator.

        Parameters
        ----------
        prompt : str
            Natural language description of the element to find
        llm : OpenAILlm
            LLM instance for element finding

        Returns
        -------
        Optional[Locator]
            Found element Locator, or None if not found
        """
        try:
            from bridgic.core.model.protocols import PydanticModel  # pyright: ignore[reportMissingImports]
            from bridgic.core.model.types import Message, Role  # pyright: ignore[reportMissingImports]
        except ModuleNotFoundError as exc:
            logger.warning(
                "get_element_by_prompt unavailable: missing module '%s'; "
                "install bridgic-core to enable prompt-based lookup.",
                exc.name or "bridgic.core",
            )
            return None
        except ImportError as exc:
            logger.warning(
                "get_element_by_prompt unavailable: failed to import bridgic.core model types: %s",
                exc,
            )
            return None
        
        snapshot = await self.get_snapshot()
        if snapshot is None:
            logger.warning(
                "get_element_by_prompt aborted: snapshot unavailable (prompt_len=%d)",
                len(prompt),
            )
            return None
        browser_state = snapshot.tree
        
        system_prompt = """You are an AI created to find an element on a page by a prompt.
<browser_state>
Interactive Elements: All interactive elements will be provided in format as:
- role "name" [ref=ref_id]

Examples:
- button "Submit" [ref=8d4b03a9]
- textbox "Email" [ref=d6a530b4]
- link "Learn more" [ref=1f79fe5e]

Note that:
- Only elements with [ref=...] are interactive
- ref is the identifier you should return
- The format is: - role "name" [ref=ref_id]
</browser_state>

Your task is to find an element ref (if any) that matches the prompt (written in <prompt> tag).

If none of the elements matches, return None.

Before you return the element ref, reason about the state and elements for a sentence or two."""
        
        class ElementResponse(BaseModel):
            element_ref: Optional[str] = None
        
        user_message = f"""<browser_state>
{browser_state}
</browser_state>
<prompt>
{prompt}
</prompt>
"""
        
        messages = [
            Message.from_text(system_prompt, role=Role.SYSTEM),
            Message.from_text(user_message, role=Role.USER),
        ]
        
        result = await llm.astructured_output(
            messages=messages,
            constraint=PydanticModel(model=ElementResponse),
        )
        
        element_ref = result.element_ref
        if element_ref is None:
            return None
        
        return await self.get_element_by_ref(element_ref)

    # ==================== State Tool ====================

    async def get_snapshot_text(
        self,
        limit: int = _DEFAULT_SNAPSHOT_LIMIT,
        interactive: bool = False,
        full_page: bool = True,
        file: Optional[str] = None,
    ) -> str:
        """Get the page accessibility tree as a formatted string with element refs.

        **Call this first** to obtain element refs (e.g., ``1f79fe5e``) before
        using any action tool (``click_element_by_ref``, ``input_text_by_ref``,
        etc.).  The returned string is what LLM agents and CLI users should
        consume; for the raw ``EnhancedSnapshot`` object see :meth:`get_snapshot`.

        Output format example::

            [Page: https://example.com | Example Domain]
            - heading "Example Domain" [ref=a1b2c3d4]
            - button "Submit" [ref=8d4b03a9]
            - textbox "Email" [ref=d6a530b4]

        Parameters
        ----------
        limit : int, optional
            Maximum number of characters to return.  Must be >= 1.
            Default is 10 000.  When the snapshot exceeds this limit,
            the full content is written to a file and only a notice with
            the file path is returned (no snapshot content).
        interactive : bool, optional
            If True, only include clickable/editable elements (buttons, links,
            inputs, checkboxes, elements with cursor:pointer, etc.).
            Best for action selection. Default is False.
        full_page : bool, optional
            If True (default), include elements outside the viewport.
            If False, only include elements within the current viewport.
        file : str or None, optional
            File path to write the full snapshot.  When provided, the
            snapshot is always saved to this file regardless of whether
            content exceeds ``limit``, and only a notice with the file
            path is returned (no snapshot content).  When ``None``
            (default), file is only written if content exceeds ``limit``,
            using an auto-generated path under
            ``~/.bridgic/bridgic-browser/snapshot/``.

        Returns
        -------
        str
            Page header followed by the accessibility tree.  Lines with
            ``[ref=...]`` are interactive elements.

            When the snapshot exceeds ``limit`` or ``file`` is provided,
            a ``[notice]`` with the file path is returned instead of
            the snapshot content.

        Raises
        ------
        InvalidInputError
            If ``limit`` is less than 1, or ``file`` is empty/whitespace-only,
            contains null bytes, or points to an existing directory.
        OperationError
            If snapshot generation fails.
        """
        try:
            if limit < 1:
                _raise_invalid_input(
                    "limit must be >= 1",
                    code="INVALID_LIMIT",
                    details={"limit": limit},
                )

            if file is not None:
                if not file.strip():
                    _raise_invalid_input(
                        "file path must not be empty",
                        code="INVALID_FILE_PATH",
                        details={"file": file},
                    )
                if "\x00" in file:
                    _raise_invalid_input(
                        "file path must not contain null bytes",
                        code="INVALID_FILE_PATH",
                        details={"file": repr(file)},
                    )
                if Path(file).is_dir():
                    _raise_invalid_input(
                        f"file path is an existing directory: {file}",
                        code="INVALID_FILE_PATH",
                        details={"file": file},
                    )

            snapshot = await self.get_snapshot(
                interactive=interactive,
                full_page=full_page,
            )
            _page = getattr(self, "_page", None)
            page_url = _page.url if _page else ""
            page_title = await _page.title() if _page else ""
            header = f"[Page: {page_url} | {page_title}]\n"
            full_text = snapshot.tree

            total_length = len(full_text)

            if total_length > limit or file:
                file_content = header + full_text
                total_chars = len(file_content)
                total_lines = file_content.count("\n") + (1 if file_content and not file_content.endswith("\n") else 0)
                snapshot_file = self._write_snapshot_file(file_content, file)
                notice = (
                    f"[notice] Snapshot file ({total_chars} characters, {total_lines} lines) "
                    f"saved to: {snapshot_file}\n"
                )
                logger.info("[get_snapshot_text] Snapshot saved to %s", snapshot_file)
                return header + notice

            logger.info("[get_snapshot_text] Successfully retrieved interface information")
            return header + full_text
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to get interface information: {e}"
            logger.error(f"[get_snapshot_text] {error_msg}")
            _raise_operation_error(error_msg)

    def _write_snapshot_file(self, content: str, file: Optional[str] = None) -> str:
        """Write snapshot content to a file and return the absolute path.

        Callers must validate ``file`` before calling (get_snapshot_text does
        this).  When ``file`` is None, an auto-generated path under
        BRIDGIC_SNAPSHOT_DIR is used.
        """
        import random
        from datetime import datetime

        if file:
            filepath = Path(file)
        else:
            snapshot_dir = BRIDGIC_SNAPSHOT_DIR
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            rand_suffix = f"{random.randint(0, 0xffff):04x}"
            filename = f"snapshot-{ts}-{rand_suffix}.txt"
            filepath = snapshot_dir / filename

        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(content, encoding="utf-8")
        if sys.platform != "win32":
            try:
                filepath.chmod(0o600)
            except OSError:
                pass
        return str(filepath.resolve())

    # ==================== Navigation Tools ====================

    async def search(
        self,
        query: str,
        engine: str = "duckduckgo",
        wait_until: str = "domcontentloaded",
        timeout: Optional[float] = None,
    ) -> str:
        """Search using a search engine.

        Parameters
        ----------
        query : str
            Query string to search.
        engine : str, optional
            "duckduckgo" (default), "google", or "bing".
        wait_until : str, default "domcontentloaded"
            When to consider navigation complete:
            - "domcontentloaded": DOM parsed (fast, good for modern SPAs).
            - "load": Full page load including images/styles.
            - "networkidle": No network activity for 500ms (may timeout on SPAs).
            - "commit": Response received from server.
        timeout : float, optional
            Maximum time in seconds. Defaults to Playwright's 30s.

        Returns
        -------
        str
            Result message.
        """
        try:
            logger.info(f"[search] start engine={engine} query={query!r}")

            query = query.strip()
            if not query:
                _raise_invalid_input("Search query cannot be empty", code="QUERY_EMPTY")
            engine = engine.strip().lower() if engine else "duckduckgo"

            import urllib.parse

            encoded_query = urllib.parse.quote_plus(query)

            search_engines = {
                'duckduckgo': f'https://duckduckgo.com/?q={encoded_query}',
                'google': f'https://www.google.com/search?q={encoded_query}&udm=14',
                'bing': f'https://www.bing.com/search?q={encoded_query}',
            }

            if engine not in search_engines:
                error_msg = f'Unsupported search engine: {engine}. Options: duckduckgo, google, bing'
                logger.error(f'[search] {error_msg}')
                _raise_invalid_input(
                    error_msg,
                    code="UNSUPPORTED_SEARCH_ENGINE",
                    details={"engine": engine},
                )

            search_url = search_engines[engine]

            try:
                await self.navigate_to(search_url, wait_until=wait_until, timeout=timeout)
                result = f"Searched on {engine.title()}: '{query}'"
                logger.info(f"[search] done {result}")
                return result
            except BridgicBrowserError:
                raise
            except Exception as e:
                logger.error(f"[search] failed engine={engine} error={type(e).__name__}: {e}")
                error_msg = f'Search on {engine} failed for "{query}": {str(e)}'
                _raise_operation_error(error_msg)
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Search failed: {str(e)}"
            logger.error(f"[search] failed error={type(e).__name__}: {error_msg}")
            _raise_operation_error(error_msg)

    async def go_back(self) -> str:
        """Navigate back to the previous page in the tab's history.

        Returns
        -------
        str
            "Navigated back to: <url>" on success.

        Raises
        ------
        StateError
            If no active page is available, or if there is no previous page
            in history (error code "NO_HISTORY_ENTRY", retryable=False).
        OperationError
            If navigation fails for another reason.
        """
        try:
            logger.info(f"[go_back] start")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            await page.go_back()
            result = f"Navigated back to: {page.url}"
            logger.info(f"[go_back] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to navigate back: {str(e)}"
            logger.error(f"[go_back] {error_msg}")
            if "Cannot navigate" in str(e) or "no previous entry" in str(e):
                result = "Cannot navigate back: no previous page in history"
                logger.info(f"[go_back] {result}")
                _raise_state_error(result, code="NO_HISTORY_ENTRY", retryable=False)
            _raise_operation_error(error_msg)

    async def go_forward(self) -> str:
        """Navigate forward to the next page in the tab's history.

        Returns
        -------
        str
            "Navigated forward to: <url>" on success.

        Raises
        ------
        StateError
            If no active page is available.
        OperationError
            If navigation fails (e.g., no forward history entry).
        """
        try:
            logger.info(f"[go_forward] start")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")
            await page.go_forward()
            result = f"Navigated forward to: {page.url}"
            logger.info(f"[go_forward] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to navigate forward: {str(e)}"
            logger.error(f"[go_forward] {error_msg}")
            _raise_operation_error(error_msg)

    # ==================== Page and Tab Management Tools ====================

    async def reload_page(
        self,
        wait_until: str = "domcontentloaded",
        timeout: Optional[float] = None,
    ) -> str:
        """Reload the current page.

        Parameters
        ----------
        wait_until : str, default "domcontentloaded"
            When to consider reload complete:
            - "domcontentloaded": DOM parsed (fast, good for modern SPAs).
            - "load": Full page load including images/styles.
            - "networkidle": No network activity for 500ms (may timeout on SPAs).
            - "commit": Response received from server.
        timeout : float, optional
            Maximum time in seconds. Defaults to Playwright's 30s.

        Returns
        -------
        str
            Result message.
        """
        try:
            logger.info("[reload_page] start")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")
            kwargs: Dict[str, Any] = {"wait_until": wait_until}
            if timeout is not None:
                kwargs["timeout"] = timeout * 1000.0
            await page.reload(**kwargs)
            title = await page.title()
            result = f"Page reloaded: {page.url} (title: {title})"
            logger.info(f"[reload_page] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to reload page: {str(e)}"
            logger.error(f"[reload_page] {error_msg}")
            _raise_operation_error(error_msg)

    async def get_current_page_info(self) -> str:
        """Get current page info: URL, title, viewport size, scroll position.

        Returns
        -------
        str
            A single-line string in the format::

                url='<url>', title='<title>', viewport=<W>x<H>, page=<PW>x<PH>, scroll=(<x>,<y>)

            where ``viewport`` is the visible area (pixels), ``page`` is the
            total scrollable content size (pixels), and ``scroll`` is the
            current scroll offset from the top-left corner (pixels).

        Raises
        ------
        StateError
            If no active page is available.
        OperationError
            If page info retrieval fails.
        """
        try:
            logger.info(f"[get_current_page_info] start")

            page_info = await self._get_page_info()
            if page_info is None:
                error_msg = "No active page available"
                logger.error(f"[get_current_page_info] {error_msg}")
                _raise_operation_error(error_msg)
            result = (
                f"url={page_info.url!r}, title={page_info.title!r}, "
                f"viewport={page_info.viewport_width}x{page_info.viewport_height}, "
                f"page={page_info.page_width}x{page_info.page_height}, "
                f"scroll=({page_info.scroll_x},{page_info.scroll_y})"
            )
            logger.info(f"[get_current_page_info] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to get current page info: {str(e)}"
            logger.error(f"[get_current_page_info] {error_msg}")
            _raise_operation_error(error_msg)

    async def press_key(self, key: str) -> str:
        """Press a keyboard key or combination (e.g., "Enter", "Control+A").

        Parameters
        ----------
        key : str
            Key name or combination (e.g., "Tab", "Control+C", "Shift+Tab").

        Returns
        -------
        str
            Result message.
        """
        try:
            logger.info(f"[press_key] start key={key}")

            key = key.strip()
            if not key:
                _raise_invalid_input("Key name cannot be empty", code="KEY_EMPTY")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            await page.keyboard.press(key)
            result = f"Pressed key: {key}"
            logger.info(f"[press_key] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to press key: {str(e)}"
            logger.error(f"[press_key] {error_msg}")
            _raise_operation_error(error_msg)

    async def scroll_to_text(self, text: str) -> str:
        """Scroll the page to make the specified text visible.

        Finds the first occurrence of the text on the page and scrolls it
        into view.  Unlike :meth:`scroll_element_into_view_by_ref`, this
        method locates elements by their visible text content rather than a
        snapshot ref.  When the text is not found or has no bounding box,
        a "not found" message is returned (no exception is raised).

        Parameters
        ----------
        text : str
            Text string to find and scroll to (case-sensitive, substring match).

        Returns
        -------
        str
            "Scrolled to text: <text>" on success, or
            "Text not found: <text>" / "Text '<text>' not found or not visible"
            when the text cannot be located.

        Raises
        ------
        InvalidInputError
            If ``text`` is empty.
        StateError
            If no active page is available.
        OperationError
            If an unexpected error occurs.
        """
        try:
            logger.info(f"[scroll_to_text] start text={text!r}")

            text = text.strip()
            if not text:
                _raise_invalid_input("Text to find cannot be empty", code="TEXT_EMPTY")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            try:
                locator = page.get_by_text(text, exact=False).first
                bounding_box = await locator.bounding_box(timeout=5000)
                if bounding_box:
                    await locator.scroll_into_view_if_needed()
                    result = f'Scrolled to text: {text}'
                    logger.info(f"[scroll_to_text] done {result}")
                    return result
                else:
                    result = f'Text not found: {text}'
                    logger.warning(f"[scroll_to_text] done {result}")
                    return result
            except Exception:
                result = f"Text '{text}' not found or not visible"
                logger.info(f"[scroll_to_text] done {result}")
                return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to scroll to text: {str(e)}"
            logger.error(f"[scroll_to_text] {error_msg}")
            _raise_operation_error(error_msg)

    async def evaluate_javascript(self, code: str) -> str:
        """Execute JavaScript in page context. **Only run trusted code.**

        Parameters
        ----------
        code : str
            Arrow function format, e.g., "() => document.title".

        Returns
        -------
        str
            Execution result as string.
        """
        try:
            logger.info(f"[evaluate_javascript] start code_preview={code[:100] if code and len(code) > 100 else code!r}")

            code = code.strip()
            if not code:
                _raise_invalid_input("JavaScript code cannot be empty", code="CODE_EMPTY")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            result = await page.evaluate(code)

            if isinstance(result, bool):
                result_str = "True" if result else "False"
                logger.info(f"[evaluate_javascript] done result={result_str!r}")
                return result_str
            elif result is None:
                logger.info(f"[evaluate_javascript] done result=None")
                return "None"
            elif isinstance(result, (int, float)):
                result_str = str(result)
                logger.info(f"[evaluate_javascript] done result={result_str!r}")
                return result_str
            else:
                result_str = str(result)
                logger.info(f"[evaluate_javascript] done result_preview={result_str[:200]!r} result_len={len(result_str)}")
                return result_str
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to execute JavaScript: {str(e)}"
            logger.error(f"[evaluate_javascript] {error_msg}")
            _raise_operation_error(error_msg)

    # ==================== Tab Management ====================

    async def new_tab(
        self,
        url: Optional[str] = None,
        wait_until: str = "domcontentloaded",
        timeout: Optional[float] = None,
    ) -> str:
        """Create a new browser tab and optionally navigate to a URL.

        The new tab becomes the active tab.  Use :meth:`get_tabs` to list all
        open tabs and retrieve the new tab's page_id.

        Parameters
        ----------
        url : Optional[str], optional
            URL to open in the new tab. Auto-prepends "http://" if the
            protocol is missing. If None or empty, creates a blank tab.
        wait_until : str, default "domcontentloaded"
            When to consider navigation complete (only used when url is provided):
            - "domcontentloaded": DOM parsed (fast, good for modern SPAs).
            - "load": Full page load including images/styles.
            - "networkidle": No network activity for 500ms (may timeout on SPAs).
            - "commit": Response received from server.
        timeout : float, optional
            Maximum time in seconds for navigation. Defaults to Playwright's 30s.

        Returns
        -------
        str
            "Opened new tab <page_id> at <url>" when url is provided, or
            "Created new blank tab <page_id>" for a blank tab.

        Raises
        ------
        StateError
            If the browser has not been started yet. Call ``navigate_to()``
            first to open a page, then use this method to create additional tabs.
        OperationError
            If tab creation or navigation fails.
        """
        if self._playwright is None:
            _raise_state_error(
                "Browser is not started. Use navigate_to() to open a page first, then you can create additional tabs.",
                code="BROWSER_NOT_STARTED",
            )

        try:
            logger.info(f"[new_tab] start url={url}")

            if url is not None:
                url = url.strip()
                if not url:
                    url = None

            if url:
                url_lower = url.lower()
                has_scheme = "://" in url or url_lower.startswith(("data:", "about:"))
                if not has_scheme:
                    if not url.startswith("/"):
                        url = f"http://{url}"
                    # else: URLs starting with '/' are absolute paths; passed as-is and will
                    # fail at navigation time with a clear Playwright error (intentional).

            page = await self._new_page(url, wait_until=wait_until, timeout=timeout)
            page_id = generate_page_id(page)
            if url:
                result = f"Opened new tab {page_id} at {page.url}"
            else:
                result = f"Created new blank tab {page_id}"
            logger.info(f"[new_tab] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to create new tab: {str(e)}"
            logger.error(f"[new_tab] {error_msg}")
            _raise_operation_error(error_msg)

    async def get_tabs(self) -> str:
        """Get information about all open tabs.

        Returns
        -------
        str
            Newline-separated list of tab info strings, each containing
            page_id, url, and title. The active tab is marked with "(active)".
        """
        try:
            logger.info(f"[get_tabs] start")

            current_page = await self.get_current_page()
            current_id = generate_page_id(current_page) if current_page else None
            page_descs = await self.get_all_page_descs()
            lines = []
            for desc in page_descs:
                line = model_to_llm_string(desc)
                if desc.page_id == current_id:
                    line += " (active)"
                lines.append(line)
            logger.info(f"[get_tabs] done tabs={len(lines)}")
            if not lines:
                return "No open tabs"
            return "\n".join(lines)
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to get tabs info: {str(e)}"
            logger.error(f"[get_tabs] {error_msg}")
            _raise_operation_error(error_msg)

    async def switch_tab(self, page_id: str) -> str:
        """Switch to specified tab.

        Parameters
        ----------
        page_id : str
            Target tab's page_id, format: "page_xxxx".

        Returns
        -------
        str
            Operation result message.

        Notes
        -----
        The page_id format is "page_xxxx" where xxxx is a unique identifier.
        Use get_tabs() to retrieve available page_ids.
        """
        try:
            logger.info(f"[switch_tab] start page_id={page_id}")

            success, result = await self.switch_to_page(page_id)
            if not success:
                logger.error(f"[switch_tab] {result}")
                _raise_state_error(
                    result,
                    code="TAB_NOT_FOUND" if "not found" in result.lower() else "INVALID_STATE",
                    details={"page_id": page_id},
                )
            logger.info(f"[switch_tab] done page_id={page_id}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to switch tab: {str(e)}"
            logger.error(f"[switch_tab] {error_msg}")
            _raise_operation_error(error_msg)

    async def close_tab(self, page_id: Optional[str] = None) -> str:
        """Close a tab.

        Parameters
        ----------
        page_id : Optional[str], optional
            page_id of the tab to close. If None, closes the current tab.
            Format: "page_xxxx".

        Returns
        -------
        str
            Operation result message.

        Notes
        -----
        If the closed tab is the current tab, the browser will automatically
        switch to another open tab if available.
        """
        try:
            logger.info(f"[close_tab] start page_id={page_id}")

            result = ""
            if page_id is None:
                page = await self.get_current_page()
                if page is None:
                    _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")
                success, closed_result = await self._close_page(page)
                if not success:
                    logger.error(f"[close_tab] {closed_result}")
                    _raise_state_error(
                        closed_result,
                        code="TAB_CLOSE_FAILED",
                        details={"page_id": page_id},
                    )
                result = closed_result
            else:
                success, closed_result = await self._close_page(page_id)
                if not success:
                    logger.error(f"[close_tab] {closed_result}")
                    _raise_state_error(
                        closed_result,
                        code="TAB_NOT_FOUND" if "not found" in closed_result.lower() else "TAB_CLOSE_FAILED",
                        details={"page_id": page_id},
                    )
                result = closed_result

            logger.info(f"[close_tab] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to close tab: {str(e)}"
            logger.error(f"[close_tab] {error_msg}")
            _raise_operation_error(error_msg)

    # ==================== Browser Control Tools ====================

    async def browser_resize(self, width: int, height: int) -> str:
        """Resize the browser viewport.

        Parameters
        ----------
        width : int
            New viewport width in pixels.
        height : int
            New viewport height in pixels.

        Returns
        -------
        str
            Operation result message.
        """
        try:
            logger.info(f"[browser_resize] start width={width} height={height}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            await page.set_viewport_size({"width": width, "height": height})

            result = f"Browser viewport resized to {width}x{height}"
            logger.info(f"[browser_resize] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to resize browser: {str(e)}"
            logger.error(f"[browser_resize] {error_msg}")
            _raise_operation_error(error_msg)

    # ==================== Wait ====================

    async def _is_text_visible_in_any_frame(
        self, page: "Page", text: str, exact: bool = False,
    ) -> bool:
        """Check whether *text* is visible in any frame (main + all iframes)."""
        for frame in page.frames:
            try:
                locator = frame.get_by_text(text, exact=exact)
                if await locator.count() > 0 and await locator.first.is_visible():
                    return True
            except Exception:
                # Frame may have been detached or navigated away.
                continue
        return False

    async def _wait_for_text_across_frames(
        self,
        page: "Page",
        text: str,
        *,
        gone: bool = False,
        exact: bool = False,
        timeout_ms: float = 30000.0,
    ) -> None:
        """Poll all frames (main + iframes) until *text* appears or disappears.

        Raises ``TimeoutError`` if the condition is not met within *timeout_ms*.
        """
        import time as _time

        no_timeout = timeout_ms <= 0
        deadline = _time.monotonic() + timeout_ms / 1000.0
        poll_interval = 0.2  # 200 ms

        while True:
            found = await self._is_text_visible_in_any_frame(page, text, exact=exact)
            if not gone and found:
                return
            if gone and not found:
                return
            if not no_timeout and _time.monotonic() >= deadline:
                action = "disappear" if gone else "appear"
                raise TimeoutError(
                    f"Locator.wait_for: Timeout {timeout_ms:.0f}ms exceeded. "
                    f"Text '{text}' did not {action}."
                )
            await asyncio.sleep(poll_interval)

    async def wait_for(
        self,
        time_seconds: Optional[float] = None,
        text: Optional[str] = None,
        text_gone: Optional[str] = None,
        selector: Optional[str] = None,
        state: str = "visible",
        timeout: float = 30.0,
    ) -> str:
        """Wait for a condition: time delay, text appearance/disappearance, or element state.

        **Priority**: Only ONE condition is used: time_seconds > text > text_gone > selector.

        Parameters
        ----------
        time_seconds : float, optional
            Fixed delay in SECONDS (e.g., 2.5 = 2.5 seconds, max 60).
            If provided, ignores all other parameters.
        text : str, optional
            Wait until this text appears and is visible on the page.
        text_gone : str, optional
            Wait until this text disappears from the page.
        selector : str, optional
            CSS selector to wait for (e.g., "#submit-btn", ".loading-spinner").
        state : str, optional
            Element state when using selector: "visible" (default), "hidden",
            "attached", "detached".
        timeout : float, optional
            Maximum wait time in SECONDS for text/selector conditions.
            Default is 30.0. Does not apply to ``time_seconds``.
            Setting ``timeout=0`` disables the timeout (waits indefinitely).

        Returns
        -------
        str
            Success: "Waited for X seconds" or "Text 'X' appeared on the page"
            Failure: "Wait condition not met: {error}"

        Examples
        --------
        wait_for(time_seconds=3)  # Wait 3 seconds
        wait_for(text="Success")  # Wait for "Success" to appear
        wait_for(text_gone="Loading...")  # Wait for loading text to disappear
        wait_for(selector=".modal", state="visible")  # Wait for modal
        """
        try:
            logger.info(f"[wait_for] start time_seconds={time_seconds} text={text} text_gone={text_gone} selector={selector}")

            if time_seconds is not None:
                actual_seconds = min(max(float(time_seconds), 0), 60)
                await asyncio.sleep(actual_seconds)
                result = f"Waited for {actual_seconds} seconds"
                logger.info(f"[wait_for] done {result}")
                return result

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            timeout_ms = timeout * 1000.0

            if text is not None:
                await self._wait_for_text_across_frames(
                    page, text, gone=False, timeout_ms=timeout_ms,
                )
                result = f"Text '{text}' appeared on the page"
                logger.info(f"[wait_for] done {result}")
                return result

            if text_gone is not None:
                await self._wait_for_text_across_frames(
                    page, text_gone, gone=True, timeout_ms=timeout_ms,
                )
                result = f"Text '{text_gone}' disappeared from the page"
                logger.info(f"[wait_for] done {result}")
                return result

            if selector is not None:
                locator = page.locator(selector)
                await locator.first.wait_for(state=state, timeout=timeout_ms)
                result = f"Selector '{selector}' reached state '{state}'"
                logger.info(f"[wait_for] done {result}")
                return result

            _raise_invalid_input("No wait condition specified", code="INVALID_WAIT_CONDITION")
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Wait condition not met: {str(e)}"
            logger.error(f"[wait_for] {error_msg}")
            _raise_operation_error(error_msg)

    # ==================== Element Action Tools (by ref) ====================

    async def input_text_by_ref(
        self,
        ref: str,
        text: str,
        clear: bool = True,
        is_secret: bool = False,
        slowly: bool = False,
        submit: bool = False,
    ) -> str:
        """Input text into a specific element identified by its snapshot ref.

        This is the primary text-input tool for interacting with form fields by
        ref.  Unlike :meth:`type_text` and :meth:`insert_text` which type into
        the currently focused element, this method targets the element directly
        via its ref and handles both visible and hidden (shadow-DOM) inputs.

        Comparison:

        - ``input_text_by_ref`` — target by ref; clears first; handles hidden
          inputs via JS; fires ``input``/``change`` events; **preferred**.
        - :meth:`type_text` — no ref; types into focused element
          character-by-character via ``keyboard.press``; triggers per-character
          ``keydown``/``keyup`` events (needed for autocomplete widgets).
        - :meth:`insert_text` — no ref; pastes into focused element in one shot
          without key events; fastest for long strings.

        Parameters
        ----------
        ref : str
            Element ref from snapshot (e.g., "8d4b03a9"). Obtain refs by
            calling :meth:`get_snapshot_text` first.
        text : str
            Text to input. An empty string clears the field when ``clear=True``.
        clear : bool, optional
            Clear existing field content before typing. Default True.
            When False, text is appended to whatever is already in the field.
        is_secret : bool, optional
            When True, the result message shows a generic confirmation instead
            of the actual text (for passwords and tokens). Default False.
        slowly : bool, optional
            When True, types character-by-character with ~100 ms delay between
            keystrokes, triggering per-character ``keydown``/``keyup`` events.
            Use for fields with live key-event handlers (e.g. autocomplete).
            Falls back to JS value-set if the element is not visible. Default False.
        submit : bool, optional
            Press Enter after typing to submit the form. Default False.

        Returns
        -------
        str
            "Input text '<text>'" on success, or "Successfully input sensitive
            information" when ``is_secret=True``.  Appended with " and
            submitted" when ``submit=True``.

        Raises
        ------
        StateError
            If the ref cannot be resolved (element gone or page changed).
        OperationError
            If text input fails.
        """
        try:
            locator = await self.get_element_by_ref(ref)
            if locator is None:
                msg = f'Element ref {ref} is not available - page may have changed. Please try refreshing browser state.'
                logger.warning(f'[input_text_by_ref] {msg}')
                _raise_state_error(msg, code="REF_NOT_AVAILABLE", details={"ref": ref})

            is_vis = await locator.is_visible()

            _js_set_value = (
                "(el, v) => {"
                "  if ('value' in el) {"
                f"    el.value = {'el.value + v' if not clear else 'v'};"
                "    el.dispatchEvent(new Event('input', {bubbles: true}));"
                "    el.dispatchEvent(new Event('change', {bubbles: true}));"
                "  } else if (el.isContentEditable) {"
                f"    el.textContent = {'el.textContent + v' if not clear else 'v'};"
                "    el.dispatchEvent(new Event('input', {bubbles: true}));"
                "  }"
                "}"
            )

            if clear:
                if is_vis:
                    await locator.clear()
                else:
                    logger.debug("[input_text_by_ref] is_visible()=False; clearing via JS")
                    await locator.evaluate(
                        "(el) => { if ('value' in el) el.value = ''; "
                        "else if (el.isContentEditable) el.textContent = ''; }"
                    )

            if slowly:
                if is_vis:
                    await locator.focus()
                    await locator.type(text, delay=100)
                else:
                    logger.debug("[input_text_by_ref] is_visible()=False; setting value via JS (slowly mode unavailable)")
                    await locator.evaluate("el => el.focus()")
                    await locator.evaluate(_js_set_value, text)
            else:
                if is_vis and clear:
                    await locator.fill(text)
                else:
                    if not is_vis:
                        logger.debug("[input_text_by_ref] is_visible()=False; setting value via JS")
                    await locator.evaluate(_js_set_value, text)

            if submit:
                if not is_vis:
                    await locator.evaluate("el => el.focus()")
                page = await self.get_current_page()
                if page:
                    await page.keyboard.press("Enter")

            msg = f"Input text '{text}'"
            if is_secret:
                msg = "Successfully input sensitive information"
            if submit:
                msg += " and submitted"

            logger.info(f'[input_text_by_ref] {msg}')
            return msg

        except BridgicBrowserError:
            raise
        except Exception as e:
            logger.error(f'[input_text_by_ref] Failed to input text: {type(e).__name__}: {e}')
            error_msg = f'Failed to input text to element {ref}: {e}'
            _raise_operation_error(error_msg)

    async def click_element_by_ref(self, ref: str) -> str:
        """Click an element identified by its snapshot ref.

        Prefer this over :meth:`mouse_click` for accessible elements — it uses
        the snapshot ref to target the element rather than screen coordinates,
        which is more reliable when pages scroll or re-render.

        Handles covered and hidden elements automatically:

        - If the element is covered by another element (e.g. a Stripe accordion
          overlay), the intercepting element is clicked instead.
        - If the element has a bounding box but ``is_visible()`` is False
          (shadow-DOM slot), a ``click`` event is dispatched directly.
        - If the element has no bounding box and is not visible, a ``click``
          event is dispatched.

        Parameters
        ----------
        ref : str
            Element ref from snapshot (e.g., "8d4b03a9"). Obtain refs by
            calling :meth:`get_snapshot_text` first.

        Returns
        -------
        str
            "Clicked element <ref>" on success.

        Raises
        ------
        StateError
            If the ref cannot be resolved (element gone or page changed).
        OperationError
            If the click fails.
        """
        try:
            locator = await self.get_element_by_ref(ref)
            if locator is None:
                msg = f'Element ref {ref} is not available - page may have changed. Please try refreshing browser state.'
                logger.warning(f'[click_element_by_ref] {msg}')
                _raise_state_error(msg, code="REF_NOT_AVAILABLE", details={"ref": ref})

            bbox = await locator.bounding_box()
            if bbox is not None:
                cx = bbox["x"] + bbox["width"] / 2
                cy = bbox["y"] + bbox["height"] / 2

                if not await locator.is_visible():
                    logger.debug(
                        "[click_element_by_ref] element has bbox but is_visible()=False "
                        "(likely shadow-DOM slot); using dispatch_event click"
                    )
                    await locator.dispatch_event("click")
                else:
                    covered = await locator.evaluate(
                        f"(el) => {{ if (window.parent !== window) return false; "
                        f"const t = document.elementFromPoint({cx}, {cy}); "
                        f"return !!t && t !== el && !el.contains(t) && !t.contains(el); }}"
                    )
                    if covered:
                        logger.debug("[click_element_by_ref] covered at (%.1f, %.1f), clicking intercepting element", cx, cy)
                        page = await self.get_current_page()
                        if page:
                            await page.evaluate(f"document.elementFromPoint({cx}, {cy})?.click()")
                        else:
                            await locator.evaluate("el => el.click()")
                    else:
                        await locator.click()
            else:
                if not await locator.is_visible():
                    logger.debug("[click_element_by_ref] bbox=None and is_visible()=False; using dispatch_event click")
                    await locator.dispatch_event("click")
                else:
                    await locator.click()

            msg = f'Clicked element {ref}'
            logger.info(f'[click_element_by_ref] {msg}')
            return msg

        except BridgicBrowserError:
            raise
        except Exception as e:
            logger.error(f'[click_element_by_ref] Failed to click element: {type(e).__name__}: {e}')
            error_msg = f'Failed to click element {ref}: {str(e)}'
            _raise_operation_error(error_msg)

    async def get_dropdown_options_by_ref(self, ref: str) -> str:
        """Get all options from a dropdown/select element.

        Parameters
        ----------
        ref : str
            Element ref from snapshot (e.g., "8d4b03a9").

        Returns
        -------
        str
            Numbered list: "1. Option Text (value: val)"
        """
        try:
            locator = await self.get_element_by_ref(ref)
            if locator is None:
                msg = f'Element ref {ref} is not available - page may have changed. Please try refreshing browser state.'
                logger.warning(f'[get_dropdown_options_by_ref] {msg}')
                _raise_state_error(msg, code="REF_NOT_AVAILABLE", details={"ref": ref})

            page = await self.get_current_page()
            options = await _get_dropdown_option_locators(page, locator)
            if not options:
                _raise_state_error('This dropdown has no options', code='ELEMENT_STATE_ERROR')

            # Detect currently selected option(s)
            selected_values = set()
            try:
                selected_values = set(await locator.evaluate(
                    "el => el.tagName === 'SELECT' ? Array.from(el.selectedOptions).map(o => o.value) : []"
                ))
            except Exception:
                pass

            option_texts = []
            for i, option in enumerate(options):
                text = await option.text_content()
                value = await option.get_attribute("value")
                if text:
                    line = f"{i + 1}. {text.strip()}" + (f" (value: {value})" if value else "")
                    if value in selected_values:
                        line += " [selected]"
                    option_texts.append(line)

            result = '\n'.join(option_texts) if option_texts else 'Unable to get dropdown options'
            logger.info(f'[get_dropdown_options_by_ref] Retrieved dropdown options')
            return result

        except BridgicBrowserError:
            raise
        except Exception as e:
            logger.error(f'[get_dropdown_options_by_ref] Failed to get dropdown options: {type(e).__name__}: {e}')
            error_msg = f'Failed to get dropdown options for element {ref}: {str(e)}'
            _raise_operation_error(error_msg)

    async def select_dropdown_option_by_ref(self, ref: str, text: str) -> str:
        """Select an option from a dropdown element by its visible text or value.

        Supports native ``<select>`` elements and custom ARIA listbox/option
        dropdowns (including portalized ones linked via ``aria-controls`` or
        ``aria-owns``).

        Matching order for custom dropdowns (non-native ``<select>``):

        1. Exact match on option visible text.
        2. Exact match on option ``value`` attribute.
        3. Case-insensitive match on visible text.
        4. Case-insensitive match on ``value`` attribute.

        For native ``<select>`` elements, Playwright's ``select_option`` is
        used (tries ``value`` first, then ``label``).

        Call :meth:`get_dropdown_options_by_ref` first to see available options
        and their values.

        Parameters
        ----------
        ref : str
            Element ref of the dropdown from snapshot (e.g., "1f79fe5e").
        text : str
            Visible option text or ``value`` attribute to select.

        Returns
        -------
        str
            "Selected option: <text>" on success.

        Raises
        ------
        StateError
            If the ref cannot be resolved.
        OperationError
            If no matching option is found or the click fails.
        """
        try:
            locator = await self.get_element_by_ref(ref)
            if locator is None:
                msg = f'Element ref {ref} is not available - page may have changed. Please try refreshing browser state.'
                logger.warning(f'[select_dropdown_option_by_ref] {msg}')
                _raise_state_error(msg, code="REF_NOT_AVAILABLE", details={"ref": ref})

            tag_name = await locator.evaluate("el => el.tagName.toLowerCase()")

            if tag_name == "select":
                try:
                    await locator.select_option(value=text)
                except Exception:
                    await locator.select_option(label=text)
            else:
                normalized_target = text.strip()
                page = await self.get_current_page()
                options = await _get_dropdown_option_locators(page, locator)

                if not options:
                    if await locator.is_visible():
                        await locator.click()
                    else:
                        await locator.dispatch_event("click")
                    options = await _get_dropdown_option_locators(page, locator)

                if not options:
                    _raise_operation_error(
                        f'Failed to find dropdown options for element {ref}',
                        code='ELEMENT_STATE_ERROR',
                        details={"ref": ref, "text": text},
                    )

                chosen_option = None
                for option in options:
                    option_text = (await option.text_content() or "").strip()
                    option_value = (await option.get_attribute("value") or "").strip()
                    if option_text == normalized_target or option_value == normalized_target:
                        chosen_option = option
                        break

                if chosen_option is None:
                    lowered_target = normalized_target.lower()
                    for option in options:
                        option_text = (await option.text_content() or "").strip()
                        option_value = (await option.get_attribute("value") or "").strip()
                        if option_text.lower() == lowered_target or option_value.lower() == lowered_target:
                            chosen_option = option
                            break

                if chosen_option is None:
                    _raise_operation_error(f'Failed to find dropdown option "{text}" for element {ref}', code='ELEMENT_STATE_ERROR')

                if await chosen_option.is_visible():
                    await chosen_option.click()
                else:
                    await chosen_option.dispatch_event("click")

            msg = f'Selected option: {text}'
            logger.info(f'[select_dropdown_option_by_ref] {msg}')
            return msg

        except BridgicBrowserError:
            raise
        except Exception as e:
            logger.error(f'[select_dropdown_option_by_ref] Failed to select dropdown option: {type(e).__name__}: {e}')
            error_msg = f'Failed to select dropdown option "{text}" for element {ref}: {str(e)}'
            _raise_operation_error(error_msg)

    async def hover_element_by_ref(self, ref: str) -> str:
        """Hover mouse over an element by ref.

        Parameters
        ----------
        ref : str
            Element ref from snapshot (e.g., "d8ae31b4").

        Returns
        -------
        str
            Result message.
        """
        try:
            locator = await self.get_element_by_ref(ref)
            if locator is None:
                msg = f'Element ref {ref} is not available - page may have changed. Please try refreshing browser state.'
                logger.warning(f'[hover_element_by_ref] {msg}')
                _raise_state_error(msg, code="REF_NOT_AVAILABLE", details={"ref": ref})

            bbox = await locator.bounding_box()
            if bbox is not None:
                cx = bbox["x"] + bbox["width"] / 2
                cy = bbox["y"] + bbox["height"] / 2

                if not await locator.is_visible():
                    logger.debug(
                        "[hover_element_by_ref] element has bbox but is_visible()=False "
                        "(likely shadow-DOM slot); moving mouse to coordinates directly"
                    )
                    page = await self.get_current_page()
                    if page:
                        await page.mouse.move(cx, cy)
                    else:
                        await locator.hover(force=True)
                else:
                    covered = await locator.evaluate(
                        f"(el) => {{ if (window.parent !== window) return false; "
                        f"const t = document.elementFromPoint({cx}, {cy}); "
                        f"return !!t && t !== el && !el.contains(t) && !t.contains(el); }}"
                    )
                    if covered:
                        logger.debug("[hover_element_by_ref] covered at (%.1f, %.1f), moving mouse to coordinates", cx, cy)
                        page = await self.get_current_page()
                        if page:
                            await page.mouse.move(cx, cy)
                        else:
                            await locator.hover(force=True)
                    else:
                        await locator.hover()
            else:
                if not await locator.is_visible():
                    msg = (
                        f'Could not hover element {ref}: element is not visible and has '
                        'no screen coordinates'
                    )
                    logger.warning(f'[hover_element_by_ref] {msg}')
                    _raise_operation_error(
                        msg,
                        code="ELEMENT_NOT_VISIBLE",
                        details={"ref": ref},
                    )
                else:
                    await locator.hover()

            msg = f'Hovered over element ref {ref}'
            logger.info(f'[hover_element_by_ref] {msg}')
            return msg

        except BridgicBrowserError:
            raise
        except Exception as e:
            logger.error(f'[hover_element_by_ref] Failed to hover element: {type(e).__name__}: {e}')
            error_msg = f'Failed to hover element {ref}: {str(e)}'
            _raise_operation_error(error_msg)

    async def focus_element_by_ref(self, ref: str) -> str:
        """Focus an element by ref.

        Parameters
        ----------
        ref : str
            Element ref from snapshot (e.g., "1fe9cf5e").

        Returns
        -------
        str
            Result message.
        """
        try:
            locator = await self.get_element_by_ref(ref)
            if locator is None:
                msg = f'Element ref {ref} is not available - page may have changed. Please try refreshing browser state.'
                logger.warning(f'[focus_element_by_ref] {msg}')
                _raise_state_error(msg, code="REF_NOT_AVAILABLE", details={"ref": ref})

            if await locator.is_visible():
                await locator.focus()
            else:
                logger.debug(
                    "[focus_element_by_ref] is_visible()=False (likely shadow-DOM slot); "
                    "using el.focus() via evaluate to properly update document.activeElement"
                )
                await locator.evaluate("el => el.focus()")

            msg = f'Focused element ref {ref}'
            logger.info(f'[focus_element_by_ref] {msg}')
            return msg

        except BridgicBrowserError:
            raise
        except Exception as e:
            logger.error(f'[focus_element_by_ref] Failed to focus element: {type(e).__name__}: {e}')
            error_msg = f'Failed to focus element {ref}: {str(e)}'
            _raise_operation_error(error_msg)

    async def evaluate_javascript_on_ref(self, ref: str, code: str) -> str:
        """Execute JavaScript on an element.

        The element is passed as the first argument to the function.

        Parameters
        ----------
        ref : str
            Element ref from snapshot (e.g., "8d4b03a9").
        code : str
            Arrow function receiving the element as first arg, e.g., "el => el.textContent".

        Returns
        -------
        str
            Execution result as string.
        """
        try:
            locator = await self.get_element_by_ref(ref)
            if locator is None:
                msg = f'Element ref {ref} is not available - page may have changed. Please try refreshing browser state.'
                logger.warning(f'[evaluate_javascript_on_ref] {msg}')
                _raise_state_error(msg, code="REF_NOT_AVAILABLE", details={"ref": ref})

            result = await locator.evaluate(code)

            if result is None:
                result_str = "null"
            elif isinstance(result, str):
                result_str = result
            else:
                result_str = str(result)

            logger.info(f'[evaluate_javascript_on_ref] Execution successful, result length: {len(result_str)}')
            return result_str

        except BridgicBrowserError:
            raise
        except Exception as e:
            logger.error(f'[evaluate_javascript_on_ref] Failed to execute JavaScript: {type(e).__name__}: {e}')
            error_msg = f'Failed to execute JavaScript on element {ref}: {str(e)}'
            _raise_operation_error(error_msg)

    async def upload_file_by_ref(self, ref: str, file_path: str) -> str:
        """Upload a file to a file input element by ref.

        Parameters
        ----------
        ref : str
            Element ref from snapshot (e.g., "1f79fe5e").
        file_path : str
            Path to the file to upload.

        Returns
        -------
        str
            Result message.
        """
        try:
            if not os.path.exists(file_path):
                msg = f'File {file_path} does not exist'
                logger.error(f'[upload_file_by_ref] {msg}')
                _raise_operation_error(msg, code="NOT_FOUND", details={"path": file_path})

            locator = await self.get_element_by_ref(ref)
            if locator is None:
                msg = f'Element ref {ref} is not available - page may have changed. Please try refreshing browser state.'
                logger.warning(f'[upload_file_by_ref] {msg}')
                _raise_state_error(msg, code="REF_NOT_AVAILABLE", details={"ref": ref})

            tag_name = await locator.evaluate("el => el.tagName.toLowerCase()")
            input_type = await locator.get_attribute("type") if tag_name == "input" else None
            if tag_name != "input" or input_type != "file":
                nested = locator.locator("input[type='file']")
                if await nested.count() > 0:
                    logger.debug(
                        "[upload_file_by_ref] ref %s (%s) is not a file input; "
                        "found nested input[type=file], retargeting",
                        ref, tag_name,
                    )
                    locator = nested.first
                else:
                    msg = f'Element ref {ref} is not a file input element (tag: {tag_name}, type: {input_type})'
                    logger.error(f'[upload_file_by_ref] {msg}')
                    _raise_operation_error(
                        msg,
                        code="ELEMENT_TYPE_MISMATCH",
                        details={"ref": ref, "tag_name": tag_name, "input_type": input_type},
                    )

            await locator.set_input_files(file_path)

            msg = f'Successfully uploaded file to element ref {ref}'
            logger.info(f'[upload_file_by_ref] {msg}')
            return msg

        except BridgicBrowserError:
            raise
        except Exception as e:
            logger.error(f'[upload_file_by_ref] Failed to upload file: {type(e).__name__}: {e}')
            error_msg = f'Failed to upload file to element {ref}: {str(e)}'
            _raise_operation_error(error_msg)

    async def drag_element_by_ref(self, start_ref: str, end_ref: str) -> str:
        """Drag element from start_ref and drop on end_ref.

        Parameters
        ----------
        start_ref : str
            Element ref to drag (e.g., "8d4b03a9").
        end_ref : str
            Element ref of drop target (e.g., "1f79fe5e").

        Returns
        -------
        str
            Result message.
        """
        try:
            logger.info(f'[drag_element_by_ref] start start_ref={start_ref} end_ref={end_ref}')

            source_locator = await self.get_element_by_ref(start_ref)
            if source_locator is None:
                msg = f'Source element ref {start_ref} is not available - page may have changed.'
                logger.warning(f'[drag_element_by_ref] {msg}')
                _raise_state_error(msg, code="REF_NOT_AVAILABLE", details={"start_ref": start_ref})

            target_locator = await self.get_element_by_ref(end_ref)
            if target_locator is None:
                msg = f'Target element ref {end_ref} is not available - page may have changed.'
                logger.warning(f'[drag_element_by_ref] {msg}')
                _raise_state_error(msg, code="REF_NOT_AVAILABLE", details={"end_ref": end_ref})

            await source_locator.drag_to(target_locator)

            msg = f'Dragged element {start_ref} to {end_ref}'
            logger.info(f'[drag_element_by_ref] {msg}')
            return msg

        except BridgicBrowserError:
            raise
        except Exception as e:
            logger.error(f'[drag_element_by_ref] Failed to drag element: {type(e).__name__}: {e}')
            error_msg = f'Failed to drag element from {start_ref} to {end_ref}: {str(e)}'
            _raise_operation_error(error_msg)

    async def check_checkbox_or_radio_by_ref(self, ref: str) -> str:
        """Check a checkbox or radio button (or ARIA equivalent) by ref.

        Works for:

        - Native ``<input type="checkbox">`` and ``<input type="radio">``
          elements.
        - Custom ARIA checkboxes/toggles (``role="checkbox"`` with
          ``aria-checked``).

        This method is idempotent: if the element is already checked, it
        returns immediately without error (see result message).

        After clicking, the checked state is verified.  If it remains
        unchecked, :exc:`OperationError` is raised.

        Parameters
        ----------
        ref : str
            Element ref from snapshot (e.g., "8d4b03a9"). Obtain refs by
            calling :meth:`get_snapshot_text` first.

        Returns
        -------
        str
            "Checked element <ref> (confirmed: checked=true)" on success, or
            "Checked element <ref> (was already checked)" if already checked.

        Raises
        ------
        StateError
            If the ref cannot be resolved (element gone or page changed).
        OperationError
            If the element state is still unchecked after the interaction.
        """
        try:
            logger.info(f'[check_checkbox_or_radio_by_ref] start ref={ref}')

            locator = await self.get_element_by_ref(ref)
            if locator is None:
                msg = f'Element ref {ref} is not available - page may have changed. Please try refreshing browser state.'
                logger.warning(f'[check_checkbox_or_radio_by_ref] {msg}')
                _raise_state_error(msg, code="REF_NOT_AVAILABLE", details={"ref": ref})

            is_native = await _is_native_checkbox_or_radio(locator)
            already_checked = await _is_checked(locator)
            if already_checked:
                msg = f'Checked element {ref} (was already checked)'
                logger.info(f'[check_checkbox_or_radio_by_ref] {msg}')
                return msg

            bbox = await locator.bounding_box()
            if is_native:
                if bbox is not None:
                    cx = bbox["x"] + bbox["width"] / 2
                    cy = bbox["y"] + bbox["height"] / 2

                    if not await locator.is_visible():
                        logger.debug(
                            "[check_checkbox_or_radio_by_ref] native input has bbox but is_visible()=False; "
                            "using dispatch_event click"
                        )
                        await locator.dispatch_event("click")
                    else:
                        covered = await locator.evaluate(
                            f"(el) => {{ if (window.parent !== window) return false; "
                            f"const t = document.elementFromPoint({cx}, {cy}); "
                            f"return !!t && t !== el && !el.contains(t) && !t.contains(el); }}"
                        )
                        if covered:
                            logger.debug("[check_checkbox_or_radio_by_ref] covered at (%.1f, %.1f), clicking intercepting element", cx, cy)
                            page = await self.get_current_page()
                            if page:
                                await page.evaluate(f"document.elementFromPoint({cx}, {cy})?.click()")
                            else:
                                await locator.check(force=True)
                        else:
                            await locator.check()
                else:
                    if not await locator.is_visible():
                        logger.debug("[check_checkbox_or_radio_by_ref] native input bbox=None and is_visible()=False; using dispatch_event click")
                        await locator.dispatch_event("click")
                    else:
                        await locator.check()
            else:
                page = await self.get_current_page()
                await _click_checkable_target(page, locator, bbox)

            if not await _is_checked(locator):
                msg = f'Failed to check element {ref}: state is still unchecked'
                logger.warning(f'[check_checkbox_or_radio_by_ref] {msg}')
                _raise_operation_error(
                    msg,
                    code="ELEMENT_STATE_ERROR",
                    details={"ref": ref, "expected": "checked"},
                )

            msg = f'Checked element {ref} (confirmed: checked=true)'
            logger.info(f'[check_checkbox_or_radio_by_ref] {msg}')
            return msg

        except BridgicBrowserError:
            raise
        except Exception as e:
            logger.error(f'[check_checkbox_or_radio_by_ref] Failed to check element: {type(e).__name__}: {e}')
            error_msg = f'Failed to check element {ref}: {str(e)}'
            _raise_operation_error(error_msg)

    async def uncheck_checkbox_by_ref(self, ref: str) -> str:
        """Uncheck a checkbox by ref.

        Parameters
        ----------
        ref : str
            Element ref from snapshot (e.g., "1f79fe5e").

        Returns
        -------
        str
            Result message.

        Notes
        -----
        This method is idempotent: if the element is already unchecked, it
        returns immediately without error.

        Radio buttons cannot be unchecked directly (they work in exclusive
        groups — selecting another radio in the group is the correct approach).
        If a radio button ref is passed, this method will attempt the action
        but will NOT raise an error if the state remains checked, and will NOT
        confirm the state change.
        """
        try:
            logger.info(f'[uncheck_checkbox_by_ref] start ref={ref}')

            locator = await self.get_element_by_ref(ref)
            if locator is None:
                msg = f'Element ref {ref} is not available - page may have changed. Please try refreshing browser state.'
                logger.warning(f'[uncheck_checkbox_by_ref] {msg}')
                _raise_state_error(msg, code="REF_NOT_AVAILABLE", details={"ref": ref})

            is_native = await _is_native_checkbox_or_radio(locator)
            already_checked = await _is_checked(locator)
            if not already_checked:
                msg = f'Unchecked element {ref} (was already unchecked)'
                logger.info(f'[uncheck_checkbox_by_ref] {msg}')
                return msg

            bbox = await locator.bounding_box()
            if is_native:
                if bbox is not None:
                    cx = bbox["x"] + bbox["width"] / 2
                    cy = bbox["y"] + bbox["height"] / 2

                    if not await locator.is_visible():
                        logger.debug(
                            "[uncheck_checkbox_by_ref] native input has bbox but is_visible()=False; "
                            "using dispatch_event click"
                        )
                        await locator.dispatch_event("click")
                    else:
                        covered = await locator.evaluate(
                            f"(el) => {{ if (window.parent !== window) return false; "
                            f"const t = document.elementFromPoint({cx}, {cy}); "
                            f"return !!t && t !== el && !el.contains(t) && !t.contains(el); }}"
                        )
                        if covered:
                            logger.debug("[uncheck_checkbox_by_ref] covered at (%.1f, %.1f), clicking intercepting element", cx, cy)
                            page = await self.get_current_page()
                            if page:
                                await page.evaluate(f"document.elementFromPoint({cx}, {cy})?.click()")
                            else:
                                await locator.uncheck(force=True)
                        else:
                            await locator.uncheck()
                else:
                    if not await locator.is_visible():
                        logger.debug("[uncheck_checkbox_by_ref] native input bbox=None and is_visible()=False; using dispatch_event click")
                        await locator.dispatch_event("click")
                    else:
                        await locator.uncheck()
            else:
                page = await self.get_current_page()
                await _click_checkable_target(page, locator, bbox)

            is_native_radio = is_native and (await locator.get_attribute("type") or "").strip().lower() == "radio"
            if not is_native_radio and await _is_checked(locator):
                msg = f'Failed to uncheck element {ref}: state is still checked'
                logger.warning(f'[uncheck_checkbox_by_ref] {msg}')
                _raise_operation_error(
                    msg,
                    code="ELEMENT_STATE_ERROR",
                    details={"ref": ref, "expected": "unchecked"},
                )

            msg = f'Unchecked element {ref} (confirmed: checked=false)'
            logger.info(f'[uncheck_checkbox_by_ref] {msg}')
            return msg

        except BridgicBrowserError:
            raise
        except Exception as e:
            logger.error(f'[uncheck_checkbox_by_ref] Failed to uncheck element: {type(e).__name__}: {e}')
            error_msg = f'Failed to uncheck element {ref}: {str(e)}'
            _raise_operation_error(error_msg)

    async def double_click_element_by_ref(self, ref: str) -> str:
        """Double-click an element by its snapshot ref.

        Fires a ``dblclick`` event.  Handles covered and hidden elements using
        the same strategy as :meth:`click_element_by_ref`.

        Parameters
        ----------
        ref : str
            Element ref from snapshot (e.g., "09ea4f1e"). Obtain refs by
            calling :meth:`get_snapshot_text` first.

        Returns
        -------
        str
            "Double-clicked element <ref>".

        Raises
        ------
        StateError
            If the ref cannot be resolved (element gone or page changed).
        OperationError
            If the double-click fails.
        """
        try:
            logger.info(f'[double_click_element_by_ref] start ref={ref}')

            locator = await self.get_element_by_ref(ref)
            if locator is None:
                msg = f'Element ref {ref} is not available - page may have changed. Please try refreshing browser state.'
                logger.warning(f'[double_click_element_by_ref] {msg}')
                _raise_state_error(msg, code="REF_NOT_AVAILABLE", details={"ref": ref})

            bbox = await locator.bounding_box()
            if bbox is not None:
                cx = bbox["x"] + bbox["width"] / 2
                cy = bbox["y"] + bbox["height"] / 2

                if not await locator.is_visible():
                    logger.debug(
                        "[double_click_element_by_ref] element has bbox but is_visible()=False "
                        "(likely shadow-DOM slot); using dispatch_event dblclick"
                    )
                    await locator.dispatch_event("dblclick")
                else:
                    covered = await locator.evaluate(
                        f"(el) => {{ if (window.parent !== window) return false; "
                        f"const t = document.elementFromPoint({cx}, {cy}); "
                        f"return !!t && t !== el && !el.contains(t) && !t.contains(el); }}"
                    )
                    if covered:
                        logger.debug("[double_click_element_by_ref] covered at (%.1f, %.1f), dispatching dblclick on intercepting element", cx, cy)
                        page = await self.get_current_page()
                        if page:
                            await page.evaluate(
                                f"(function(){{"
                                f"const el=document.elementFromPoint({cx},{cy});"
                                f"if(el)el.dispatchEvent(new MouseEvent('dblclick',{{bubbles:true,cancelable:true,view:window}}));"
                                f"}})()"
                            )
                        else:
                            await locator.dblclick(force=True)
                    else:
                        await locator.dblclick()
            else:
                if not await locator.is_visible():
                    logger.debug("[double_click_element_by_ref] bbox=None and is_visible()=False; using dispatch_event dblclick")
                    await locator.dispatch_event("dblclick")
                else:
                    await locator.dblclick()

            msg = f'Double-clicked element {ref}'
            logger.info(f'[double_click_element_by_ref] {msg}')
            return msg

        except BridgicBrowserError:
            raise
        except Exception as e:
            logger.error(f'[double_click_element_by_ref] Failed to double-click element: {type(e).__name__}: {e}')
            error_msg = f'Failed to double-click element {ref}: {str(e)}'
            _raise_operation_error(error_msg)

    async def scroll_element_into_view_by_ref(self, ref: str) -> str:
        """Scroll the page until the element identified by its ref is in view.

        Unlike :meth:`scroll_to_text` which searches by visible text,
        this method uses the element's snapshot ref for precise targeting.
        Useful before taking an element screenshot or verifying visibility
        of an off-screen element.

        Parameters
        ----------
        ref : str
            Element ref from snapshot (e.g., "1f79fe5e"). Obtain refs by
            calling :meth:`get_snapshot_text` first.

        Returns
        -------
        str
            "Scrolled element <ref> into view".

        Raises
        ------
        StateError
            If the ref cannot be resolved (element gone or page changed).
        OperationError
            If scrolling fails.
        """
        try:
            logger.info(f'[scroll_element_into_view_by_ref] start ref={ref}')

            locator = await self.get_element_by_ref(ref)
            if locator is None:
                msg = f'Element ref {ref} is not available - page may have changed. Please try refreshing browser state.'
                logger.warning(f'[scroll_element_into_view_by_ref] {msg}')
                _raise_state_error(msg, code="REF_NOT_AVAILABLE", details={"ref": ref})

            await locator.scroll_into_view_if_needed()

            msg = f'Scrolled element {ref} into view'
            logger.info(f'[scroll_element_into_view_by_ref] {msg}')
            return msg

        except BridgicBrowserError:
            raise
        except Exception as e:
            logger.error(f'[scroll_element_into_view_by_ref] Failed to scroll element into view: {type(e).__name__}: {e}')
            error_msg = f'Failed to scroll element {ref} into view: {str(e)}'
            _raise_operation_error(error_msg)

    # ==================== Mouse Tools (coordinate-based) ====================

    async def mouse_move(self, x: float, y: float) -> str:
        """Move the mouse to specific coordinates.

        Parameters
        ----------
        x : float
            X coordinate (horizontal position from left).
        y : float
            Y coordinate (vertical position from top).

        Returns
        -------
        str
            Operation result message.
        """
        try:
            logger.info(f"[mouse_move] start x={x} y={y}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            await page.mouse.move(x, y)
            result = f"Moved mouse to coordinates ({x}, {y})"
            logger.info(f"[mouse_move] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to move mouse: {str(e)}"
            logger.error(f"[mouse_move] {error_msg}")
            _raise_operation_error(error_msg)

    async def mouse_click(
        self,
        x: float,
        y: float,
        button: Literal["left", "right", "middle"] = "left",
        click_count: int = 1,
    ) -> str:
        """Click the mouse at specific viewport coordinates.

        Use this for elements that are not in the accessibility tree (e.g.,
        canvas-based UIs, custom rendered widgets).  For accessible elements
        identified by a snapshot ref, prefer :meth:`click_element_by_ref`
        which handles covered/hidden elements automatically.

        Parameters
        ----------
        x : float
            X coordinate in pixels (horizontal, measured from the left edge
            of the viewport).
        y : float
            Y coordinate in pixels (vertical, measured from the top edge of
            the viewport).
        button : {"left", "right", "middle"}, optional
            Mouse button to click. Default is "left".
        click_count : int, optional
            Number of clicks. Default is 1. Use 2 for a double-click.

        Returns
        -------
        str
            "Mouse clicked at (<x>, <y>) with <button> button" (or
            "double-clicked" when click_count is 2).

        Raises
        ------
        StateError
            If no active page is available.
        OperationError
            If the click fails.
        """
        try:
            logger.info(f"[mouse_click] start x={x} y={y} button={button} click_count={click_count}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            await page.mouse.click(x, y, button=button, click_count=click_count)

            click_type = "double-clicked" if click_count == 2 else "clicked"
            result = f"Mouse {click_type} at ({x}, {y}) with {button} button"
            logger.info(f"[mouse_click] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to click mouse: {str(e)}"
            logger.error(f"[mouse_click] {error_msg}")
            _raise_operation_error(error_msg)

    async def mouse_drag(
        self,
        start_x: float,
        start_y: float,
        end_x: float,
        end_y: float,
    ) -> str:
        """Drag the mouse from one position to another.

        Parameters
        ----------
        start_x : float
            Starting X coordinate.
        start_y : float
            Starting Y coordinate.
        end_x : float
            Ending X coordinate.
        end_y : float
            Ending Y coordinate.

        Returns
        -------
        str
            Operation result message.
        """
        try:
            logger.info(f"[mouse_drag] start from=({start_x}, {start_y}) to=({end_x}, {end_y})")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            await page.mouse.move(start_x, start_y)
            await page.mouse.down()
            await page.mouse.move(end_x, end_y)
            await page.mouse.up()

            result = f"Dragged mouse from ({start_x}, {start_y}) to ({end_x}, {end_y})"
            logger.info(f"[mouse_drag] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to drag mouse: {str(e)}"
            logger.error(f"[mouse_drag] {error_msg}")
            _raise_operation_error(error_msg)

    async def mouse_down(self, button: Literal["left", "right", "middle"] = "left") -> str:
        """Press and hold a mouse button.

        Parameters
        ----------
        button : {"left", "right", "middle"}, optional
            Mouse button to press. Default is "left".

        Returns
        -------
        str
            Operation result message.
        """
        try:
            logger.info(f"[mouse_down] start button={button}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            await page.mouse.down(button=button)
            result = f"Mouse {button} button pressed down"
            logger.info(f"[mouse_down] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to press mouse button: {str(e)}"
            logger.error(f"[mouse_down] {error_msg}")
            _raise_operation_error(error_msg)

    async def mouse_up(self, button: Literal["left", "right", "middle"] = "left") -> str:
        """Release a mouse button.

        Parameters
        ----------
        button : {"left", "right", "middle"}, optional
            Mouse button to release. Default is "left".

        Returns
        -------
        str
            Operation result message.
        """
        try:
            logger.info(f"[mouse_up] start button={button}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            await page.mouse.up(button=button)
            result = f"Mouse {button} button released"
            logger.info(f"[mouse_up] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to release mouse button: {str(e)}"
            logger.error(f"[mouse_up] {error_msg}")
            _raise_operation_error(error_msg)

    async def mouse_wheel(self, delta_x: float = 0, delta_y: float = 0) -> str:
        """Scroll the mouse wheel at the current mouse position.

        Positive delta_y scrolls down, negative delta_y scrolls up.
        Positive delta_x scrolls right, negative delta_x scrolls left.

        Parameters
        ----------
        delta_x : float, optional
            Horizontal scroll amount in pixels. Positive = right, negative = left.
            Default is 0.
        delta_y : float, optional
            Vertical scroll amount in pixels. Positive = down, negative = up.
            Default is 0.

        Returns
        -------
        str
            "Scrolled mouse wheel: delta_x=<delta_x>, delta_y=<delta_y>".

        Raises
        ------
        StateError
            If no active page is available.
        OperationError
            If scrolling fails.
        """
        try:
            logger.info(f"[mouse_wheel] start delta_x={delta_x} delta_y={delta_y}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            await page.mouse.wheel(delta_x=delta_x, delta_y=delta_y)
            result = f"Scrolled mouse wheel: delta_x={delta_x}, delta_y={delta_y}"
            logger.info(f"[mouse_wheel] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to scroll mouse wheel: {str(e)}"
            logger.error(f"[mouse_wheel] {error_msg}")
            _raise_operation_error(error_msg)

    # ==================== Keyboard Tools ====================

    async def type_text(self, text: str, submit: bool = False) -> str:
        """Type text into the currently focused element, one character at a time.

        Each character fires ``keydown``, ``keypress``, and ``keyup`` events,
        which is required for fields with per-keystroke handlers such as
        autocomplete widgets.  This is slower than :meth:`insert_text` for
        long strings.

        An element must already be focused before calling this method (e.g.
        via :meth:`focus_element_by_ref` or by clicking a field first).

        Comparison:

        - :meth:`input_text_by_ref` — target by ref; clears first; handles
          hidden inputs; **preferred** for form filling.
        - ``type_text`` — no ref; requires a pre-focused element; fires per-
          character key events; use when those events are needed.
        - :meth:`insert_text` — no ref; pastes in one shot without key events;
          fastest for long strings.

        Parameters
        ----------
        text : str
            Text to type character by character.
        submit : bool, optional
            Whether to press Enter after typing. Default is False.

        Returns
        -------
        str
            "Typed <N> characters sequentially" (appended with " and submitted"
            when ``submit=True``).

        Raises
        ------
        StateError
            If no active page is available.
        OperationError
            If typing fails.
        """
        try:
            logger.info(f"[type_text] start text_len={len(text)} submit={submit}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            for char in text:
                await page.keyboard.press(char)

            if submit:
                await page.keyboard.press("Enter")

            submit_msg = " and submitted" if submit else ""
            result = f"Typed {len(text)} characters sequentially{submit_msg}"
            logger.info(f"[type_text] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to type sequentially: {str(e)}"
            logger.error(f"[type_text] {error_msg}")
            _raise_operation_error(error_msg)

    async def key_down(self, key: str) -> str:
        """Press and hold a key.

        Parameters
        ----------
        key : str
            Key name to press. Examples: "Shift", "Control", "Alt", "a", "Enter".

        Returns
        -------
        str
            Operation result message.

        Notes
        -----
        Use key_up() to release the key.
        """
        try:
            logger.info(f"[key_down] start key={key}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            await page.keyboard.down(key)
            result = f"Key '{key}' pressed down"
            logger.info(f"[key_down] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to press key down: {str(e)}"
            logger.error(f"[key_down] {error_msg}")
            _raise_operation_error(error_msg)

    async def key_up(self, key: str) -> str:
        """Release a held key.

        Parameters
        ----------
        key : str
            Key name to release. Examples: "Shift", "Control", "Alt", "a", "Enter".

        Returns
        -------
        str
            Operation result message.
        """
        try:
            logger.info(f"[key_up] start key={key}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            await page.keyboard.up(key)
            result = f"Key '{key}' released"
            logger.info(f"[key_up] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to release key: {str(e)}"
            logger.error(f"[key_up] {error_msg}")
            _raise_operation_error(error_msg)

    async def fill_form(
        self,
        fields: List[Dict[str, str]],
        submit: bool = False,
    ) -> str:
        """Fill multiple form fields at once using their snapshot refs.

        Iterates through the fields list and calls Playwright's ``locator.fill()``
        on each.  Fields that fail are collected and reported rather than
        aborting early.  Unlike :meth:`input_text_by_ref`, this method does not
        apply the slowly/clear/is_secret options and does not fall back to JS
        for hidden inputs — use :meth:`input_text_by_ref` for individual fields
        that need those features.

        Parameters
        ----------
        fields : List[Dict[str, str]]
            List of field specifications. Each dict must have:

            - ``"ref"`` : str — element ref from snapshot (e.g., "8d4a07a9").
            - ``"value"`` : str — text to fill into the field.

        submit : bool, optional
            Press Enter after filling all fields. Default is False.

        Returns
        -------
        str
            Summary message in one of two forms:

            - All succeeded: "Filled <N> fields: [ref1, ref2, ...]"
            - Some failed: "Filled <K>/<N> fields. OK: [ref1]. Failed: [ref2: error]"

            Appended with " and submitted" when ``submit=True``.

        Raises
        ------
        InvalidInputError
            If ``fields`` is empty.
        OperationError
            If an unexpected error occurs (individual field failures are
            collected into the result message, not raised).
        """
        try:
            logger.info(f"[fill_form] start fields_count={len(fields)} submit={submit}")

            if not fields:
                _raise_invalid_input("No fields provided to fill", code="INVALID_FIELDS")

            filled_refs = []
            errors = []

            for field in fields:
                ref = field.get("ref")
                value = field.get("value", "")

                if not ref:
                    errors.append("Field missing 'ref' key")
                    continue

                locator = await self.get_element_by_ref(ref)
                if locator is None:
                    errors.append(f"{ref}: not available")
                    continue

                try:
                    await locator.fill(value)
                    filled_refs.append(ref)
                except BridgicBrowserError:
                    raise
                except Exception as e:
                    errors.append(f"{ref}: {str(e)}")

            if submit and filled_refs:
                page = await self.get_current_page()
                if page:
                    await page.keyboard.press("Enter")

            submit_msg = " and submitted" if submit else ""
            if errors:
                result = (
                    f"Filled {len(filled_refs)}/{len(fields)} fields{submit_msg}. "
                    f"OK: [{', '.join(filled_refs)}]. "
                    f"Failed: [{'; '.join(errors)}]"
                )
            else:
                result = f"Filled {len(filled_refs)} fields{submit_msg}: [{', '.join(filled_refs)}]"

            logger.info(f"[fill_form] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to fill form: {str(e)}"
            logger.error(f"[fill_form] {error_msg}")
            _raise_operation_error(error_msg)

    async def insert_text(self, text: str) -> str:
        """Insert text at the current cursor position without per-character key events.

        Pastes the text directly into the currently focused element.  Unlike
        :meth:`type_text`, no ``keydown``/``keyup`` events are fired per character,
        so it is significantly faster for long strings but will not trigger
        handlers that listen to individual keystrokes (e.g., autocomplete widgets
        that react to ``onkeydown``).

        An element must already be focused before calling this method (e.g.
        via :meth:`focus_element_by_ref`).

        Use :meth:`input_text_by_ref` to target a specific element by ref.
        Use :meth:`type_text` when per-character key events must fire.

        Parameters
        ----------
        text : str
            Text to insert at the current cursor position.  Requires an element
            to already be focused (e.g., via :meth:`focus_element_by_ref`).

        Returns
        -------
        str
            "Inserted text (<N> characters)".

        Raises
        ------
        StateError
            If no active page is available.
        OperationError
            If insertion fails.
        """
        try:
            logger.info(f"[insert_text] start text_len={len(text)}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            await page.keyboard.insert_text(text)
            result = f"Inserted text ({len(text)} characters)"
            logger.info(f"[insert_text] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to insert text: {str(e)}"
            logger.error(f"[insert_text] {error_msg}")
            _raise_operation_error(error_msg)

    # ==================== Screenshot and PDF Tools ====================

    async def take_screenshot(
        self,
        filename: Optional[str] = None,
        ref: Optional[str] = None,
        full_page: bool = False,
        type: Literal["png", "jpeg"] = "png",
        quality: Optional[int] = None,
    ) -> str:
        """Take a screenshot of the page or a specific element.

        Parameters
        ----------
        filename : Optional[str], optional
            Path to save the screenshot. If not provided, returns base64-encoded
            image data.
        ref : Optional[str], optional
            Element ref from snapshot to screenshot. If provided, captures only
            that element.
        full_page : bool, optional
            Whether to capture the full scrollable page. Default is False.
            Ignored if ref is provided.
        type : {"png", "jpeg"}, optional
            Image format. Default is "png".
        quality : Optional[int], optional
            Quality for JPEG images (0-100). Only applies when type is "jpeg".

        Returns
        -------
        str
            On success:
            - With filename: "Screenshot saved to: /path/to/file.png"
            - Without filename: Base64 data URL "data:image/png;base64,iVBORw0..."
        
        Raises
        ------
        StateError
            If no active page is available or the provided ref cannot be resolved.
        OperationError
            If screenshot capture fails.
        """
        try:
            logger.info(f"[take_screenshot] start filename={filename} ref={ref} full_page={full_page} type={type}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            screenshot_options = {
                "type": type,
                "full_page": full_page if ref is None else False,
            }

            if type == "jpeg" and quality is not None:
                screenshot_options["quality"] = quality

            if ref is not None:
                locator = await self.get_element_by_ref(ref)
                if locator is None:
                    msg = f'Element ref {ref} is not available - page may have changed.'
                    logger.warning(f'[take_screenshot] {msg}')
                    _raise_state_error(msg, code="REF_NOT_AVAILABLE", details={"ref": ref})
                target = locator
            else:
                target = page

            if filename:
                if not filename.lower().endswith(f".{type}"):
                    filename = f"{filename}.{type}"

                dirname = os.path.dirname(filename)
                if dirname:
                    os.makedirs(dirname, exist_ok=True)

                screenshot_options["path"] = filename
                await target.screenshot(**screenshot_options)
                result = f"Screenshot saved to: {filename}"
            else:
                screenshot_bytes = await target.screenshot(**screenshot_options)
                b64_data = base64.b64encode(screenshot_bytes).decode("utf-8")
                result = f"data:image/{type};base64,{b64_data}"

            logger.info(f"[take_screenshot] done")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to take screenshot: {str(e)}"
            logger.error(f"[take_screenshot] {error_msg}")
            _raise_operation_error(error_msg)

    async def save_pdf(
        self,
        filename: Optional[str] = None,
        display_header_footer: bool = False,
        print_background: bool = True,
        scale: float = 1.0,
        paper_width: Optional[str] = None,
        paper_height: Optional[str] = None,
        margin_top: Optional[str] = None,
        margin_bottom: Optional[str] = None,
        margin_left: Optional[str] = None,
        margin_right: Optional[str] = None,
        landscape: bool = False,
    ) -> str:
        """Save the current page as a PDF file.

        Parameters
        ----------
        filename : Optional[str], optional
            Path to save the PDF.  The ``.pdf`` extension is added automatically
            when missing.  If not provided, saves to a temporary file and
            returns its path.
        display_header_footer : bool, optional
            Whether to display header and footer. Default is False.
        print_background : bool, optional
            Whether to print background graphics. Default is True.
        scale : float, optional
            Scale of the webpage rendering. Valid range is 0.1–2.0.
            Default is 1.0.
        paper_width : Optional[str], optional
            Paper width with units (e.g., "8.5in", "21cm", "215mm").
            Defaults to US Letter (8.5in) when omitted.
        paper_height : Optional[str], optional
            Paper height with units (e.g., "11in", "29.7cm", "297mm").
            Defaults to US Letter (11in) when omitted.
        margin_top : Optional[str], optional
            Top margin with units (e.g., "1in", "2cm"). Default is "1cm".
        margin_bottom : Optional[str], optional
            Bottom margin with units. Default is "1cm".
        margin_left : Optional[str], optional
            Left margin with units. Default is "1cm".
        margin_right : Optional[str], optional
            Right margin with units. Default is "1cm".
        landscape : bool, optional
            Whether to use landscape orientation. Default is False (portrait).

        Returns
        -------
        str
            "PDF saved to: <path>" on success.

        Raises
        ------
        StateError
            If no active page is available.
        OperationError
            If PDF generation fails.

        Notes
        -----
        PDF generation requires Chromium (headless). It is not supported on
        Firefox or WebKit.
        """
        try:
            logger.info(f"[save_pdf] start filename={filename}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            output_path: Optional[str] = None
            temp_output_created = False
            pdf_options: Dict[str, Any] = {
                "display_header_footer": display_header_footer,
                "print_background": print_background,
                "scale": scale,
                "landscape": landscape,
            }

            if paper_width:
                pdf_options["width"] = paper_width
            if paper_height:
                pdf_options["height"] = paper_height
            if margin_top:
                pdf_options["margin"] = pdf_options.get("margin", {})
                pdf_options["margin"]["top"] = margin_top
            if margin_bottom:
                pdf_options["margin"] = pdf_options.get("margin", {})
                pdf_options["margin"]["bottom"] = margin_bottom
            if margin_left:
                pdf_options["margin"] = pdf_options.get("margin", {})
                pdf_options["margin"]["left"] = margin_left
            if margin_right:
                pdf_options["margin"] = pdf_options.get("margin", {})
                pdf_options["margin"]["right"] = margin_right

            if filename:
                if not filename.lower().endswith(".pdf"):
                    filename = f"{filename}.pdf"
                output_path = filename

                dirname = os.path.dirname(filename)
                if dirname:
                    os.makedirs(dirname, exist_ok=True)
            else:
                fd, output_path = tempfile.mkstemp(suffix=".pdf", prefix="browser_page_")
                os.close(fd)
                temp_output_created = True

            pdf_options["path"] = output_path
            try:
                await page.pdf(**pdf_options)
            except Exception:
                # Clean up only auto-generated temp files on failure.
                if temp_output_created and output_path and os.path.exists(output_path):
                    try:
                        os.remove(output_path)
                    except Exception as cleanup_exc:
                        logger.warning(f"[save_pdf] failed to clean temp file {output_path}: {cleanup_exc}")
                raise

            result = f"PDF saved to: {output_path}"
            logger.info(f"[save_pdf] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to save PDF: {str(e)}"
            logger.error(f"[save_pdf] {error_msg}")
            _raise_operation_error(error_msg)

    # ==================== Network and Console Tools ====================

    async def start_console_capture(self) -> str:
        """Start capturing console messages from the current page.

        Returns
        -------
        str
            "Console message capture started".

        Notes
        -----
        - Only one capture session per page; calling again resets the capture
        - Use get_console_messages() to retrieve and optionally clear messages
        """
        try:
            logger.info("[start_console_capture] start")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            page_key = _get_page_key(page)

            if page_key in self._console_handlers:
                try:
                    page.remove_listener("console", self._console_handlers[page_key])
                except Exception:
                    pass

            self._console_messages[page_key] = []

            def handle_console(msg):
                if page_key in self._console_messages:
                    self._console_messages[page_key].append({
                        "type": msg.type,
                        "text": msg.text,
                        "location": str(msg.location) if msg.location else None,
                    })

            page.on("console", handle_console)
            self._console_handlers[page_key] = handle_console

            result = "Console message capture started"
            logger.info(f"[start_console_capture] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to start console capture: {str(e)}"
            logger.error(f"[start_console_capture] {error_msg}")
            _raise_operation_error(error_msg)

    async def stop_console_capture(self) -> str:
        """Stop capturing console messages and clean up resources.

        Returns
        -------
        str
            Operation result message.
        """
        try:
            logger.info("[stop_console_capture] start")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            page_key = _get_page_key(page)

            if page_key not in self._console_handlers:
                _raise_state_error("No active console capture. Use console-start first.", code="NO_ACTIVE_CAPTURE")

            try:
                page.remove_listener("console", self._console_handlers[page_key])
            except Exception:
                pass
            del self._console_handlers[page_key]

            self._console_messages.pop(page_key, None)

            result = "Console capture stopped"
            logger.info(f"[stop_console_capture] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to stop console capture: {str(e)}"
            logger.error(f"[stop_console_capture] {error_msg}")
            _raise_operation_error(error_msg)

    async def get_console_messages(
        self,
        type_filter: Optional[Literal["log", "debug", "info", "error", "warning", "dir", "trace"]] = None,
        clear: bool = True,
    ) -> str:
        """Get captured console messages.

        Parameters
        ----------
        type_filter : Optional[str], optional
            Filter messages by type. Options: "log", "debug", "info", "error",
            "warning", "dir", "trace". Default is None (return all types).
        clear : bool, optional
            Whether to clear the captured buffer after retrieving. Default
            is True (consume-and-clear pattern).

        Returns
        -------
        str
            JSON array string.  Each element is an object with keys:

            - ``"type"`` : str — console message type (e.g. "log", "error").
            - ``"text"`` : str — message text.
            - ``"location"`` : str | null — source location if available.

        Raises
        ------
        StateError
            If no active page is available.
        OperationError
            If retrieval fails.

        Notes
        -----
        Console capture must be started first with :meth:`start_console_capture`.
        Returns an empty JSON array (``"[]"``) if no messages have been captured.
        """
        try:
            logger.info(f"[get_console_messages] start type_filter={type_filter} clear={clear}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            page_key = _get_page_key(page)
            messages = self._console_messages.get(page_key, [])

            if type_filter:
                messages = [m for m in messages if m["type"] == type_filter]

            if clear and page_key in self._console_messages:
                self._console_messages[page_key] = []

            result = json.dumps(messages, indent=2)
            logger.info(f"[get_console_messages] done count={len(messages)}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to get console messages: {str(e)}"
            logger.error(f"[get_console_messages] {error_msg}")
            _raise_operation_error(error_msg)

    async def start_network_capture(self) -> str:
        """Start capturing network requests from the current page.

        Returns
        -------
        str
            "Network request capture started".

        Notes
        -----
        - Call BEFORE navigation to capture all requests from page load
        - Use get_network_requests(include_static=False) to filter out images/CSS/JS
        """
        try:
            logger.info("[start_network_capture] start")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            page_key = _get_page_key(page)

            if page_key in self._network_handlers:
                try:
                    page.remove_listener("request", self._network_handlers[page_key])
                except Exception:
                    pass

            self._network_requests[page_key] = []

            def handle_request(request):
                if page_key in self._network_requests:
                    self._network_requests[page_key].append({
                        "url": request.url,
                        "method": request.method,
                        "resource_type": request.resource_type,
                        "headers": dict(request.headers) if request.headers else {},
                        # TODO: What should we do if the requested data volume is too large? Should we implement pagination?
                        "post_data": request.post_data if request.post_data else None,
                    })

            page.on("request", handle_request)
            self._network_handlers[page_key] = handle_request

            result = "Network request capture started"
            logger.info(f"[start_network_capture] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to start network capture: {str(e)}"
            logger.error(f"[start_network_capture] {error_msg}")
            _raise_operation_error(error_msg)

    async def stop_network_capture(self) -> str:
        """Stop capturing network requests and clean up resources.

        Returns
        -------
        str
            Operation result message.
        """
        try:
            logger.info("[stop_network_capture] start")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            page_key = _get_page_key(page)

            if page_key not in self._network_handlers:
                _raise_state_error("No active network capture. Use network-start first.", code="NO_ACTIVE_CAPTURE")

            try:
                page.remove_listener("request", self._network_handlers[page_key])
            except Exception:
                pass
            del self._network_handlers[page_key]

            self._network_requests.pop(page_key, None)

            result = "Network capture stopped"
            logger.info(f"[stop_network_capture] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to stop network capture: {str(e)}"
            logger.error(f"[stop_network_capture] {error_msg}")
            _raise_operation_error(error_msg)

    async def get_network_requests(
        self,
        include_static: bool = False,
        clear: bool = True,
    ) -> str:
        """Get captured network requests.

        Parameters
        ----------
        include_static : bool, optional
            Whether to include static resources (images, stylesheets, scripts,
            fonts, media).  Default is False (only document, xhr, and fetch
            requests are returned).
        clear : bool, optional
            Whether to clear the captured buffer after retrieving. Default
            is True (consume-and-clear pattern).

        Returns
        -------
        str
            JSON array string.  Each element is an object with keys:

            - ``"url"`` : str — request URL.
            - ``"method"`` : str — HTTP method (e.g. "GET", "POST").
            - ``"resource_type"`` : str — Playwright resource type (e.g.
              "document", "xhr", "fetch", "image", "stylesheet").
            - ``"headers"`` : dict — request headers.
            - ``"post_data"`` : str | null — request body for POST requests.

        Raises
        ------
        StateError
            If no active page is available.
        OperationError
            If retrieval fails.

        Notes
        -----
        Network capture must be started first with :meth:`start_network_capture`.
        Call :meth:`start_network_capture` BEFORE navigation to capture all
        requests from a page load.  Returns an empty JSON array (``"[]"``) if
        no requests have been captured.
        """
        try:
            logger.info(f"[get_network_requests] start include_static={include_static} clear={clear}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            page_key = _get_page_key(page)
            requests = self._network_requests.get(page_key, [])

            if not include_static:
                static_types = {"image", "stylesheet", "script", "font", "media"}
                requests = [r for r in requests if r["resource_type"] not in static_types]

            if clear and page_key in self._network_requests:
                self._network_requests[page_key] = []

            result = json.dumps(requests, indent=2)
            logger.info(f"[get_network_requests] done count={len(requests)}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to get network requests: {str(e)}"
            logger.error(f"[get_network_requests] {error_msg}")
            _raise_operation_error(error_msg)

    async def wait_for_network_idle(self, timeout: float = 30.0) -> str:
        """Wait for network to become idle.

        Parameters
        ----------
        timeout : float, optional
            Maximum time to wait in seconds. Default is 30.0.

        Returns
        -------
        str
            Operation result message.
        """
        try:
            logger.info(f"[wait_for_network_idle] start timeout_seconds={timeout}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            timeout_ms = float(timeout) * 1000.0
            await page.wait_for_load_state("networkidle", timeout=timeout_ms)

            result = "Network is idle"
            logger.info(f"[wait_for_network_idle] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to wait for network idle: {str(e)}"
            logger.error(f"[wait_for_network_idle] {error_msg}")
            _raise_operation_error(error_msg)

    # ==================== Dialog Tools ====================

    async def setup_dialog_handler(
        self,
        default_action: str = "accept",
        default_prompt_text: Optional[str] = None,
    ) -> str:
        """Set up automatic dialog handling for all future dialogs.

        Parameters
        ----------
        default_action : str, optional
            Action to take on dialogs: "accept" or "dismiss". Default is "accept".
        default_prompt_text : str, optional
            Text to enter for prompt() dialogs. Default is empty string.

        Returns
        -------
        str
            Confirmation message with the configured action.

        Notes
        -----
        - Handler stays active until remove_dialog_handler is called
        - Only one handler per page; calling again replaces the previous
        """
        try:
            logger.info(f"[setup_dialog_handler] start action={default_action}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            page_key = _get_page_key(page)

            async def handle_dialog(dialog):
                dialog_type = dialog.type
                message = dialog.message
                logger.info(f"[dialog_handler] type={dialog_type} message={message}")

                if default_action == "accept":
                    if dialog_type == "prompt" and default_prompt_text is not None:
                        await dialog.accept(default_prompt_text)
                    else:
                        await dialog.accept()
                else:
                    await dialog.dismiss()

            if page_key in self._dialog_handlers:
                page.remove_listener("dialog", self._dialog_handlers[page_key])

            self._dialog_handlers[page_key] = handle_dialog
            page.on("dialog", handle_dialog)

            result = f"Dialog handler set up with default action: {default_action}"
            logger.info(f"[setup_dialog_handler] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to setup dialog handler: {str(e)}"
            logger.error(f"[setup_dialog_handler] {error_msg}")
            _raise_operation_error(error_msg)

    async def handle_dialog(
        self,
        accept: bool,
        prompt_text: Optional[str] = None,
    ) -> str:
        """Handle the next dialog that appears.

        Parameters
        ----------
        accept : bool
            Whether to accept (True) or dismiss (False) the dialog.
        prompt_text : Optional[str], optional
            Text to enter for prompt dialogs. Only used when accept is True.

        Returns
        -------
        str
            Operation result message.

        Notes
        -----
        This sets up a one-time handler for the very next dialog.
        Use ``setup_dialog_handler`` for persistent automatic handling.

        If ``setup_dialog_handler`` is already active when this method is
        called, the auto-handler is automatically removed (with a warning)
        so only this one-time handler fires.  Call ``setup_dialog_handler``
        again afterwards if persistent handling should resume.
        """
        try:
            logger.info(f"[handle_dialog] start accept={accept} prompt_text={prompt_text}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            # If an auto-handler (setup_dialog_handler) is already active for
            # this page, both listeners would fire on the same dialog — the
            # second accept()/dismiss() call will throw.  Remove the auto-handler
            # first so only the one-time handler runs.
            page_key = _get_page_key(page)
            if page_key in self._dialog_handlers:
                logger.warning(
                    "[handle_dialog] An auto dialog handler is already active — "
                    "removing it so the one-time handler takes precedence. "
                    "Call setup_dialog_handler() again if you need auto-handling to resume."
                )
                try:
                    page.remove_listener("dialog", self._dialog_handlers[page_key])
                except Exception:
                    pass
                del self._dialog_handlers[page_key]

            handled = {"done": False, "type": None, "message": None}

            async def one_time_handler(dialog):
                if handled["done"]:
                    return

                handled["done"] = True
                handled["type"] = dialog.type
                handled["message"] = dialog.message

                if accept:
                    if dialog.type == "prompt" and prompt_text is not None:
                        await dialog.accept(prompt_text)
                    else:
                        await dialog.accept()
                else:
                    await dialog.dismiss()

            page.once("dialog", one_time_handler)

            action = "accept" if accept else "dismiss"
            result = f"Dialog handler ready to {action} the next dialog"
            logger.info(f"[handle_dialog] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to set up dialog handler: {str(e)}"
            logger.error(f"[handle_dialog] {error_msg}")
            _raise_operation_error(error_msg)

    async def remove_dialog_handler(self) -> str:
        """Remove the automatic dialog handler.

        Returns
        -------
        str
            Operation result message.
        """
        try:
            logger.info("[remove_dialog_handler] start")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            page_key = _get_page_key(page)

            if page_key in self._dialog_handlers:
                page.remove_listener("dialog", self._dialog_handlers[page_key])
                del self._dialog_handlers[page_key]
                result = "Dialog handler removed"
            else:
                result = "No dialog handler was set up"

            logger.info(f"[remove_dialog_handler] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to remove dialog handler: {str(e)}"
            logger.error(f"[remove_dialog_handler] {error_msg}")
            _raise_operation_error(error_msg)

    # ==================== Storage Tools ====================

    async def save_storage_state(self, filename: Optional[str] = None) -> str:
        """Save the browser's storage state to a file.

        Parameters
        ----------
        filename : Optional[str], optional
            Path to save the storage state. If not provided, saves to a temporary file.

        Returns
        -------
        str
            On success: Returns the file path where state was saved.
        """
        try:
            logger.info(f"[save_storage_state] start filename={filename}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            context = page.context

            if filename:
                if not filename.lower().endswith(".json"):
                    filename = f"{filename}.json"
                output_path = filename

                dirname = os.path.dirname(filename)
                if dirname:
                    os.makedirs(dirname, exist_ok=True)
            else:
                fd, output_path = tempfile.mkstemp(suffix=".json", prefix="browser_state_")
                os.close(fd)

            await context.storage_state(path=output_path)

            result = f"Storage state saved to: {output_path}"
            logger.info(f"[save_storage_state] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to save storage state: {str(e)}"
            logger.error(f"[save_storage_state] {error_msg}")
            _raise_operation_error(error_msg)

    async def restore_storage_state(self, filename: str) -> str:
        """Restore browser storage state from a file.

        Parameters
        ----------
        filename : str
            Path to the storage state JSON file.

        Returns
        -------
        str
            On success: Returns a confirmation message.
        """
        try:
            logger.info(f"[restore_storage_state] start filename={filename}")

            if not os.path.exists(filename):
                _raise_operation_error(f"Storage state file not found: {filename}", code="NOT_FOUND")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            context = page.context

            with open(filename, "r") as f:
                state = json.load(f)

            cookies = state.get("cookies", [])
            if cookies:
                await context.add_cookies(cookies)

            origins = state.get("origins", [])
            for origin_data in origins:
                origin = origin_data.get("origin", "")
                local_storage = origin_data.get("localStorage", [])

                if local_storage and origin:
                    for item in local_storage:
                        name = item.get("name", "")
                        value = item.get("value", "")
                        if name:
                            await page.evaluate(
                                f"localStorage.setItem({json.dumps(name)}, {json.dumps(value)})"
                            )

            result = f"Storage state restored from: {filename} ({len(cookies)} cookies)"
            logger.info(f"[restore_storage_state] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to restore storage state: {str(e)}"
            logger.error(f"[restore_storage_state] {error_msg}")
            _raise_operation_error(error_msg)

    async def clear_cookies(
        self,
        name: Optional[str] = None,
        domain: Optional[str] = None,
        path: Optional[str] = None,
    ) -> str:
        """Clear cookies from the browser context.

        Parameters
        ----------
        name : Optional[str], optional
            Clear only cookies with this exact name. Default clears all.
        domain : Optional[str], optional
            Clear only cookies whose domain contains this string. Default clears all.
        path : Optional[str], optional
            Clear only cookies with this exact path. Default clears all.

        Returns
        -------
        str
            Operation result message.
        """
        try:
            logger.info(f"[clear_cookies] start name={name} domain={domain} path={path}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            context = page.context
            await context.clear_cookies(name=name, domain=domain, path=path)

            if name or domain or path:
                result = "Cookies cleared (filtered)"
            else:
                result = "All cookies cleared"
            logger.info(f"[clear_cookies] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to clear cookies: {str(e)}"
            logger.error(f"[clear_cookies] {error_msg}")
            _raise_operation_error(error_msg)

    async def get_cookies(
        self,
        urls: Optional[list] = None,
        *,
        name: Optional[str] = None,
        domain: Optional[str] = None,
        path: Optional[str] = None,
    ) -> str:
        """Get cookies from the browser context.

        Parameters
        ----------
        urls : Optional[list], optional
            List of URLs to get cookies for. If not provided, returns all cookies.
        name : Optional[str], optional
            Filter cookies by exact name.
        domain : Optional[str], optional
            Filter cookies by domain substring match.
        path : Optional[str], optional
            Filter cookies by path prefix match.

        Returns
        -------
        str
            JSON string containing the cookies.
        """
        try:
            logger.info(
                f"[get_cookies] start urls={urls} name={name} domain={domain} path={path}"
            )

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            context = page.context

            if urls:
                cookies = await context.cookies(urls)
            else:
                cookies = await context.cookies()

            if name:
                cookies = [cookie for cookie in cookies if cookie.get("name") == name]
            if domain:
                cookies = [
                    cookie
                    for cookie in cookies
                    if domain in (cookie.get("domain") or "")
                ]
            if path:
                cookies = [
                    cookie
                    for cookie in cookies
                    if (cookie.get("path") or "").startswith(path)
                ]

            result = json.dumps(cookies, indent=2)
            logger.info(f"[get_cookies] done count={len(cookies)}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to get cookies: {str(e)}"
            logger.error(f"[get_cookies] {error_msg}")
            _raise_operation_error(error_msg)

    async def set_cookie(
        self,
        name: str,
        value: str,
        url: Optional[str] = None,
        domain: Optional[str] = None,
        path: str = "/",
        expires: Optional[float] = None,
        http_only: bool = False,
        secure: bool = False,
        same_site: Optional[str] = None,
    ) -> str:
        """Set a cookie in the browser context.

        Parameters
        ----------
        name : str
            Cookie name.
        value : str
            Cookie value.
        url : Optional[str], optional
            URL to associate the cookie with. Either url or domain must be specified.
        domain : Optional[str], optional
            Cookie domain. Either url or domain must be specified.
        path : str, optional
            Cookie path. Default is "/".
        expires : Optional[float], optional
            Unix timestamp when the cookie expires.
        http_only : bool, optional
            Whether the cookie is HTTP only. Default is False.
        secure : bool, optional
            Whether the cookie requires HTTPS. Default is False.
        same_site : Optional[str], optional
            SameSite attribute. Options: "Strict", "Lax", "None".

        Returns
        -------
        str
            Operation result message.
        """
        try:
            logger.info(f"[set_cookie] start name={name}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            if not url and not domain:
                page_url = getattr(page, "url", "")
                parsed = urlparse(page_url)
                if parsed.scheme not in ("http", "https") or not parsed.hostname:
                    _raise_invalid_input(
                        "Either url or domain must be specified (current page URL has no host)",
                        code="INVALID_COOKIE_TARGET",
                        details={"page_url": page_url},
                    )
                domain = parsed.hostname
            if url and domain:
                _raise_invalid_input("Provide either url or domain, not both", code="INVALID_COOKIE_TARGET")

            context = page.context

            cookie: Dict[str, Any] = {
                "name": name,
                "value": value,
                "httpOnly": http_only,
                "secure": secure,
            }

            if url:
                cookie["url"] = url
            if domain:
                cookie["domain"] = domain
                cookie["path"] = path
            if expires is not None:
                cookie["expires"] = expires
            if same_site:
                cookie["sameSite"] = same_site

            await context.add_cookies([cookie])

            result = f"Cookie '{name}' set successfully"
            logger.info(f"[set_cookie] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to set cookie: {str(e)}"
            logger.error(f"[set_cookie] {error_msg}")
            _raise_operation_error(error_msg)

    # ==================== Verification Tools ====================

    async def verify_element_visible(
        self,
        role: str,
        accessible_name: str,
        timeout: float = 5.0,
    ) -> str:
        """Verify that an element with the given role and name is visible.

        Parameters
        ----------
        role : str
            ARIA role of the element (e.g., "button", "link", "textbox").
        accessible_name : str
            Accessible name of the element (usually its text content or aria-label).
        timeout : float, optional
            Maximum time to wait for the element in seconds. Default is 5.0.

        Returns
        -------
        str
            "PASS: ..." on success.

        Raises
        ------
        StateError
            If no active page is available.
        VerificationError
            If the target element is not visible.
        """
        try:
            logger.info(f"[verify_element_visible] start role={role} name={accessible_name}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            locator = page.get_by_role(role, name=accessible_name)

            try:
                await locator.wait_for(state="visible", timeout=timeout * 1000.0)
                result = f"PASS: Element with role '{role}' and name '{accessible_name}' is visible"
                logger.info(f"[verify_element_visible] {result}")
                return result
            except Exception:
                result = f"FAIL: Element with role '{role}' and name '{accessible_name}' is not visible"
                logger.warning(f"[verify_element_visible] {result}")
                _raise_verification_error(
                    result,
                    details={"role": role, "name": accessible_name, "timeout": timeout},
                )
        except BridgicBrowserError:
            raise
        except Exception as e:
            if isinstance(e, (StateError, VerificationError)):
                raise
            error_msg = f"Verification error: {str(e)}"
            logger.error(f"[verify_element_visible] {error_msg}")
            _raise_verification_error(error_msg)

    async def verify_text_visible(
        self,
        text: str,
        exact: bool = False,
        timeout: float = 5.0,
    ) -> str:
        """Verify that specific text is visible on the page.

        Parameters
        ----------
        text : str
            Text to search for on the page.
        exact : bool, optional
            Whether to match the text exactly. Default is False (substring match).
        timeout : float, optional
            Maximum time to wait for the text in seconds. Default is 5.0.

        Returns
        -------
        str
            "PASS: ..." on success.

        Raises
        ------
        StateError
            If no active page is available.
        VerificationError
            If the target text is not visible.
        """
        try:
            logger.info(f"[verify_text_visible] start text={text!r} exact={exact}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            try:
                await self._wait_for_text_across_frames(
                    page, text, exact=exact, timeout_ms=timeout * 1000.0,
                )
                result = f"PASS: Text '{text}' is visible on the page"
                logger.info(f"[verify_text_visible] {result}")
                return result
            except TimeoutError:
                result = f"FAIL: Text '{text}' is not visible on the page"
                logger.warning(f"[verify_text_visible] {result}")
                _raise_verification_error(
                    result,
                    details={"text": text, "exact": exact, "timeout": timeout},
                )
        except BridgicBrowserError:
            raise
        except Exception as e:
            if isinstance(e, (StateError, VerificationError)):
                raise
            error_msg = f"Verification error: {str(e)}"
            logger.error(f"[verify_text_visible] {error_msg}")
            _raise_verification_error(error_msg)

    async def verify_value(
        self,
        ref: str,
        value: str,
        attribute: str = "value",
    ) -> str:
        """Verify that an element has the expected value or attribute.

        Parameters
        ----------
        ref : str
            Element ref obtained from snapshot refs (e.g., "8d4b03a9").
        value : str
            Expected value.
        attribute : str, optional
            Attribute or property to check. Default is "value".

        Returns
        -------
        str
            "PASS: ..." on success.

        Raises
        ------
        StateError
            If the element ref cannot be resolved.
        VerificationError
            If the actual value/attribute does not match.
        """
        try:
            logger.info(f"[verify_value] start ref={ref} expected={value} attr={attribute}")

            locator = await self.get_element_by_ref(ref)
            if locator is None:
                _raise_state_error(
                    f"Element ref {ref} is not available",
                    code="REF_NOT_AVAILABLE",
                    details={"ref": ref},
                )

            if attribute == "value":
                actual = await locator.input_value()
            elif attribute == "textContent":
                actual = await locator.text_content()
            elif attribute == "innerText":
                actual = await locator.inner_text()
            else:
                actual = await locator.get_attribute(attribute)

            if actual is None:
                actual = ""

            if actual == value:
                result = f"PASS: Element {ref} has {attribute}='{value}'"
                logger.info(f"[verify_value] {result}")
            else:
                result = f"FAIL: Element {ref} {attribute} mismatch. Expected: '{value}', Actual: '{actual}'"
                logger.warning(f"[verify_value] {result}")
                _raise_verification_error(
                    result,
                    details={"ref": ref, "attribute": attribute, "expected": value, "actual": actual},
                )

            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            if isinstance(e, (StateError, VerificationError)):
                raise
            error_msg = f"Verification error: {str(e)}"
            logger.error(f"[verify_value] {error_msg}")
            _raise_verification_error(error_msg)

    async def verify_element_state(
        self,
        ref: str,
        state: str,
    ) -> str:
        """Verify that an element is in the expected state.

        Parameters
        ----------
        ref : str
            Element ref obtained from snapshot refs (e.g., "1f79fe5e").
        state : str
            Expected state. Options: "visible", "hidden", "enabled",
            "disabled", "checked", "unchecked", "editable".

        Returns
        -------
        str
            "PASS: ..." on success.

        Raises
        ------
        InvalidInputError
            If the requested state is unsupported.
        StateError
            If the element ref cannot be resolved.
        VerificationError
            If the element does not match the expected state.
        """
        try:
            logger.info(f"[verify_element_state] start ref={ref} state={state}")

            locator = await self.get_element_by_ref(ref)
            if locator is None:
                _raise_state_error(
                    f"Element ref {ref} is not available",
                    code="REF_NOT_AVAILABLE",
                    details={"ref": ref},
                )

            result = ""
            try:
                if state == "visible":
                    is_visible = await locator.is_visible()
                    result = f"PASS: Element {ref} is visible" if is_visible else f"FAIL: Element {ref} is not visible"

                elif state == "hidden":
                    is_hidden = await locator.is_hidden()
                    result = f"PASS: Element {ref} is hidden" if is_hidden else f"FAIL: Element {ref} is not hidden"

                elif state == "enabled":
                    is_enabled = await locator.is_enabled()
                    result = f"PASS: Element {ref} is enabled" if is_enabled else f"FAIL: Element {ref} is not enabled"

                elif state == "disabled":
                    is_disabled = await locator.is_disabled()
                    result = f"PASS: Element {ref} is disabled" if is_disabled else f"FAIL: Element {ref} is not disabled"

                elif state == "checked":
                    is_checked = await locator.is_checked()
                    result = f"PASS: Element {ref} is checked" if is_checked else f"FAIL: Element {ref} is not checked"

                elif state == "unchecked":
                    is_checked = await locator.is_checked()
                    result = f"PASS: Element {ref} is unchecked" if not is_checked else f"FAIL: Element {ref} is checked (expected unchecked)"

                elif state == "editable":
                    is_editable = await locator.is_editable()
                    result = f"PASS: Element {ref} is editable" if is_editable else f"FAIL: Element {ref} is not editable"

                else:
                    _raise_invalid_input(
                        f"Unknown state '{state}'",
                        code="INVALID_STATE_VALUE",
                        details={"state": state},
                    )

            except BridgicBrowserError:
                raise
            except Exception as e:
                if isinstance(e, InvalidInputError):
                    raise
                result = f"FAIL: Could not check state '{state}' for element {ref}: {str(e)}"
                _raise_verification_error(
                    result,
                    details={"ref": ref, "state": state},
                )

            logger.info(f"[verify_element_state] {result}")
            if result.startswith("FAIL:"):
                _raise_verification_error(
                    result,
                    details={"ref": ref, "state": state},
                )
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            if isinstance(e, (StateError, InvalidInputError, VerificationError)):
                raise
            error_msg = f"Verification error: {str(e)}"
            logger.error(f"[verify_element_state] {error_msg}")
            _raise_verification_error(error_msg)

    async def verify_url(self, expected_url: str, exact: bool = False) -> str:
        """Verify the current page URL.

        Parameters
        ----------
        expected_url : str
            Expected URL or URL substring.
        exact : bool, optional
            When True, the full URL must match exactly.
            When False (default), checks that ``expected_url`` is a substring
            of the actual URL (e.g., ``"/dashboard"`` matches
            ``"https://app.example.com/dashboard?tab=1"``).

        Returns
        -------
        str
            "PASS: URL matches. Current: <actual_url>" on success.

        Raises
        ------
        StateError
            If no active page is available.
        VerificationError
            If the URL does not match the expectation, with the message:
            "FAIL: URL mismatch. Expected: '<expected_url>', Actual: '<actual_url>'".
        """
        try:
            logger.info(f"[verify_url] start expected={expected_url} exact={exact}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            actual_url = page.url

            if exact:
                matches = actual_url == expected_url
            else:
                matches = expected_url in actual_url

            if matches:
                result = f"PASS: URL matches. Current: {actual_url}"
                logger.info(f"[verify_url] {result}")
            else:
                result = f"FAIL: URL mismatch. Expected: '{expected_url}', Actual: '{actual_url}'"
                logger.warning(f"[verify_url] {result}")
                _raise_verification_error(
                    result,
                    details={"expected_url": expected_url, "actual_url": actual_url, "exact": exact},
                )

            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            if isinstance(e, (StateError, VerificationError)):
                raise
            error_msg = f"Verification error: {str(e)}"
            logger.error(f"[verify_url] {error_msg}")
            _raise_verification_error(error_msg)

    async def verify_title(self, expected_title: str, exact: bool = False) -> str:
        """Verify the current page title.

        Parameters
        ----------
        expected_title : str
            Expected title or title pattern.
        exact : bool, optional
            Whether to match exactly. Default is False (contains check).

        Returns
        -------
        str
            "PASS: ..." on success.

        Raises
        ------
        StateError
            If no active page is available.
        VerificationError
            If the title does not match expectation.
        """
        try:
            logger.info(f"[verify_title] start expected={expected_title} exact={exact}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            actual_title = await page.title()

            if exact:
                matches = actual_title == expected_title
            else:
                matches = expected_title in actual_title

            if matches:
                result = f"PASS: Title matches. Current: '{actual_title}'"
                logger.info(f"[verify_title] {result}")
            else:
                result = f"FAIL: Title mismatch. Expected: '{expected_title}', Actual: '{actual_title}'"
                logger.warning(f"[verify_title] {result}")
                _raise_verification_error(
                    result,
                    details={"expected_title": expected_title, "actual_title": actual_title, "exact": exact},
                )

            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            if isinstance(e, (StateError, VerificationError)):
                raise
            error_msg = f"Verification error: {str(e)}"
            logger.error(f"[verify_title] {error_msg}")
            _raise_verification_error(error_msg)

    # ==================== DevTools (Tracing and Video) ====================

    async def start_tracing(
        self,
        screenshots: bool = True,
        snapshots: bool = True,
        sources: bool = False,
    ) -> str:
        """Start browser tracing.

        Parameters
        ----------
        screenshots : bool, optional
            Whether to capture screenshots during trace. Default is True.
        snapshots : bool, optional
            Whether to capture DOM snapshots. Default is True.
        sources : bool, optional
            Whether to include source files. Default is False.

        Returns
        -------
        str
            Operation result message.

        Notes
        -----
        Only one trace can be active at a time per browser context.
        """
        try:
            logger.info(f"[start_tracing] start screenshots={screenshots} snapshots={snapshots}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            context = page.context
            context_key = _get_context_key(context)

            if context_key in self._tracing_state and self._tracing_state[context_key]:
                _raise_state_error("Tracing is already active. Stop the current trace first.", code="TRACING_ALREADY_ACTIVE")

            await context.tracing.start(
                screenshots=screenshots,
                snapshots=snapshots,
                sources=sources,
            )

            self._tracing_state[context_key] = True

            result = "Tracing started"
            logger.info(f"[start_tracing] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to start tracing: {str(e)}"
            logger.error(f"[start_tracing] {error_msg}")
            _raise_operation_error(error_msg)

    async def stop_tracing(self, filename: Optional[str] = None) -> str:
        """Stop browser tracing and save the trace file.

        Parameters
        ----------
        filename : Optional[str], optional
            Path to save the trace file. If not provided, saves to a temporary file.

        Returns
        -------
        str
            On success: Returns the file path where trace was saved.
        """
        try:
            logger.info(f"[stop_tracing] start filename={filename}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            context = page.context
            context_key = _get_context_key(context)

            if context_key not in self._tracing_state or not self._tracing_state[context_key]:
                _raise_state_error("No active tracing to stop. Start tracing first.", code="NO_ACTIVE_TRACING")

            if filename:
                if not filename.lower().endswith(".zip"):
                    filename = f"{filename}.zip"
                output_path = filename

                dirname = os.path.dirname(filename)
                if dirname:
                    os.makedirs(dirname, exist_ok=True)
            else:
                fd, output_path = tempfile.mkstemp(suffix=".zip", prefix="browser_trace_")
                os.close(fd)

            await context.tracing.stop(path=output_path)
            self._tracing_state[context_key] = False

            result = f"Trace saved to: {output_path}"
            logger.info(f"[stop_tracing] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to stop tracing: {str(e)}"
            logger.error(f"[stop_tracing] {error_msg}")
            _raise_operation_error(error_msg)

    async def start_video(
        self,
        width: Optional[int] = None,
        height: Optional[int] = None,
    ) -> str:
        """Mark the current page's video recording session as active.

        Video recording is always running — Playwright starts recording as soon
        as a page is created (using the ``record_video_dir`` set at browser
        creation, which defaults to ``~/.bridgic/bridgic-browser/tmp``).  This method simply
        marks the session as "started" so that :meth:`stop_video` can later
        register where to save the file.

        Use ``stop_video(filename)`` to designate a save path; the actual file
        is written when the browser closes.

        Parameters
        ----------
        width : Optional[int], optional
            Accepted for API compatibility but **not used** — video resolution
            is determined by ``record_video_size`` passed at ``Browser()``
            creation time, not here.
        height : Optional[int], optional
            Accepted for API compatibility but **not used** — see ``width``.

        Returns
        -------
        str
            "Video recording started".

        Raises
        ------
        StateError
            If no active page is available, or if no video is attached to the
            current page (should not occur under normal operation).
        OperationError
            If an unexpected error occurs.
        """
        try:
            logger.info(f"[start_video] start width={width} height={height}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            context = page.context
            context_key = _get_context_key(context)

            if page.video:
                self._video_state[context_key] = True
                result = "Video recording started"
                logger.info(f"[start_video] done {result}")
                return result
            else:
                _raise_state_error("No video recording available for this page", code="NO_ACTIVE_RECORDING")
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to start video: {str(e)}"
            logger.error(f"[start_video] {error_msg}")
            _raise_operation_error(error_msg)

    async def stop_video(self, filename: Optional[str] = None) -> str:
        """Stop video recording.

        Marks the current recording session as stopped and registers the
        destination path.  The actual video files are written by Playwright
        when pages close, so saving is deferred to ``browser_close()`` /
        ``close_tab()`` — no pages are touched here.

        Parameters
        ----------
        filename : Optional[str], optional
            Destination path for the video file(s).  Accepts a file path
            (``./videos/demo.webm``) or a directory (``./videos/``).
            The ``.webm`` extension is added automatically when missing.
            If not provided, Playwright writes files to the temporary
            recording directory automatically on page close.

        Returns
        -------
        str
            Confirmation that recording was stopped and where files will be
            saved (``Video will be saved to: <path> on browser close``).
        """
        try:
            logger.info(f"[stop_video] start filename={filename}")

            if self._context is None:
                _raise_state_error("No context is open", code="NO_CONTEXT")
            context_key = _get_context_key(self._context)

            if not self._video_state.get(context_key):
                _raise_state_error("No active video recording. Use video-start first.", code="NO_ACTIVE_RECORDING")

            # Resolve destination path now (before any context changes) and
            # create the directory so the user gets an early error if the path
            # is invalid.  Actual file writing is deferred to browser close.
            resolved: Optional[str] = None
            if filename:
                if filename.endswith(os.sep) or filename.endswith("/") or os.path.isdir(filename):
                    import time as _time
                    dest_dir = os.path.abspath(filename)
                    resolved = os.path.join(dest_dir, f"video_{_time.strftime('%Y%m%d_%H%M%S')}.webm")
                else:
                    if not filename.lower().endswith(".webm"):
                        filename = f"{filename}.webm"
                    resolved = os.path.abspath(filename)
                dest_dir = os.path.dirname(resolved)
                if dest_dir:
                    os.makedirs(dest_dir, exist_ok=True)

            # Defer the actual save; no pages are closed or navigated here.
            self._pending_video_save_path[context_key] = resolved
            self._video_state[context_key] = False

            if resolved:
                dest_dir_display = os.path.dirname(resolved)
                stem_display = os.path.splitext(os.path.basename(resolved))[0]
                result = (
                    f"Video recording stopped. "
                    f"Files will be saved to {dest_dir_display}/ "
                    f"as {stem_display}.webm (single tab) or "
                    f"{stem_display}_1.webm, {stem_display}_2.webm, ... (multiple tabs) "
                    f"when browser closes."
                )
            else:
                result = (
                    "Video recording stopped. "
                    "Files will be auto-saved to the recording directory when browser closes."
                )
            logger.info(f"[stop_video] done (deferred) {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to stop video: {str(e)}"
            logger.error(f"[stop_video] {error_msg}")
            _raise_operation_error(error_msg)

    async def add_trace_chunk(self, title: Optional[str] = None) -> str:
        """Add a new chunk to the trace.

        Parameters
        ----------
        title : Optional[str], optional
            Title for the new trace chunk.

        Returns
        -------
        str
            Operation result message.
        """
        try:
            logger.info(f"[add_trace_chunk] start title={title}")

            page = await self.get_current_page()
            if page is None:
                _raise_state_error("No active page available", code="NO_ACTIVE_PAGE")

            context = page.context
            context_key = _get_context_key(context)

            if context_key not in self._tracing_state or not self._tracing_state[context_key]:
                _raise_state_error("No active tracing. Start tracing first.", code="NO_ACTIVE_TRACING")

            await context.tracing.start_chunk(title=title)

            result = f"New trace chunk started" + (f": {title}" if title else "")
            logger.info(f"[add_trace_chunk] done {result}")
            return result
        except BridgicBrowserError:
            raise
        except Exception as e:
            error_msg = f"Failed to add trace chunk: {str(e)}"
            logger.error(f"[add_trace_chunk] {error_msg}")
            _raise_operation_error(error_msg)
