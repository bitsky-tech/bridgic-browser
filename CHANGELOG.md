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

- **Tool System** (68+ tools organized by category)
  - Navigation tools: search, navigate_to_url, go_back, go_forward
  - Page control tools: reload_page, scroll_to_text, press_key, evaluate_javascript
  - Element interaction tools: click, input_text, hover, focus, drag, etc.
  - Mouse tools: mouse_move, mouse_click, mouse_drag, mouse_wheel
  - Keyboard tools: press_sequentially, key_down, key_up, fill_form, insert_text
  - Screenshot tools: take_screenshot, save_pdf
  - Network tools: console capture, network request monitoring
  - Dialog tools: setup_dialog_handler, handle_dialog
  - Storage tools: cookie management, storage state save/restore
  - Verification tools: verify_element_visible, verify_text_visible, verify_value
  - DevTools: start_tracing, stop_tracing, start_video, stop_video

- **Tool Presets**
  - MINIMAL: 10 tools for basic navigation
  - NAVIGATION: 4 tools for search and navigation
  - SCRAPING: 13 tools for data extraction
  - FORM_FILLING: 20 tools for form automation
  - TESTING: 28 tools for E2E testing
  - INTERACTIVE: 40 tools for full interaction
  - DEVELOPER: 18 tools for development
  - COMPLETE: 68 tools (all available)

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
