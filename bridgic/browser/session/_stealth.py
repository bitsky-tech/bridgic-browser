"""
Stealth mode configuration for anti-bot detection.

Based on browser-use (https://github.com/browser-use/browser-use)

This module provides stealth configurations to make automated browsers
appear more like regular user browsers, helping bypass bot detection.
"""

from __future__ import annotations

import json
import os
import sys
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


# ========== Anti devtools-detector Script ==========
# Neutralises the devtools-detector library
# (https://github.com/AEPKILL/devtools-detector).
#
# Safe for both headless AND headed modes — only patches console.table,
# window.devtoolsFormatters, and the Function constructor.  Does NOT
# touch navigator/window.chrome properties that would break Cloudflare
# Turnstile challenge iframes.
#
# Uses the same _mkNative / _nativeFns infrastructure defined in the
# main stealth init script when running in headless mode.  In headed
# mode (where the main script is NOT injected), we bootstrap a minimal
# _mkNative locally so .toString() on our patches still reports
# "[native code]".
_ANTI_DEVTOOLS_DETECTOR_SCRIPT: str = """
(function () {
  // Only run in the top-level frame.  CAPTCHA providers (Cloudflare
  // Turnstile, reCAPTCHA, hCaptcha, etc.) use transient iframes for
  // verification — these iframes may start as about:blank so their
  // hostname is unknown at init-script time.  Patching Function or
  // toString inside them causes detectable inconsistencies that fail
  // the challenge.  devtools-detector runs in the main page context,
  // so skipping child frames does not weaken the protection.
  try { if (window !== window.top) return; } catch (_) { return; }

  // ── Bootstrap _mkNative (reuse existing or create minimal version) ──
  // In headless mode the main stealth script has already defined _mkNative
  // in an earlier IIFE scope — we cannot reach it, so we always create our
  // own lightweight copy.  The overhead is negligible (one WeakSet + WeakMap).
  var _nativeFns, _nativeFnNames, _mkNative;
  _nativeFns   = new WeakSet();
  _nativeFnNames = new WeakMap();
  _mkNative = function (fn, name) {
    _nativeFns.add(fn);
    if (name !== undefined) _nativeFnNames.set(fn, name);
    return fn;
  };
  // Intercept Function.prototype.toString for OUR patches only.
  // If the main stealth script already installed a toString override,
  // chain through it — our WeakSet simply won't match functions we
  // didn't register, so the call falls through transparently.
  var _prevToString = Function.prototype.toString;
  Function.prototype.toString = _mkNative(function toString() {
    if (_nativeFns.has(this)) {
      var _n = _nativeFnNames.has(this) ? _nativeFnNames.get(this) : (this.name || '');
      return 'function ' + _n + '() { [native code] }';
    }
    return _prevToString.call(this);
  }, 'toString');

  // ── 1. performanceChecker: console.table timing neutralization ────
  // devtools-detector compares console.table(largeArray) vs
  // console.log(largeArray) execution time.  Under CDP Runtime.enable,
  // console.table incurs extra formatting/serialization overhead.
  // Replacing it with console.log makes both paths identical in cost.
  // devtools-detector caches console.table at module-load time via
  // cacheConsoleMethod('table'), so this must run before page scripts.
  var _origLog = console.log;
  console.table = _mkNative(function table() {
    return _origLog.apply(console, arguments);
  }, 'table');

  // ── 2. devtoolsFormatterChecker: freeze devtoolsFormatters ────────
  // devtools-detector registers a custom formatter whose header() fires
  // when CDP serialises console.log output.  Make the property a no-op
  // accessor so the formatter array can never be installed.
  try {
    Object.defineProperty(window, 'devtoolsFormatters', {
      get: _mkNative(function () { return undefined; }, ''),
      set: _mkNative(function () {}, ''),
      configurable: false,
    });
  } catch (_) {}

  // ── 3. debuggerChecker: Function constructor interception ─────────
  // devtools-detector creates debugger-bearing functions dynamically via
  //   (() => {}).constructor('debugger')()
  // Intercept the Function constructor to strip the debugger keyword so
  // the constructed function body is empty (executes instantly, <1 ms).
  // The fallback raw `debugger;` in the catch block is handled at the
  // CDP level (Debugger.setSkipAllPauses).
  var _OrigFunction = Function;
  Function = _mkNative(function Function() {
    var args = [];
    for (var i = 0; i < arguments.length; i++) {
      var a = arguments[i];
      args.push(typeof a === 'string' ? a.replace(/\\bdebugger\\b/g, '') : a);
    }
    return _OrigFunction.apply(this, args);
  }, 'Function');
  Function.prototype = _OrigFunction.prototype;
  Object.defineProperty(Function.prototype, 'constructor', {
    value: Function, writable: true, configurable: true,
  });
  try { Object.setPrototypeOf(Function, Object.getPrototypeOf(_OrigFunction)); } catch (_) {}
  try { Object.defineProperty(Function, 'length', Object.getOwnPropertyDescriptor(_OrigFunction, 'length')); } catch (_) {}
  try { Object.defineProperty(Function, 'name', { value: 'Function', configurable: true }); } catch (_) {}
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
    "--hide-scrollbars",
    # NOTE: do NOT include "--disable-features=..." here.
    # Chrome's --disable-features is last-wins; we let Playwright add its own flag
    # and then append our combined flag in build_args() so ours wins with the full
    # merged feature set.  A hardcoded string would silently stop matching whenever
    # Playwright updates its feature list.
]

@dataclass
class StealthConfig:
    """Configuration for stealth mode anti-detection.

    All default values align with browser-use defaults.

    Parameters
    ----------
    enabled : bool
        Whether stealth mode is enabled. Default True.
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
    permissions : list[str]
        Browser permissions to grant.

    Examples
    --------
    # Default stealth config
    >>> config = StealthConfig()

    # Full stealth with security disabled (for testing)
    >>> config = StealthConfig(disable_security=True)
    """

    enabled: bool = True
    disable_security: bool = False
    use_new_headless: bool = True
    """Use full Chromium binary with --headless=new instead of chromium-headless-shell.
    Only active when stealth.enabled=True and headless=True (and not using system Chrome).
    Set to False to restore old chromium-headless-shell behaviour."""
    in_docker: bool = field(default_factory=lambda: sys.platform != "darwin" and os.path.exists("/.dockerenv"))
    permissions: List[str] = field(
        default_factory=lambda: ["clipboard-read", "clipboard-write", "notifications"]
    )


def _get_playwright_disabled_features() -> List[str]:
    """Read Playwright's default disabled-features list from its bundled JS.

    Playwright generates a single ``--disable-features=A,B,C`` flag whose contents
    change between Playwright versions.  Rather than hardcoding it (which becomes
    stale on every Playwright upgrade), we parse it at runtime so our combined flag
    always stays in sync.

    Returns an empty list if the file cannot be read (e.g. non-standard install).
    """
    import re as _re
    try:
        import playwright as _pw
        switches_js = Path(_pw.__file__).parent / "driver/package/lib/server/chromium/chromiumSwitches.js"
        text = switches_js.read_text(encoding="utf-8")
        # Locate the disabledFeatures array body: everything between the outer [ and ].filter
        m = _re.search(r'const disabledFeatures\s*=.*?\[(.+?)\]\.filter', text, _re.DOTALL)
        if not m:
            return []
        return [f for f in _re.findall(r'"([^"]+)"', m.group(1)) if f]
    except Exception:
        return []


class StealthArgsBuilder:
    """Builder for stealth Chrome arguments."""

    def __init__(self, config: StealthConfig):
        self.config = config

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
            # Override the hardcoded --lang=en-US with the actual locale so
            # that Chrome's Accept-Language header stays consistent with the
            # navigator.languages array patched by the JS init script.
            # Without this, a locale="zh-CN" user would have --lang=en-US
            # (Accept-Language: en-US) but navigator.languages=["zh-CN",…],
            # which is a detectable inconsistency for bot-detection systems.
            if locale:
                lang = locale.replace("_", "-")
                args = [a for a in args if not a.startswith("--lang=")]
                args.append(f"--lang={lang}")
            # Linux-only args (skip on macOS/Windows to avoid "unsupported flag" warnings)
            if sys.platform == "linux":
                args.extend(CHROME_LINUX_ONLY_ARGS)
            # Combine Playwright's disabled features with bridgic's full set.
            # Chrome's --disable-features is last-wins: since our flag is appended
            # after Playwright's default, ours overrides.  Merging ensures we don't
            # accidentally re-enable features that Playwright disables for stability
            # (e.g. AutoDeElevate, RenderDocument added in newer Playwright versions).
            pw_features = _get_playwright_disabled_features()
            combined = list(dict.fromkeys(pw_features + list(CHROME_DISABLED_COMPONENTS)))
            args.append(f"--disable-features={','.join(combined)}")
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
            # Combine Playwright's stability features with bridgic's stealth features
            # into a single --disable-features flag so it wins the last-wins race.
            # This ensures ThirdPartyStoragePartitioning (and other stability features)
            # stay disabled — without it, cross-origin OAuth iframes (Google login)
            # cannot access cookies, causing bot-detection failures.
            pw_features = _get_playwright_disabled_features()
            combined = list(dict.fromkeys(pw_features + list(CHROME_DISABLED_COMPONENTS_HEADED)))
            args.append(f"--disable-features={','.join(combined)}")
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

    def get_anti_devtools_script(self) -> Optional[str]:
        """Return JS to neutralise the devtools-detector library.

        Safe for **both** headless and headed modes — only patches
        ``console.table``, ``window.devtoolsFormatters``, and the
        ``Function`` constructor.  Does NOT touch navigator/window
        properties that would break Cloudflare Turnstile.

        Returns None when stealth is disabled.
        """
        if not self.config.enabled:
            return None
        return _ANTI_DEVTOOLS_DETECTOR_SCRIPT

def create_stealth_config(
    enabled: bool = True,
    disable_security: bool = False,
    **kwargs: Any,
) -> StealthConfig:
    """Create a stealth configuration.

    Convenience function for creating StealthConfig.

    Parameters
    ----------
    enabled : bool
        Whether stealth mode is enabled.
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
]
