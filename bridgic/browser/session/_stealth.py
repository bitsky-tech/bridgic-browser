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
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


# ========== JS Init Script — navigator/window property patches ==========
# Injected into every page (main world) before any page script runs.
# Targets the most common bot-detection signals as identified by Cloudflare,
# DataDome, PerimeterX and the rebrowser-bot-detector test suite (2024-2025).
_STEALTH_INIT_SCRIPT_TEMPLATE: str = """
(function () {
  // ── Function.prototype.toString spoofing ──────────────────────────────────
  // MUST be first — all subsequent patches register their functions here.
  //
  // Detection technique: Cloudflare Turnstile, DataDome, Akamai call .toString()
  // on browser APIs to detect monkey-patching:
  //   WebGLRenderingContext.prototype.getParameter.toString()
  //   → patched:  "function (parameter) { const _val = ... }"   ← BUSTED
  //   → expected: "function getParameter() { [native code] }"
  //
  // We intercept Function.prototype.toString so every function registered via
  // _mkNative() always reports as native, closing this entire class of detection.
  const _nativeFns = new WeakSet();
  const _nativeFnNames = new WeakMap();
  const _mkNative = (fn, name) => {
    _nativeFns.add(fn);
    if (name !== undefined) _nativeFnNames.set(fn, name);
    return fn;
  };
  const _origFnToString = Function.prototype.toString;
  Function.prototype.toString = _mkNative(function toString() {
    if (_nativeFns.has(this)) {
      const _n = _nativeFnNames.has(this) ? _nativeFnNames.get(this) : (this.name || '');
      return `function ${_n}() { [native code] }`;
    }
    return _origFnToString.call(this);
  }, 'toString');

  // ── navigator.webdriver ────────────────────────────────────────────────────
  // --disable-blink-features=AutomationControlled removes this from
  // Navigator.prototype in the main frame. Only patch when the property
  // actually exists (sub-frames or environments without the flag) to avoid
  // creating a detectable own-property where real Chrome has none.
  // 'webdriver' in navigator should be false in real Chrome — adding it even
  // as undefined is a fingerprint signal.
  const _wdDesc = Object.getOwnPropertyDescriptor(Navigator.prototype, 'webdriver');
  if (_wdDesc) {
    // Property is defined on the prototype — override to return undefined
    Object.defineProperty(Navigator.prototype, 'webdriver', {
      get: _mkNative(function () { return undefined; }, ''),
      configurable: true,
    });
  } else if (navigator.webdriver !== undefined) {
    // Fallback: own property on the instance (e.g. some sub-frame environments)
    Object.defineProperty(navigator, 'webdriver', {
      get: _mkNative(function () { return undefined; }, ''),
      configurable: true,
    });
  }
  // else: property is already absent — safest to leave it alone

  // ── navigator.plugins / mimeTypes ─────────────────────────────────────────
  // Headless Chrome exposes 0 plugins. Real Chrome has PDF Viewer entries.
  // Methods on plugin/mime objects are registered as native so .toString() checks pass.
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
    p.item      = _mkNative(function item(i) { return p[i] ?? null; }, 'item');
    p.namedItem = _mkNative(function namedItem(n) { const idx = _pdfMimes.findIndex(m => m.type === n); return idx >= 0 ? p[idx] : null; }, 'namedItem');
    return p;
  };

  const _plugins = [
    _makePlugin('PDF Viewer',                'Portable Document Format'),
    _makePlugin('Chrome PDF Viewer',         'Portable Document Format'),
    _makePlugin('Chromium PDF Viewer',       'Portable Document Format'),
    _makePlugin('Microsoft Edge PDF Viewer', 'Portable Document Format'),
    _makePlugin('WebKit built-in PDF',       'Portable Document Format'),
  ];

  // Link global mimeTypes entries to the primary plugin (PDF Viewer), matching Chrome behaviour
  _pdfMimes.forEach((m) => { m.enabledPlugin = _plugins[0]; });

  const _pluginList = Object.assign([..._plugins], {
    item:      _mkNative(function item(i) { return _plugins[i] ?? null; }, 'item'),
    namedItem: _mkNative(function namedItem(n) { return _plugins.find(p => p.name === n) ?? null; }, 'namedItem'),
    refresh:   _mkNative(function refresh() {}, 'refresh'),
    length:    _plugins.length,
  });

  Object.defineProperty(navigator, 'plugins', {
    get: _mkNative(function () { return _pluginList; }, ''),
    configurable: true,
  });

  const _mimeList = Object.assign([..._pdfMimes], {
    item:      _mkNative(function item(i) { return _pdfMimes[i] ?? null; }, 'item'),
    namedItem: _mkNative(function namedItem(n) { return _pdfMimes.find(m => m.type === n) ?? null; }, 'namedItem'),
    length:    _pdfMimes.length,
  });

  Object.defineProperty(navigator, 'mimeTypes', {
    get: _mkNative(function () { return _mimeList; }, ''),
    configurable: true,
  });

  // ── navigator.languages ───────────────────────────────────────────────────
  // __BRIDGIC_LANGS__ is replaced by get_init_script() based on the Browser locale setting.
  // Keeping navigator.languages[0] consistent with navigator.language avoids a detectable mismatch.
  try {
    Object.defineProperty(navigator, 'languages', {
      get: _mkNative(function () { return __BRIDGIC_LANGS__; }, ''),
      configurable: true,
    });
  } catch (_) {}

  // ── window.chrome ─────────────────────────────────────────────────────────
  // Headless Chrome may have a missing or incomplete chrome object.
  // Check all expected fields: a partial chrome (e.g. runtime present but csi/loadTimes absent)
  // is equally detectable and must also be patched.
  // All methods are wrapped with _mkNative so .toString() checks see native code.
  if (!window.chrome || !window.chrome.runtime || !window.chrome.csi || !window.chrome.loadTimes) {
    const _chrome = {
      app: {
        isInstalled:    false,
        getDetails:     _mkNative(function getDetails()     { return null; },            'getDetails'),
        getIsInstalled: _mkNative(function getIsInstalled() { return false; },           'getIsInstalled'),
        installState:   _mkNative(function installState()   { return 'not_installed'; }, 'installState'),
      },
      runtime: {
        connect:     _mkNative(function connect()     {}, 'connect'),
        sendMessage: _mkNative(function sendMessage() {}, 'sendMessage'),
      },
      csi: _mkNative(function csi() {
        return {
          onloadT: Date.now(),
          pageT:   Date.now() - (performance.timeOrigin ?? performance.timing?.navigationStart ?? 0),
          startE:  Date.now() - 1000,
          tran:    15,
        };
      }, 'csi'),
      loadTimes: _mkNative(function loadTimes() {
        return {
          commitLoadTime:               Date.now() / 1000 - 1,
          connectionInfo:               'h2',
          finishDocumentLoadTime:       Date.now() / 1000,
          finishLoadTime:               Date.now() / 1000,
          firstPaintAfterLoadTime:      0,
          firstPaintTime:               Date.now() / 1000 - 0.5,
          navigationType:               'Other',
          npnNegotiatedProtocol:        'h2',
          requestTime:                  Date.now() / 1000 - 1,
          startLoadTime:                Date.now() / 1000 - 1,
          wasAlternateProtocolAvailable: false,
          wasFetchedViaSpdy:            true,
          wasNpnNegotiated:             true,
        };
      }, 'loadTimes'),
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
  // Wrapped with _mkNative so permissions.query.toString() returns native code.
  if (navigator.permissions && navigator.permissions.query) {
    const _origQuery = navigator.permissions.query.bind(navigator.permissions);
    navigator.permissions.query = _mkNative(function query(params) {
      if (params && params.name === 'notifications') {
        return Promise.resolve({ state: Notification.permission === 'denied' ? 'default' : Notification.permission, onchange: null });
      }
      return _origQuery(params);
    }, 'query');
  }

  // ── Notification.permission ────────────────────────────────────────────────
  // Some headless environments default to 'denied'; real Chrome starts at 'default'
  // (never prompted). Consistent with the permissions.query patch above.
  if (window.Notification && Notification.permission === 'denied') {
    try {
      Object.defineProperty(Notification, 'permission', {
        get: _mkNative(function () { return 'default'; }, ''),
        configurable: true,
      });
    } catch (_) {}
  }

  // ── document.hasFocus / visibility ────────────────────────────────────────
  // Headless: the tab is never the OS focus target → hasFocus() returns false,
  // visibilityState = 'hidden'. Cloudflare, DataDome and PerimeterX all probe
  // these to identify headless environments. Real tabs are visible and focused.
  try { document.hasFocus = _mkNative(function hasFocus() { return true; }, 'hasFocus'); } catch (_) {}
  try { Object.defineProperty(document, 'hidden',          { get: _mkNative(function () { return false;     }, ''), configurable: true }); } catch (_) {}
  try { Object.defineProperty(document, 'visibilityState', { get: _mkNative(function () { return 'visible'; }, ''), configurable: true }); } catch (_) {}

  // ── window.outerWidth / outerHeight ───────────────────────────────────────
  // Headless may set these to 0. With --headless=new the screen context option
  // provides correct values, but guard here for any remaining edge cases.
  // No +85 offset: new headless has no browser chrome UI, so outerHeight == innerHeight.
  if (window.outerWidth === 0) {
    try { Object.defineProperty(window, 'outerWidth',  { get: _mkNative(function () { return window.innerWidth;  }, ''), configurable: true }); } catch (_) {}
  }
  if (window.outerHeight === 0) {
    try { Object.defineProperty(window, 'outerHeight', { get: _mkNative(function () { return window.innerHeight; }, ''), configurable: true }); } catch (_) {}
  }

  // ── navigator.deviceMemory ────────────────────────────────────────────────
  // Real Chrome returns 4 or 8 GB; some headless environments return undefined.
  if (navigator.deviceMemory === undefined) {
    try { Object.defineProperty(navigator, 'deviceMemory', { get: _mkNative(function () { return 8; }, ''), configurable: true }); } catch (_) {}
  }

  // ── navigator.hardwareConcurrency ─────────────────────────────────────────
  // Real Chrome reports actual CPU core count; headless may return 0 or 1.
  if (!navigator.hardwareConcurrency || navigator.hardwareConcurrency < 2) {
    try { Object.defineProperty(navigator, 'hardwareConcurrency', { get: _mkNative(function () { return 8; }, ''), configurable: true }); } catch (_) {}
  }

  // ── navigator.connection (NetworkInformation API) ─────────────────────────
  // Some headless environments lack this object entirely.
  if (!navigator.connection) {
    try {
      Object.defineProperty(navigator, 'connection', {
        get: _mkNative(function () { return { effectiveType: '4g', downlink: 10, rtt: 100, saveData: false }; }, ''),
        configurable: true,
      });
    } catch (_) {}
  }

  // ── WebGL vendor / renderer ───────────────────────────────────────────────
  // Headless on servers without a real GPU reports "Google SwiftShader" for
  // UNMASKED_VENDOR_WEBGL / UNMASKED_RENDERER_WEBGL — a well-known bot signal.
  // Replace only when the real value is SwiftShader/Google; preserve authentic
  // GPU strings on headed macOS/Windows/Linux with a physical GPU so the WebGL
  // fingerprint stays consistent with DPI, Canvas, and font rendering signals.
  // Both WebGLRenderingContext and WebGL2RenderingContext must be patched
  // because modern bot-detection probes both.
  // _mkNative ensures getParameter.toString() returns "[native code]" — without
  // this, calling .toString() on the patched method immediately reveals the override.
  (function () {
    const _patchWebGL = (Ctx) => {
      if (!Ctx) return;
      const _orig = Ctx.prototype.getParameter;
      Ctx.prototype.getParameter = _mkNative(function getParameter(parameter) {
        const _val = _orig.call(this, parameter);
        if (parameter === 37445) {                                    // UNMASKED_VENDOR_WEBGL
          if (_val && (_val.includes('Google') || _val === '')) return 'Intel Inc.';
          return _val;
        }
        if (parameter === 37446) {                                    // UNMASKED_RENDERER_WEBGL
          if (_val && (_val.includes('SwiftShader') || _val === '')) return 'Intel Iris OpenGL Engine';
          return _val;
        }
        return _val;
      }, 'getParameter');
    };
    _patchWebGL(window.WebGLRenderingContext);
    _patchWebGL(window.WebGL2RenderingContext);
  })();
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

# ========== Chrome Args for Headed Mode (minimal, close to real user Chrome) ==========
# Used when headless_intent=False. Only keep flags that suppress automation UI
# artifacts. Do NOT include --disable-background-networking or other flags that
# alter observable browser behaviour — they create a detectable fingerprint and
# can interfere with Cloudflare Turnstile's challenge AJAX requests.
CHROME_STEALTH_ARGS_HEADED: List[str] = [
    "--disable-blink-features=AutomationControlled",   # removes navigator.webdriver at Blink level
    "--no-first-run",                                   # suppresses first-run wizard
    "--no-default-browser-check",                       # suppresses "set as default" prompt
    "--disable-infobars",                               # removes "Chrome is controlled by automation" bar
    "--hide-crash-restore-bubble",                      # suppresses crash restore dialog
    "--no-service-autorun",                             # avoids background service start-up noise
    "--log-level=2",                                    # suppress noisy console output (same as headless)
    "--disable-search-engine-choice-screen",            # suppresses EU search-engine choice screen
    "--unsafely-disable-devtools-self-xss-warnings",    # suppresses devtools console XSS warning
    "--disable-popup-blocking",                         # allow test/agent pop-ups (consistent with headless)
]

# ========== Chrome Disabled Components for Headed Mode ==========
# Minimal subset: only suppress automation-detection signals and obvious UI noise.
# The full CHROME_DISABLED_COMPONENTS list disables HttpsUpgrades, MediaRouter,
# Translate, etc. — features that real users have enabled.  Disabling them in
# headed mode makes the browser fingerprint deviate detectably from normal Chrome.
CHROME_DISABLED_COMPONENTS_HEADED: List[str] = [
    "AutomationControlled",   # removes Chrome-level webdriver signal (belt-and-suspenders)
    "InfiniteSessionRestore", # suppresses crash-restore dialog
    # BackForwardCache intentionally omitted: real users have bfcache enabled and
    # JS can detect its absence via page lifecycle events. Agent code should handle
    # bfcache restore correctly (re-run get_snapshot after navigation) rather than
    # disabling the feature.
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
    use_new_headless : bool
        Use full Chromium binary with ``--headless=new`` instead of the
        stripped ``chromium-headless-shell`` binary. Default True.
        Only active when ``enabled=True``, ``headless=True``, and not using
        system Chrome (``channel`` / ``executable_path``).  Set to False to
        restore the old ``chromium-headless-shell`` behaviour.
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
    use_new_headless: bool = True
    """Use full Chromium binary with --headless=new instead of chromium-headless-shell.
    Only active when stealth.enabled=True and headless=True (and not using system Chrome).
    Set to False to restore old chromium-headless-shell behaviour."""
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

        Extensions require headless=False. Chrome does not support loading
        extensions in any headless mode (--headless or --headless=new).
        """
        return self.enable_extensions and not headless


