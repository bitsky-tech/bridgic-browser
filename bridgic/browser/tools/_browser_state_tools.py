from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..session._browser import Browser  # pragma: no cover

logger = logging.getLogger(__name__)

MAX_CHAR_LIMIT = 30000

async def get_llm_repr(browser: "Browser",  
    start_from_char: int = 0, 
    interactive: bool = False,
    full_page: bool = False,
    filter_invisible: bool = True
) -> str:
    """Get LLM-friendly representation of current browser state.

    Retrieves comprehensive state information about the browser's current
    page, formatted for LLM consumption. The returned text includes:

    1. Page statistics: link count, interactive element count, total
       element count
    2. Current tab information: current tab's URL
    3. Page position information: pages above/below, total page count
    4. DOM tree text representation: detailed description of all interactive
       elements, each with a unique index identifier
    5. Page position markers: "page start", "page end" markers added before
       and after DOM text to help understand the visible area's position
       within the entire page

    For complex pages, the returned text may be very long. Use the
    `start_from_char` parameter for pagination:
    - First call uses default value 0, returns from the beginning
    - If the returned content indicates truncation and provides
      `next_start_char`, pass that value to `start_from_char` in the
      next call to continue from that position

    The returned text format is suitable for direct use with LLMs for
    understanding and decision-making, containing all key information
    needed for browser automation operations.

    Parameters
    ----------
    browser : Browser
        The browser instance to get state from.
    start_from_char : int, optional
        Character offset for pagination. When page content exceeds 30000
        characters, use the `next_start_char` value from the truncation
        notice to continue reading. Default is 0 (start from beginning).
    interactive : bool, optional
        If True, only include interactive elements (buttons, links, inputs)
        with flattened output. Default is False (include all elements).
    full_page : bool, optional
        If True, include all elements regardless of viewport position.
        If False (default), only include elements within the visible viewport.
    filter_invisible : bool, optional
        If True (default), filter out CSS-hidden elements (display:none,
        visibility:hidden, opacity:0, aria-hidden="true").
        If False, include all elements regardless of visibility.

    Returns
    -------
    str
        LLM-friendly page and DOM description, containing:
        - Page statistics (link count, interactive element count, etc.)
        - Current tab's URL
        - Page position information (pages above/below/total)
        - Interactive element list (indexed DOM tree text representation)
        - Page position markers (page start/end markers)

        Returns error message string on failure.
    """
    try:
        snapshot = await browser.get_snapshot(
            interactive=interactive,
            full_page=full_page,
            filter_invisible=filter_invisible,
        )
        if snapshot is None:
            error_msg = "Failed to get interface information"
            logger.error(f"[get_llm_repr] {error_msg}")
            return error_msg
        full_text = snapshot.tree

        # Provide pagination capability to avoid returning overly long text on complex pages
        total_length = len(full_text)

        if start_from_char > 0:
            if start_from_char >= total_length:
                error_msg = (
                    f"start_from_char ({start_from_char}) exceeds total page state length "
                    f"of {total_length} characters."
                )
                logger.error(f"[get_llm_repr] {error_msg}")
                return error_msg
            text = full_text[start_from_char:]
        else:
            text = full_text

        truncated = False
        next_start_char: int | None = None

        if len(text) > MAX_CHAR_LIMIT:
            truncate_at = MAX_CHAR_LIMIT

            # Try to truncate at natural breakpoints (paragraphs, sentences) to avoid breaking sentences
            paragraph_break = text.rfind("\n\n", MAX_CHAR_LIMIT - 500, MAX_CHAR_LIMIT)
            if paragraph_break > 0:
                truncate_at = paragraph_break
            else:
                sentence_break = text.rfind(".", MAX_CHAR_LIMIT - 200, MAX_CHAR_LIMIT)
                if sentence_break > 0:
                    truncate_at = sentence_break + 1

            text = text[:truncate_at]
            truncated = True
            next_start_char = (start_from_char or 0) + truncate_at

        # If truncation occurred, add a notice at the end to help caller continue pagination
        if truncated and next_start_char is not None:
            notice = (
                "\n\n[notice] Current page state text is too long, returned portion starting "
                f"from character {start_from_char} (this segment length {len(text)} / total "
                f"length {total_length} characters). To continue getting subsequent content, "
                f"use start_from_char={next_start_char} to call get_llm_repr again."
            )
            text = f"{text}{notice}"

        logger.info("[get_llm_repr] Successfully retrieved interface information")
        return text
    except Exception as e:
        error_msg = f"Failed to get interface information: {e}"
        logger.error(f"[get_llm_repr] {error_msg}")
        return error_msg


if __name__ == "__main__":
    import asyncio
    import os

    async def main():
        from bridgic.browser.session import Browser

        # Test URLs - covering different ARIA patterns
        test_urls = [
            # 1. Button - tests button role with div/span elements
            ("button", "https://www.w3.org/WAI/ARIA/apg/patterns/button/examples/button_idl/"),
            # 2. Toolbar - tests toolbar, radio group
            ("toolbar", "https://www.w3.org/WAI/ARIA/apg/patterns/toolbar/examples/toolbar/"),
            # 3. Combobox - tests combobox, listbox, option
            ("combobox", "https://www.w3.org/WAI/ARIA/apg/patterns/combobox/examples/combobox-autocomplete-both/"),
            # 4. Tabs - tests tab, tablist, tabpanel
            ("tabs", "https://www.w3.org/WAI/ARIA/apg/patterns/tabs/examples/tabs-automatic/"),
            # 5. Dialog - tests dialog role
            ("dialog", "https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/examples/dialog/"),
            # 6. Slider - tests slider role
            ("slider", "https://www.w3.org/WAI/ARIA/apg/patterns/slider/examples/slider-color-viewer/"),
        ]

        browser = Browser(
            headless=False,
            viewport={"width": 1440, "height": 900},
        )
        await browser.start()

        for name, url in test_urls:
            print(f"\n{'='*60}")
            print(f"Testing: {name}")
            print(f"URL: {url}")
            print(f"{'='*60}")

            await browser.navigate_to(url)
            await asyncio.sleep(2)

            result = await get_llm_repr(browser, full_page=True)

            # Copy generated files to named versions
            if os.path.exists("snapshot_full.yaml"):
                with open("snapshot_full.yaml", "r", encoding="utf-8") as sf:
                    with open(f"snapshot_{name}_full.yaml", "w", encoding="utf-8") as f:
                        f.write(sf.read())

            if os.path.exists("snapshot_enhanced.yaml"):
                with open("snapshot_enhanced.yaml", "r", encoding="utf-8") as se:
                    with open(f"snapshot_{name}_enhanced.yaml", "w", encoding="utf-8") as f:
                        f.write(se.read())

            print(f"Result length: {len(result)} chars")
            print(f"Saved: snapshot_{name}_full.yaml, snapshot_{name}_enhanced.yaml")

        print(f"\n{'='*60}")
        print("All tests completed!")
        print(f"{'='*60}")

        await asyncio.sleep(3)
        await browser.kill()

    asyncio.run(main())