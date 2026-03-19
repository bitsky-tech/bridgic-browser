"""
Stealth mode configuration for anti-bot detection.

Based on browser-use (https://github.com/browser-use/browser-use)

This module provides stealth configurations to make automated browsers
appear more like regular user browsers, helping bypass bot detection.
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


# ========== JS Init Script — navigator/window property patches ==========
# Injected into every page (main world) before any page script runs.
# Targets the most common bot-detection signals as identified by Cloudflare,
# DataDome, PerimeterX and the rebrowser-bot-detector test suite (2024-2025).
_STEALTH_INIT_SCRIPT_TEMPLATE: str = """
(function () {
  // ── navigator.webdriver ────────────────────────────────────────────────────
  // Belt-and-suspenders: --disable-blink-features=AutomationControlled already
  // removes this at the Chrome level; the JS override covers sub-frames.
  Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

  // ── navigator.plugins / mimeTypes ─────────────────────────────────────────
  // Headless Chrome exposes 0 plugins. Real Chrome has PDF Viewer entries.
  const _makeMime = (type, suffixes, description) =>
    ({ type, suffixes, description, enabledPlugin: null });

  const _pdfMimes = [
    _makeMime('application/pdf', 'pdf', 'Portable Document Format'),
    _makeMime('text/pdf',        'pdf', 'Portable Document Format'),
  ];

  const _makePlugin = (name, description) => {
    const p = { name, description, filename: 'internal-pdf-viewer', length: _pdfMimes.length };
    // Create per-plugin mime copies so each plugin's enabledPlugin ref is correct
    // (mutating shared _pdfMimes objects would cause all mimes to point to the last plugin)
    _pdfMimes.forEach((m, i) => {
      const localMime = { type: m.type, suffixes: m.suffixes, description: m.description, enabledPlugin: p };
      Object.defineProperty(p, i, { value: localMime, enumerable: true });
    });
    p.item      = (i) => p[i] ?? null;
    p.namedItem = (n) => { const idx = _pdfMimes.findIndex(m => m.type === n); return idx >= 0 ? p[idx] : null; };
    return p;
  };

  const _plugins = [
    _makePlugin('PDF Viewer',              'Portable Document Format'),
    _makePlugin('Chrome PDF Viewer',       'Portable Document Format'),
    _makePlugin('Chromium PDF Viewer',     'Portable Document Format'),
    _makePlugin('Microsoft Edge PDF Viewer', 'Portable Document Format'),
    _makePlugin('WebKit built-in PDF',     'Portable Document Format'),
  ];

  // Link global mimeTypes entries to the primary plugin (PDF Viewer), matching Chrome behaviour
  _pdfMimes.forEach((m) => { m.enabledPlugin = _plugins[0]; });

  const _pluginList = Object.assign([..._plugins], {
    item:      (i) => _plugins[i] ?? null,
    namedItem: (n) => _plugins.find(p => p.name === n) ?? null,
    refresh:   () => {},
    length:    _plugins.length,
  });

  Object.defineProperty(navigator, 'plugins', { get: () => _pluginList });

  const _mimeList = Object.assign([..._pdfMimes], {
    item:      (i) => _pdfMimes[i] ?? null,
    namedItem: (n) => _pdfMimes.find(m => m.type === n) ?? null,
    length:    _pdfMimes.length,
  });

  Object.defineProperty(navigator, 'mimeTypes', { get: () => _mimeList });

  // ── navigator.languages ───────────────────────────────────────────────────
  // __BRIDGIC_LANGS__ is replaced by get_init_script() based on the Browser locale setting.
  // Keeping navigator.languages[0] consistent with navigator.language avoids a detectable mismatch.
  try {
    Object.defineProperty(navigator, 'languages', { get: () => __BRIDGIC_LANGS__ });
  } catch (_) {}

  // ── window.chrome ─────────────────────────────────────────────────────────
  // Headless Chrome may have a missing or incomplete chrome object.
  // Check all expected fields: a partial chrome (e.g. runtime present but csi/loadTimes absent)
  // is equally detectable and must also be patched.
  if (!window.chrome || !window.chrome.runtime || !window.chrome.csi || !window.chrome.loadTimes) {
    const _chrome = {
      app: {
        isInstalled: false,
        getDetails:     () => null,
        getIsInstalled: () => false,
        installState:   () => 'not_installed',
      },
      runtime: {
        connect:     () => {},
        sendMessage: () => {},
      },
      csi: () => ({
        onloadT: Date.now(),
        pageT:   Date.now() - (performance.timeOrigin ?? performance.timing?.navigationStart ?? 0),
        startE:  Date.now() - 1000,
        tran:    15,
      }),
      loadTimes: () => ({
        commitLoadTime:          Date.now() / 1000 - 1,
        connectionInfo:          'h2',
        finishDocumentLoadTime:  Date.now() / 1000,
        finishLoadTime:          Date.now() / 1000,
        firstPaintAfterLoadTime: 0,
        firstPaintTime:          Date.now() / 1000 - 0.5,
        navigationType:          'Other',
        npnNegotiatedProtocol:   'h2',
        requestTime:             Date.now() / 1000 - 1,
        startLoadTime:           Date.now() / 1000 - 1,
        wasAlternateProtocolAvailable: false,
        wasFetchedViaSpdy:       true,
        wasNpnNegotiated:        true,
      }),
    };
    try {
      Object.defineProperty(window, 'chrome', {
        value: _chrome, writable: false, enumerable: true, configurable: false,
      });
    } catch (_) { window.chrome = _chrome; }
  }

  // ── navigator.permissions ─────────────────────────────────────────────────
  // Headless returns 'denied' for notification queries without ever prompting;
  // real Chrome returns 'default' (not yet asked).
  if (navigator.permissions && navigator.permissions.query) {
    const _origQuery = navigator.permissions.query.bind(navigator.permissions);
    navigator.permissions.query = (params) => {
      if (params && params.name === 'notifications') {
        return Promise.resolve({ state: Notification.permission === 'denied' ? 'default' : Notification.permission, onchange: null });
      }
      return _origQuery(params);
    };
  }

  // ── window.outerWidth / outerHeight ───────────────────────────────────────
  // Headless sets these to 0; real Chrome includes the browser chrome (~85px).
  if (window.outerWidth === 0) {
    try { Object.defineProperty(window, 'outerWidth', { get: () => window.innerWidth }); } catch (_) {}
  }
  if (window.outerHeight === 0) {
    try { Object.defineProperty(window, 'outerHeight', { get: () => window.innerHeight + 85 }); } catch (_) {}
  }
})();
"""


# ========== Chrome Disabled Components (browser-use/profile.py:29-78) ==========
CHROME_DISABLED_COMPONENTS: List[str] = [
    "AcceptCHFrame",
    "AutoExpandDetailsElement",
    "AvoidUnnecessaryBeforeUnloadCheckSync",
    "CertificateTransparencyComponentUpdater",
    "DestroyProfileOnBrowserClose",
    "DialMediaRouteProvider",
    "ExtensionManifestV2Disabled",
    "GlobalMediaControls",
    "HttpsUpgrades",
    "ImprovedCookieControls",
    "LazyFrameLoading",
    "LensOverlay",
    "MediaRouter",
    "PaintHolding",
    "ThirdPartyStoragePartitioning",
    "Translate",
    "AutomationControlled",
    "BackForwardCache",
    "OptimizationHints",
    "ProcessPerSiteUpToMainFrameThreshold",
    "InterestFeedContentSuggestions",
    "CalculateNativeWinOcclusion",
    "HeavyAdPrivacyMitigations",
    "PrivacySandboxSettings4",
    "AutofillServerCommunication",
    "CrashReporting",
    "OverscrollHistoryNavigation",
    "InfiniteSessionRestore",
    "ExtensionDisableUnsupportedDeveloper",
    "ExtensionManifestV2Unsupported",
]

# ========== Chrome Default Args (browser-use/profile.py:118-186) ==========
CHROME_STEALTH_ARGS: List[str] = [
    "--disable-field-trial-config",
    "--disable-background-networking",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-back-forward-cache",
    "--disable-breakpad",
    "--disable-client-side-phishing-detection",
    "--disable-component-extensions-with-background-pages",
    "--disable-component-update",
    "--no-default-browser-check",
    "--disable-hang-monitor",
    "--disable-ipc-flooding-protection",
    "--disable-popup-blocking",
    "--disable-prompt-on-repost",
    "--disable-renderer-backgrounding",
    "--metrics-recording-only",
    "--no-first-run",
    "--no-service-autorun",
    "--export-tagged-pdf",
    "--disable-search-engine-choice-screen",
    "--unsafely-disable-devtools-self-xss-warnings",
    "--enable-features=NetworkService,NetworkServiceInProcess",
    "--enable-network-information-downlink-max",
    "--disable-sync",
    "--allow-legacy-extension-manifests",
    "--allow-pre-commit-input",
    "--disable-blink-features=AutomationControlled",
    "--log-level=2",
    "--lang=en-US",
    "--disable-focus-on-load",
    "--disable-window-activation",
    "--generate-pdf-document-outline",
    "--no-pings",
    "--disable-infobars",
    "--simulate-outdated-no-au=Tue, 31 Dec 2099 23:59:59 GMT",
    "--hide-crash-restore-bubble",
    "--disable-domain-reliability",
    "--disable-datasaver-prompt",
    "--disable-speech-synthesis-api",
    "--disable-speech-api",
    "--disable-print-preview",
    "--safebrowsing-disable-auto-update",
    "--disable-external-intent-requests",
    "--disable-desktop-notifications",
    "--noerrdialogs",
    "--silent-debugger-extension-api",
    "--disable-extensions-http-throttling",
    "--extensions-on-chrome-urls",
    "--disable-default-apps",
]

# ========== Linux-only Chrome Args ==========
# These flags are only valid on Linux. Applying them on macOS/Windows causes
# "不支持的命令行标记" warnings in the Chrome title bar.
CHROME_LINUX_ONLY_ARGS: List[str] = [
    "--disable-dev-shm-usage",       # /dev/shm is Linux-specific shared memory
    "--ash-no-nudges",               # ChromeOS Ash shell only
    "--suppress-message-center-popups",  # ChromeOS message center only
]

# ========== Universal Chrome Args (agent-browser compatible) ==========
# Minimal set of flags that every Chrome/Chromium build recognises on every
# platform (macOS, Linux, Windows). Used when launching the system-installed
# Chrome via executable_path so that no "不支持的命令行标记" warnings appear.
# Based on agent-browser/cli/src/native/cdp/chrome.rs::build_chrome_args().
CHROME_UNIVERSAL_ARGS: List[str] = [
    "--disable-background-networking",
    "--disable-backgrounding-occluded-windows",
    "--disable-component-update",
    "--disable-default-apps",
    "--disable-hang-monitor",
    "--disable-popup-blocking",
    "--disable-prompt-on-repost",
    "--disable-sync",
    "--disable-features=Translate",
    "--enable-features=NetworkService,NetworkServiceInProcess",
    "--metrics-recording-only",
    "--no-first-run",
    "--no-default-browser-check",
    "--password-store=basic",
    "--use-mock-keychain",
]

# ========== Chrome Docker Args (browser-use/profile.py:84-94) ==========
CHROME_DOCKER_ARGS: List[str] = [
    "--no-sandbox",
    "--disable-gpu-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--no-xshm",
    "--no-zygote",
    "--disable-site-isolation-trials",
]

# ========== Chrome Disable Security Args (browser-use/profile.py:97-105) ==========
CHROME_DISABLE_SECURITY_ARGS: List[str] = [
    "--disable-site-isolation-trials",
    "--disable-web-security",
    "--disable-features=IsolateOrigins,site-per-process",
    "--allow-running-insecure-content",
    "--ignore-certificate-errors",
    "--ignore-ssl-errors",
    "--ignore-certificate-errors-spki-list",
]

# ========== Chrome Ignore Default Args (browser-use/profile.py:390-396) ==========
CHROME_IGNORE_DEFAULT_ARGS: List[str] = [
    "--enable-automation",
    "--disable-extensions",
    "--hide-scrollbars",
    "--disable-features=AcceptCHFrame,AutoExpandDetailsElement,AvoidUnnecessaryBeforeUnloadCheckSync,CertificateTransparencyComponentUpdater,DeferRendererTasksAfterInput,DestroyProfileOnBrowserClose,DialMediaRouteProvider,ExtensionManifestV2Disabled,GlobalMediaControls,HttpsUpgrades,ImprovedCookieControls,LazyFrameLoading,LensOverlay,MediaRouter,PaintHolding,ThirdPartyStoragePartitioning,Translate",
]

# ========== Extensions (browser-use/profile.py:944-981) ==========
STEALTH_EXTENSIONS: Dict[str, Dict[str, str]] = {
    # uBlock Origin Lite (MV3) — replaces uBlock Origin (MV2, deprecated in Chrome 127+)
    "ublock_origin": {
        "name": "uBlock Origin Lite",
        "id": "ddkjiahejlhfcafbddmgiahcphecmpfh",
        "url": "https://clients2.google.com/service/update2/crx?response=redirect&prodversion=133&acceptformat=crx3&x=id%3Dddkjiahejlhfcafbddmgiahcphecmpfh%26uc",
    },
    "cookie_consent": {
        "name": "I still don't care about cookies",
        "id": "edibdbjcniadpccecjdfdjjppcpchdlm",
        "url": "https://clients2.google.com/service/update2/crx?response=redirect&prodversion=133&acceptformat=crx3&x=id%3Dedibdbjcniadpccecjdfdjjppcpchdlm%26uc",
    },
    "force_background_tab": {
        "name": "Force Background Tab",
        "id": "gidlfommnbibbmegmgajdbikelkdcmcl",
        "url": "https://clients2.google.com/service/update2/crx?response=redirect&prodversion=133&acceptformat=crx3&x=id%3Dgidlfommnbibbmegmgajdbikelkdcmcl%26uc",
    },
}


@dataclass
class StealthConfig:
    """Configuration for stealth mode anti-detection.

    All default values align with browser-use defaults.

    Parameters
    ----------
    enabled : bool
        Whether stealth mode is enabled. Default True.
    enable_extensions : bool
        Whether to load anti-detection extensions. Default True.
        Note: Extensions require headless=False and persistent context.
    disable_security : bool
        Whether to disable security features (CORS, etc.). Default False.
        Only enable for trusted sites.
    in_docker : bool
        Whether running in Docker environment. Auto-detected by default.
    cookie_whitelist_domains : list[str]
        Domains to whitelist in cookie consent extension.
    permissions : list[str]
        Browser permissions to grant.
    extension_cache_dir : Path
        Directory to cache downloaded extensions.

    Examples
    --------
    # Default stealth config
    >>> config = StealthConfig()

    # Stealth without extensions (works with headless)
    >>> config = StealthConfig(enable_extensions=False)

    # Full stealth with security disabled (for testing)
    >>> config = StealthConfig(disable_security=True)
    """

    enabled: bool = True
    enable_extensions: bool = True
    disable_security: bool = False
    minimal_args: bool = False
    in_docker: bool = field(default_factory=lambda: sys.platform != "darwin" and os.path.exists("/.dockerenv"))
    cookie_whitelist_domains: List[str] = field(
        default_factory=lambda: ["nature.com", "qatarairways.com"]
    )
    permissions: List[str] = field(
        default_factory=lambda: ["clipboard-read", "clipboard-write", "notifications"]
    )
    extension_cache_dir: Path = field(
        default_factory=lambda: Path.home() / ".cache" / "bridgic-browser" / "extensions"
    )

    def can_use_extensions(self, headless: bool) -> bool:
        """Check if extensions can be used with current settings.

        Extensions require headless=False. Playwright's headless mode uses
        chromium-headless-shell which does not support loading extensions.
        """
        return self.enable_extensions and not headless


class StealthArgsBuilder:
    """Builder for stealth Chrome arguments."""

    def __init__(self, config: StealthConfig):
        self.config = config
        self._extension_paths: List[str] = []

    def build_args(self, viewport_width: int = 1600, viewport_height: int = 900) -> List[str]:
        """Build Chrome launch arguments for stealth mode.

        Parameters
        ----------
        viewport_width : int
            Viewport width for window-size arg.
        viewport_height : int
            Viewport height for window-size arg.

        Returns
        -------
        List[str]
            Chrome command line arguments.
        """
        if not self.config.enabled:
            return []

        if self.config.minimal_args:
            # Minimal universal args (agent-browser style) — safe for system Chrome on any platform.
            # Anti-detection relies entirely on the JS init script injected by get_init_script().
            args = list(CHROME_UNIVERSAL_ARGS)
        else:
            args = list(CHROME_STEALTH_ARGS)
            # Linux-only args (skip on macOS/Windows to avoid "unsupported flag" warnings)
            if sys.platform == "linux":
                args.extend(CHROME_LINUX_ONLY_ARGS)

        # Add disabled components
        args.append(f"--disable-features={','.join(CHROME_DISABLED_COMPONENTS)}")

        # Add window size
        args.append(f"--window-size={viewport_width},{viewport_height}")

        # Docker-specific args
        if self.config.in_docker:
            args.extend(CHROME_DOCKER_ARGS)

        # Security disabled args (optional)
        if self.config.disable_security:
            args.extend(CHROME_DISABLE_SECURITY_ARGS)

        return args

    def build_extension_args(self, headless: bool) -> List[str]:
        """Build extension-related Chrome arguments.

        Parameters
        ----------
        headless : bool
            Whether browser is in headless mode.

        Returns
        -------
        List[str]
            Extension-related Chrome arguments.
        """
        if not self.config.can_use_extensions(headless):
            return []

        # Ensure extensions are downloaded
        extension_paths = self._ensure_extensions()
        if not extension_paths:
            return []

        self._extension_paths = extension_paths

        return [
            f"--load-extension={','.join(extension_paths)}",
            f"--disable-extensions-except={','.join(extension_paths)}",
            "--enable-extensions",
            "--disable-extensions-file-access-check",
            "--enable-extension-activity-logging",
        ]

    def get_ignore_default_args(self) -> List[str]:
        """Get list of Playwright default args to ignore.

        Returns
        -------
        List[str]
            Args that Playwright adds by default but should be ignored.
        """
        if not self.config.enabled:
            return []
        return list(CHROME_IGNORE_DEFAULT_ARGS)

    def get_context_options(self) -> Dict[str, Any]:
        """Get stealth-related context options.

        Returns
        -------
        Dict[str, Any]
            Context options for stealth mode.
        """
        if not self.config.enabled:
            return {}

        options: Dict[str, Any] = {
            "permissions": self.config.permissions,
            "accept_downloads": True,
        }
        return options

    def get_init_script(self, locale: Optional[str] = None) -> Optional[str]:
        """Return a JS init script to patch navigator/window properties.

        The script runs in the main world before any page script so that
        property overrides are in place before bot-detection code runs.

        Parameters
        ----------
        locale:
            Browser locale (e.g. ``"zh-CN"``, ``"fr-FR"``). Used to build a
            consistent ``navigator.languages`` array so that
            ``navigator.language === navigator.languages[0]`` (a detectable
            inconsistency when they diverge). When *None*, defaults to
            ``["en-US", "en"]``.

        Returns None when stealth is disabled.
        """
        if not self.config.enabled:
            return None

        # Build navigator.languages from the locale so it matches navigator.language
        if locale:
            normalized = locale.replace("_", "-")
            parts = normalized.split("-")
            base = parts[0]
            langs: list[str] = [normalized]
            if base != normalized:
                langs.append(base)
            # Always include English as fallback unless the locale is already English
            if base.lower() != "en" and "en" not in langs:
                langs.append("en")
        else:
            langs = ["en-US", "en"]

        return _STEALTH_INIT_SCRIPT_TEMPLATE.replace("__BRIDGIC_LANGS__", json.dumps(langs))

    def _ensure_extensions(self) -> List[str]:
        """Download and extract extensions if needed.

        Checks (in priority order):
          1. Cache dir already has the extracted extension — reuse it directly.
          2. Bundled zip in bridgic/browser/extensions/<id>.zip — extract once.
          3. Network download as fallback.

        Returns
        -------
        List[str]
            Paths to extracted extension directories.
        """
        cache_dir = self.config.extension_cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)
        # bridgic/browser/extensions/ — zips shipped with the package, work offline
        bundled_dir = Path(__file__).parent.parent / "extensions"
        paths = []

        bundled_zip = bundled_dir / "extensions.zip"
        for key, ext_info in STEALTH_EXTENSIONS.items():
            ext_dir = cache_dir / ext_info["id"]

            if not (ext_dir / "manifest.json").exists():
                if bundled_zip.exists():
                    ext_dir.mkdir(parents=True, exist_ok=True)
                    prefix = ext_info["id"] + "/"
                    with zipfile.ZipFile(bundled_zip, "r") as zf:
                        members = [m for m in zf.namelist() if m.startswith(prefix)]
                        for member in members:
                            rel = member[len(prefix):]
                            if not rel:
                                continue
                            target = ext_dir / rel
                            if member.endswith("/"):
                                target.mkdir(parents=True, exist_ok=True)
                            else:
                                target.parent.mkdir(parents=True, exist_ok=True)
                                target.write_bytes(zf.read(member))
                else:
                    self._download_and_extract_extension(ext_info, ext_dir)

            if key == "cookie_consent":
                self._apply_cookie_extension_patch(ext_dir)

            paths.append(str(ext_dir))

        return paths

    def _download_and_extract_extension(
        self, ext_info: Dict[str, str], ext_dir: Path
    ) -> None:
        """Download and extract a Chrome extension.

        Parameters
        ----------
        ext_info : Dict[str, str]
            Extension info dict with name, id, url.
        ext_dir : Path
            Directory to extract extension to.
        """
        import logging

        logger = logging.getLogger(__name__)

        cache_dir = self.config.extension_cache_dir
        crx_path = cache_dir / f"{ext_info['id']}.crx"

        logger.info(f"Downloading extension: {ext_info['name']}...")
        urllib.request.urlretrieve(ext_info["url"], crx_path)

        ext_dir.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(crx_path, "r") as zf:
                zf.extractall(ext_dir)
        except zipfile.BadZipFile:
            self._extract_crx3(crx_path, ext_dir)

        crx_path.unlink()
        logger.info(f"Extension installed: {ext_info['name']}")

    def _extract_crx3(self, crx_path: Path, ext_dir: Path) -> None:
        """Extract CRX3 format Chrome extension.

        Parameters
        ----------
        crx_path : Path
            Path to CRX file.
        ext_dir : Path
            Directory to extract to.
        """
        with open(crx_path, "rb") as f:
            magic = f.read(4)
            if magic != b"Cr24":
                raise ValueError("Invalid CRX file")

            version = int.from_bytes(f.read(4), "little")
            if version == 2:
                pubkey_len = int.from_bytes(f.read(4), "little")
                sig_len = int.from_bytes(f.read(4), "little")
                f.seek(16 + pubkey_len + sig_len)
            elif version == 3:
                header_len = int.from_bytes(f.read(4), "little")
                f.seek(12 + header_len)

            zip_data = f.read()

        with zipfile.ZipFile(io.BytesIO(zip_data), "r") as zf:
            zf.extractall(ext_dir)

    def _apply_cookie_extension_patch(self, ext_dir: Path) -> None:
        """Apply whitelist patch to cookie consent extension.

        Based on browser-use/profile.py:1033-1095

        Parameters
        ----------
        ext_dir : Path
            Extension directory path.
        """
        bg_path = ext_dir / "data" / "background.js"
        if not bg_path.exists():
            return

        try:
            content = bg_path.read_text(encoding="utf-8")

            whitelist_domains = self.config.cookie_whitelist_domains
            whitelist_entries = [f'        "{domain}": true' for domain in whitelist_domains]
            whitelist_js = "{\n" + ",\n".join(whitelist_entries) + "\n      }"

            old_init = """async function initialize(checkInitialized, magic) {
  if (checkInitialized && initialized) {
    return;
  }
  loadCachedRules();
  await updateSettings();
  await recreateTabList(magic);
  initialized = true;
}"""

            new_init = f"""// Pre-populate storage with configurable domain whitelist if empty