class StealthArgsBuilder:
    """Builder for stealth Chrome arguments."""

    def __init__(self, config: StealthConfig):
        self.config = config
        self._extension_paths: List[str] = []

    def build_args(
        self,
        viewport_width: int = 1600,
        viewport_height: int = 900,
        headless_intent: bool = True,
        locale: Optional[str] = None,
    ) -> List[str]:
        """Build Chrome launch arguments for stealth mode.

        Parameters
        ----------
        viewport_width : int
            Viewport width for window-size arg.
        viewport_height : int
            Viewport height for window-size arg.
        headless_intent : bool
            Whether the user's intent is headless (default True for backward
            compatibility). Controls both the flag set and window visibility:
            - True  (headless): uses full ``CHROME_STEALTH_ARGS`` (50+ flags) to
              mask headless-specific signals, and appends ``--headless=new`` so
              Chrome runs without a visible window.
            - False (headed): uses minimal ``CHROME_STEALTH_ARGS_HEADED`` (~10
              flags) so the browser fingerprint stays close to a real Chrome user.
              ``--headless=new`` is never added.
        locale : str, optional
            Browser locale (e.g. ``"zh-CN"``). Used to set ``--lang=`` in
            headed mode so the flag matches the browser's actual locale.
            Ignored in headless mode (``--lang=en-US`` is baked into
            ``CHROME_STEALTH_ARGS``).

        Returns
        -------
        List[str]
            Chrome command line arguments.
        """
        if not self.config.enabled:
            return []

        if headless_intent:
            # Full stealth args for headless mode (original behaviour).
            args = list(CHROME_STEALTH_ARGS)
            # Linux-only args (skip on macOS/Windows to avoid "unsupported flag" warnings)
            if sys.platform == "linux":
                args.extend(CHROME_LINUX_ONLY_ARGS)
            # Disable features list (full set for headless)
            args.append(f"--disable-features={','.join(CHROME_DISABLED_COMPONENTS)}")
        else:
            # Minimal stealth args for headed mode.
            # Goal: look like a real Chrome user, not an automated browser.
            # Using 50+ disable-* flags in headed mode creates a unique fingerprint
            # that bot-detection (Cloudflare Turnstile etc.) can identify.
            args = list(CHROME_STEALTH_ARGS_HEADED)
            if sys.platform == "linux":
                # Only --disable-dev-shm-usage is relevant on Linux headed;
                # --ash-no-nudges / --suppress-message-center-popups are ChromeOS-only.
                args.append("--disable-dev-shm-usage")
            # Minimal features list for headed mode
            args.append(f"--disable-features={','.join(CHROME_DISABLED_COMPONENTS_HEADED)}")
            # Set --lang from actual locale so it's consistent with navigator.language
            lang = locale.replace("_", "-") if locale else "en-US"
            args.append(f"--lang={lang}")

        # ── Shared args (both modes) ──────────────────────────────────────────
        # Add window size
        args.append(f"--window-size={viewport_width},{viewport_height}")

        # Docker-specific args
        if self.config.in_docker:
            args.extend(CHROME_DOCKER_ARGS)

        # Security disabled args (optional)
        if self.config.disable_security:
            args.extend(CHROME_DISABLE_SECURITY_ARGS)

        # New headless mode: user wants no window + stealth enabled.
        # _browser.py tells Playwright headless=False so it picks the full chromium
        # binary (not headless-shell), then we supply these args so Chrome itself
        # still runs without a visible window via --headless=new.
        # Playwright normally adds --hide-scrollbars / --mute-audio / --blink-settings
        # only when it receives headless=True; we add them explicitly here instead.
        # Only active when headless_intent=True (headed mode never needs --headless=new).
        if headless_intent and self.config.use_new_headless:
            args.extend([
                "--headless=new",
                "--hide-scrollbars",
                "--mute-audio",
                "--blink-settings=primaryHoverType=2,availableHoverTypes=2,primaryPointerType=4,availablePointerTypes=4",
            ])

        return args

    def build_extension_args(self, headless: bool) -> List[str]:
        """Build extension-related Chrome arguments.

        Parameters
        ----------
        headless : bool
            The *user's* headless intent (i.e. ``Browser._headless``), NOT the
            ``headless`` value passed to Playwright.  When ``use_new_headless``
            is active, Playwright receives ``headless=False`` so it picks the
            full Chromium binary, but the user still wants a windowless browser.
            Extensions are disabled whenever the user's intent is headless,
            because Chrome does not support loading extensions in any headless
            mode (``--headless`` or ``--headless=new``).

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
    "CHROME_STEALTH_ARGS_HEADED",
    "CHROME_LINUX_ONLY_ARGS",
    "CHROME_DISABLED_COMPONENTS",
    "CHROME_DISABLED_COMPONENTS_HEADED",
    "CHROME_DOCKER_ARGS",
    "CHROME_DISABLE_SECURITY_ARGS",
    "CHROME_IGNORE_DEFAULT_ARGS",
    "STEALTH_EXTENSIONS",
]
