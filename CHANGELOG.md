# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Initial development release

## [0.0.1.dev1] - 2025-01-XX

### Added
- **Core Browser Session**
  - `Browser` class with 50+ configuration options
  - Automatic launch mode selection (isolated vs persistent context)
  - Built-in stealth mode with 50+ Chrome arguments for bot detection bypass
  - Smart download management with original filename preservation
  - Multi-page/tab support with page descriptions

- **Tool System** (67 tools organized by category)
  - Navigation: navigate_to, search, get_current_page_info, reload_page, go_back, go_forward
  - Snapshot: get_snapshot_text
  - Element Interaction: click, input_text, fill_form, scroll_into_view, select, options, check, uncheck, focus, hover, double_click, upload, drag (all by ref)
  - Tabs: get_tabs, new_tab, switch_tab, close_tab
  - Evaluate: evaluate_javascript, evaluate_javascript_on_ref
  - Keyboard: type_text, press_key, key_down, key_up
  - Mouse: mouse_wheel, mouse_click, mouse_move, mouse_drag, mouse_down, mouse_up
  - Wait: wait_for
  - Capture: take_screenshot, save_pdf
  - Network: start_network_capture, get_network_requests, stop_network_capture, wait_for_network_idle
  - Dialog: setup_dialog_handler, handle_dialog, remove_dialog_handler
  - Storage: cookie management, storage state save/restore
  - Verify: verify_text_visible, verify_element_visible, verify_url, verify_title, verify_element_state, verify_value
  - Developer: console capture, tracing, video recording
  - Lifecycle: browser_close, browser_resize

- **Tool Selection via ToolCategory**
  - 15 named categories: NAVIGATION, SNAPSHOT, ELEMENT_INTERACTION, TABS, EVALUATE, KEYBOARD, MOUSE, WAIT, CAPTURE, NETWORK, DIALOG, STORAGE, VERIFY, DEVELOPER, LIFECYCLE
  - `BrowserToolSetBuilder.for_categories(browser, ToolCategory.NAVIGATION, ...)` — select by category
  - `BrowserToolSetBuilder.for_tool_names(browser, "click_element_by_ref", ...)` — select by method name
  - `ToolCategory.ALL` — include all 67 tools

- **AI-Friendly Architecture**
  - Element reference system (refs) for stable element identification
  - Snapshot generation with accessibility tree
  - LLM-friendly page state representation with pagination support

- **Stealth Mode**
  - `StealthConfig` for customizable stealth settings
  - `StealthArgsBuilder` for Chrome argument generation
  - Support for browser extensions (uBlock Origin, Cookie Consent)
  - Docker-optimized configuration

- **Documentation**
  - Comprehensive README with English and Chinese sections
  - CONTRIBUTING.md with development guidelines
  - LOGGING.md for logging configuration

### Dependencies
- playwright >= 1.57.0
- pydantic >= 2.11.10
- bridgic-core >= 0.3.0b1
- bridgic-llms-openai >= 0.1.1

[Unreleased]: https://github.com/bitsky-tech/bridgic-browser/compare/v0.0.1.dev1...HEAD
[0.0.1.dev1]: https://github.com/bitsky-tech/bridgic-browser/releases/tag/v0.0.1.dev1