async function ensureWhitelistStorage() {{
  const result = await chrome.storage.local.get({{ settings: null }});
  if (!result.settings) {{
    const defaultSettings = {{
      statusIndicators: true,
      whitelistedDomains: {whitelist_js}
    }};
    await chrome.storage.local.set({{ settings: defaultSettings }});
  }}
}}

async function initialize(checkInitialized, magic) {{
  if (checkInitialized && initialized) {{
    return;
  }}
  loadCachedRules();
  await ensureWhitelistStorage();
  await updateSettings();
  await recreateTabList(magic);
  initialized = true;
}}"""

            if old_init in content:
                content = content.replace(old_init, new_init)
                bg_path.write_text(content, encoding="utf-8")
        except Exception:
            pass


def create_stealth_config(
    enabled: bool = True,
    enable_extensions: bool = True,
    disable_security: bool = False,
    **kwargs: Any,
) -> StealthConfig:
    """Create a stealth configuration.

    Convenience function for creating StealthConfig.

    Parameters
    ----------
    enabled : bool
        Whether stealth mode is enabled.
    enable_extensions : bool
        Whether to load extensions.
    disable_security : bool
        Whether to disable security features.
    **kwargs
        Additional StealthConfig parameters.

    Returns
    -------
    StealthConfig
        Configured stealth config.
    """
    return StealthConfig(
        enabled=enabled,
        enable_extensions=enable_extensions,
        disable_security=disable_security,
        **kwargs,
    )


__all__ = [
    "StealthConfig",
    "StealthArgsBuilder",
    "create_stealth_config",
    "CHROME_STEALTH_ARGS",
    "CHROME_LINUX_ONLY_ARGS",
    "CHROME_UNIVERSAL_ARGS",
    "CHROME_DISABLED_COMPONENTS",
    "CHROME_DOCKER_ARGS",
    "CHROME_DISABLE_SECURITY_ARGS",
    "CHROME_IGNORE_DEFAULT_ARGS",
    "STEALTH_EXTENSIONS",
]
