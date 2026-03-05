import asyncio
import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional, Sequence, Union

if TYPE_CHECKING:
    try:
        from bridgic.llms.openai import OpenAILlm  # pyright: ignore[reportMissingImports]
    except ModuleNotFoundError:  # pragma: no cover - optional dependency
        OpenAILlm = Any  # type: ignore[misc,assignment]

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
from ..utils import find_page_by_id, generate_page_id

logger = logging.getLogger(__name__)


# Type aliases for Playwright types
ViewportSize = Dict[str, int]  # {"width": int, "height": int}
Geolocation = Dict[str, float]  # {"latitude": float, "longitude": float, "accuracy"?: float}
HttpCredentials = Dict[str, Any]  # {"username": str, "password": str, ...}
ClientCertificate = Dict[str, Any]


class Browser:
    """Browser wrapper for Playwright with automatic launch mode selection.

    This class automatically chooses between `launch` + `new_context` and
    `launch_persistent_context` based on whether `user_data_dir` is provided.

    - With `user_data_dir`: Uses `launch_persistent_context` for session persistence
    - Without `user_data_dir`: Uses `launch` + `new_context` for isolated sessions

    Parameters
    ----------
    headless : bool, default True
        Whether to run browser in headless mode.
    viewport : ViewportSize, optional
        Viewport size. Defaults to {"width": 1920, "height": 1080}.
    user_data_dir : str | Path, optional
        Path to user data directory for persistent context. If provided,
        uses `launch_persistent_context`; otherwise uses `launch` + `new_context`.
    stealth : bool | StealthConfig, default True
        Stealth mode for bypassing bot detection. **Enabled by default.**
        - True (default): Enable stealth with optimal StealthConfig
        - False: Disable stealth mode completely
        - StealthConfig: Custom stealth configuration

        Stealth mode includes:
        - 50+ Chrome args to disable automation detection
        - Ignoring Playwright's automation-revealing default args
        - Extensions (uBlock Origin, Cookie Consent, etc.) when headless=False

        Note: Extensions require headless=False. If stealth with extensions is
        enabled without user_data_dir, a temporary directory will be created.
    channel : str, optional
        Browser distribution channel. Use "chrome", "chrome-beta", "msedge", etc.
        for branded browsers, or "chromium" for new headless mode.
    executable_path : str | Path, optional
        Path to a browser executable to run instead of the bundled one.
    proxy : ProxySettings, optional
        Network proxy settings: {"server": str, "bypass"?: str, "username"?: str, "password"?: str}.
    timeout : float, optional
        Maximum time in milliseconds to wait for browser to start. Default 30000.
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

    # Non-headless with full stealth (includes extensions)
    >>> browser = Browser(headless=False)  # Auto temp user_data_dir for extensions

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
    ...         enable_extensions=False,  # No extensions
    ...         disable_security=True,    # For testing only
    ...     ),
    ... )
    """

    def __init__(
        self,
        # === Common frequently used parameters ===
        headless: bool = True,
        viewport: Optional[ViewportSize] = None,
        user_data_dir: Optional[Union[str, Path]] = None,
        # === Stealth mode (enabled by default for best anti-detection) ===
        stealth: Union[bool, StealthConfig, None] = True,
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
        # Store all parameters
        self._headless = headless
        self._viewport = viewport or {"width": 1920, "height": 1080}
        self._user_data_dir = Path(user_data_dir).expanduser() if user_data_dir else None

        # Stealth configuration
        self._stealth_config: Optional[StealthConfig] = None
        self._stealth_builder: Optional[StealthArgsBuilder] = None
        self._temp_user_data_dir: Optional[str] = None  # For auto-created temp dir

        if stealth is True:
            self._stealth_config = StealthConfig()
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

    @property
    def use_persistent_context(self) -> bool:
        """Whether to use persistent context mode.

        Returns True if:
        - user_data_dir is provided, OR
        - stealth mode with extensions is enabled (extensions need persistent context)
        """
        # Explicit user_data_dir
        if self._user_data_dir is not None:
            return True

        # Stealth with extensions needs persistent context (and headless=False)
        if self._stealth_config and self._stealth_config.can_use_extensions(self._headless):
            return True

        return False

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
        """Whether browser runs in headless mode."""
        return self._headless

    @property
    def viewport(self) -> ViewportSize:
        """Current viewport size configuration."""
        return self._viewport

    @property
    def user_data_dir(self) -> Optional[Path]:
        """User data directory path, or None if not using persistent context."""
        return self._user_data_dir

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
            "user_data_dir": str(self._user_data_dir) if self._user_data_dir else None,
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

        # Add stealth args first (if enabled)
        if self._stealth_builder:
            viewport_width = self._viewport.get("width", 1920)
            viewport_height = self._viewport.get("height", 1080)
            stealth_args = self._stealth_builder.build_args(viewport_width, viewport_height)
            args_list.extend(stealth_args)

            # Add extension args if applicable
            extension_args = self._stealth_builder.build_extension_args(self._headless)
            args_list.extend(extension_args)

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
            options["timeout"] = self._timeout
        if self._headless is not None:
            options["headless"] = self._headless
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

            # Add screen size to match viewport (stealth recommendation)
            if self._viewport:
                options["screen"] = self._viewport.copy()

        # Add non-None context parameters (user values override stealth defaults)
        if self._viewport is not None:
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

        # Determine user_data_dir
        if self._user_data_dir:
            options["user_data_dir"] = str(self._user_data_dir)
        elif self._stealth_config and self._stealth_config.can_use_extensions(self._headless):
            # Stealth with extensions needs persistent context, create temp dir
            if not self._temp_user_data_dir:
                self._temp_user_data_dir = tempfile.mkdtemp(prefix="bridgic-browser-")
                logger.info(f"Created temporary user data dir for stealth extensions: {self._temp_user_data_dir}")
            options["user_data_dir"] = self._temp_user_data_dir
        else:
            options["user_data_dir"] = ""

        return options
    #########################################################
    # browser level
    #########################################################
    async def start(self) -> None:
        """Start the browser.

        Automatically chooses between two launch modes:
        - If `user_data_dir` is provided: Uses `launch_persistent_context`
        - If `user_data_dir` is not provided: Uses `launch` + `new_context`

        When stealth mode is enabled, anti-detection args are automatically
        applied. If stealth with extensions is enabled without user_data_dir,
        a temporary directory is created automatically.
        """
        if self._playwright is not None:
            logger.warning("Playwright has already been started")
            return

        logger.info("Starting playwright")
        if self.stealth_enabled:
            extensions_enabled = (
                self._stealth_config
                and self._stealth_config.can_use_extensions(self._headless)
            )
            logger.info(f"Stealth mode enabled (extensions={extensions_enabled})")

        self._playwright = await async_playwright().start()

        if self.use_persistent_context:
            # Mode 1: Persistent context (with user_data_dir or stealth extensions)
            logger.info("Using persistent context mode")
            persistent_options = self._get_persistent_context_options()
            logger.debug(f"Persistent context options: {persistent_options}")
            self._context = await self._playwright.chromium.launch_persistent_context(
                **persistent_options
            )
            self._browser = self._context.browser
        else:
            # Mode 2: Normal launch + new_context (without user_data_dir)
            logger.info("Using normal launch mode")
            launch_options = self._get_launch_options()
            logger.debug(f"Launch options: {launch_options}")
            self._browser = await self._playwright.chromium.launch(**launch_options)

            context_options = self._get_context_options()
            logger.debug(f"Context options: {context_options}")
            self._context = await self._browser.new_context(**context_options)

        # Auto create a new page if no page is open
        pages = self._context.pages
        if len(pages) > 0:
            self._page = pages[0]
        else:
            self._page = await self._context.new_page()

        # Attach download manager to handle downloads with correct filenames
        if self._download_manager:
            self._download_manager.attach_to_context(self._context)
            logger.info(f"Download manager attached, saving to: {self._download_manager.downloads_path}")

        logger.info(
            f"Playwright started (persistent_context={self.use_persistent_context}, "
            f"stealth={self.stealth_enabled})"
        )

    async def kill(self) -> None:
        """Stop the browser and clean up all resources.

        Handles both launch modes:
        - Persistent context: Closing context automatically closes browser
        - Normal launch: Must close browser separately

        Also cleans up temporary user data directory if one was created
        for stealth extensions.
        """
        errors = []
        
        # Close page
        if self._page:
            try:
                await self._page.close()
            except Exception as e:
                errors.append(f"page.close: {e}")
            self._page = None

        # Detach download manager before context closes to remove handlers
        if self._download_manager and self._context:
            try:
                self._download_manager.detach_from_context(self._context)
            except Exception as e:
                errors.append(f"download_manager.detach: {e}")

        # Close context
        # NOTE: In persistent context mode, closing context will auto close browser
        if self._context:
            try:
                await self._context.close()
            except Exception as e:
                errors.append(f"context.close: {e}")
            self._context = None

        # Close browser (only needed in normal launch mode, not persistent context)
        # In persistent context mode, browser is None or already closed
        if self._browser:
            try:
                await self._browser.close()
            except Exception as e:
                errors.append(f"browser.close: {e}")
            self._browser = None

        # Stop playwright
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception as e:
                errors.append(f"playwright.stop: {e}")
            self._playwright = None

        # Clean up temporary user data directory (created for stealth extensions)
        if self._temp_user_data_dir:
            try:
                import shutil
                shutil.rmtree(self._temp_user_data_dir, ignore_errors=True)
                logger.debug(f"Cleaned up temporary user data dir: {self._temp_user_data_dir}")
            except Exception as e:
                errors.append(f"temp_dir cleanup: {e}")
            self._temp_user_data_dir = None

        # Clear snapshot cache
        self._last_snapshot = None
        self._last_snapshot_url = None

        if errors:
            logger.warning(f"Browser killed with errors: {errors}")
        else:
            logger.info("Browser killed")

    async def __aenter__(self) -> "Browser":
        """Async context manager entry - starts the browser.
        
        Usage:
            async with Browser(headless=True) as browser:
                await browser.navigate_to("https://example.com")
                # Browser is automatically closed when exiting the context
        """
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit - kills the browser."""
        await self.kill()

    async def close(self) -> None:
        """Close the browser and clean up all resources. Alias for kill()."""
        await self.kill()
    #########################################################
    # page level
    #########################################################
    async def navigate_to(
        self,
        url: str,
        wait_until: Literal["domcontentloaded", "load", "networkidle", "commit"] = "domcontentloaded",
        timeout: Optional[float] = None,
    ) -> None:
        """Navigate current page to the specified URL.

        Parameters
        ----------
        url : str
            The URL to navigate to.
        wait_until : str, default "domcontentloaded"
            When to consider navigation complete.
            - "domcontentloaded": DOM is parsed (fast, recommended for SPAs).
            - "load": Full page load including images/styles.
            - "networkidle": No network activity for 500ms (may timeout on SPAs).
            - "commit": Response received from server.
        timeout : float, optional
            Maximum time in milliseconds. Defaults to Playwright's 30000ms.
        """
        if not self._page:
            logger.warning("No page is open, creating a new page")
            if self._context:
                self._page = await self._context.new_page()
            else:
                await self.start()

        logger.info(f"Navigating to {url}")
        kwargs: Dict[str, Any] = {"wait_until": wait_until}
        if timeout is not None:
            kwargs["timeout"] = timeout
        await self._page.goto(url, **kwargs)
        # Update cache
        self._last_snapshot = None
        self._last_snapshot_url = None

    async def new_page(
        self,
        url: Optional[str] = None,
        wait_until: Literal["domcontentloaded", "load", "networkidle", "commit"] = "domcontentloaded",
        timeout: Optional[float] = None,
    ) -> Optional[Page]:
        if not self._context:
            logger.warning("No context is open, starting playwright")
            await self.start()
        self._page = await self._context.new_page()
        if url:
            await self.navigate_to(url, wait_until=wait_until, timeout=timeout)
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
        return True, f"Switched to page: {page_id}"

    async def close_page(self, page: Page | str) -> tuple[bool, str]:
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
        return True, f"Closed page: {page_id}"

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

    async def get_current_page_info(self) -> Optional[PageInfo]:
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
            page_info = await self.get_current_page_info()
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
    async def take_screenshot(
        self,
        path: Optional[str | Path] = None,
        full_page: bool = False,
        **kwargs,
    ) -> Optional[bytes]:
        """Take a screenshot of the current page.

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
    
    #########################################################
    # snapshot
    #########################################################
    async def get_snapshot(
        self,
        interactive: bool = False,
        full_page: bool = True,
    ) -> Optional[EnhancedSnapshot]:
        """Get accessibility snapshot of the current page.

        Parameters
        ----------
        interactive : bool
            If True, only include interactive elements with flattened output.
        full_page : bool
            If True (default), include all elements regardless of viewport position.
            If False, only include elements within the viewport.

        Returns
        -------
        Optional[EnhancedSnapshot]
            Snapshot with tree string and refs dictionary, or None if failed.
        """
        if not self._page:
            logger.warning("No page is open, can't get snapshot")
            return None
        try:
            options = SnapshotOptions(
                interactive=interactive,
                full_page=full_page,
            )
            if self._snapshot_generator is None:
                self._snapshot_generator = SnapshotGenerator()
            current_url = self.get_current_page_url()
            self._last_snapshot = await self._snapshot_generator.get_enhanced_snapshot_async(self._page, options)
            self._last_snapshot_url = current_url
            return self._last_snapshot
        except Exception as e:
            logger.warning("Failed to get snapshot: %s", e, exc_info=True)
            return None
    
    async def get_element_by_ref(self, ref: str, _fallback_depth: int = 0) -> Optional[Locator]:
        if not self._page:
            logger.warning("No page is open, can't get element by ref")
            return None
        if self._last_snapshot is None:
            logger.warning("No snapshot is available, can't get element by ref, please get snapshot first")
            return None
        try:
            if self._snapshot_generator is None:
                self._snapshot_generator = SnapshotGenerator()
            
            locator = self._snapshot_generator.get_locator_from_ref_async(
                self._page, ref, self._last_snapshot.refs
            )
            if locator:
                # Validate locator and expose ambiguity explicitly for debugging.
                count = await locator.count()
                if count == 1:
                    return locator
                elif count > 1:
                    ref_data = self._last_snapshot.refs.get(ref)
                    can_recover_by_role_name = (
                        bool(ref_data and ref_data.name)
                        and ref_data.role not in SnapshotGenerator.ROLE_TEXT_MATCH_ROLES
                        and ref_data.role not in SnapshotGenerator.STRUCTURAL_NOISE_ROLES
                        and ref_data.role not in SnapshotGenerator.TEXT_LEAF_ROLES
                    )
                    if can_recover_by_role_name and ref_data:
                        frame_path = getattr(ref_data, 'frame_path', None)
                        scope = self._page
                        if frame_path:
                            for local_nth in frame_path:
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

                    if (
                        ref_data
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
- button "Submit" [ref=e2]
- textbox "Email" [ref=e3]
- link "Learn more" [ref=e5]

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
