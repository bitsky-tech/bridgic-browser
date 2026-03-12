from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..session._browser import Browser  # pragma: no cover

logger = logging.getLogger(__name__)

MAX_CHAR_LIMIT = int(os.environ.get("BRIDGIC_MAX_CHARS", "30000"))

async def get_llm_repr(browser: "Browser",
    start_from_char: int = 0,
    interactive: bool = False,
    full_page: bool = True,
) -> str:
    """Get page accessibility tree with element refs for interaction.

    **Call this first** to get refs (e.g., e1, e2) before using action tools.

    Parameters
    ----------
    browser : Browser
        Browser instance.
    start_from_char : int, optional
        Pagination offset. Use `next_start_char` from truncation notice.
    interactive : bool, optional
        If True, only return clickable/editable elements (buttons, links,
        inputs, checkboxes, elements with cursor:pointer, etc.).
    full_page : bool, optional
        If True (default), include elements outside viewport.

    Returns
    -------
    str
        Tree with refs like: `- button "Submit" [ref=e1]`
        Use refs with click_element_by_ref, input_text_by_ref, etc.
    """
    try:
        snapshot = await browser.get_snapshot(
            interactive=interactive,
            full_page=full_page,
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

            # Prefer a paragraph break (\n\n) as the cut point — separates major sections.
            paragraph_break = text.rfind("\n\n", MAX_CHAR_LIMIT - 500, MAX_CHAR_LIMIT)
            if paragraph_break > 0:
                truncate_at = paragraph_break
            else:
                # Fall back to any line boundary (\n). Accessibility tree elements are
                # separated by single newlines, so this always produces a complete line.
                # Never use '.' as a fallback: URLs and element names contain dots and
                # would cause mid-line truncation.
                line_break = text.rfind("\n", 0, MAX_CHAR_LIMIT)
                if line_break > 0:
                    truncate_at = line_break + 1  # include the trailing \n

            text = text[:truncate_at]
            truncated = True
            next_start_char = start_from_char + truncate_at

        # If truncation occurred, add a notice at the end to help caller continue pagination
        if truncated and next_start_char is not None:
            cli_flags = []
            if interactive:
                cli_flags.append("-i")
            if not full_page:
                cli_flags.append("-F")
            cli_flags.append(f"-s {next_start_char}")
            cli_cmd = "bridgic-browser snapshot " + " ".join(cli_flags)

            notice = (
                "\n\n[notice] Current page state text is too long, returned portion starting "
                f"from character {start_from_char} (this segment length {len(text)} / total "
                f"length {total_length} characters). To continue getting subsequent content: "
                f"call get_llm_repr(browser, start_from_char={next_start_char}, "
                f"interactive={interactive}, full_page={full_page}) "
                f"or run: {cli_cmd}"
            )
            text = f"{text}{notice}"

        logger.info("[get_llm_repr] Successfully retrieved interface information")
        return text
    except Exception as e:
        error_msg = f"Failed to get interface information: {e}"
        logger.error(f"[get_llm_repr] {error_msg}")
        return error_msg


# if __name__ == "__main__":
#     """Manual test runner for get_llm_repr. Writes output to temp dir, not project root."""
#     import asyncio
#     import tempfile
#     from pathlib import Path

#     async def main():
#         from bridgic.browser.session import Browser

#         test_urls = [
#             ("test_page", "http://192.168.0.5:8081/test_page.html"),
#         ]

#         out_dir = Path(tempfile.gettempdir()) / "bridgic_get_llm_repr_out"
#         out_dir.mkdir(parents=True, exist_ok=True)
#         print(f"Writing snapshot YAML to: {out_dir}")

#         browser = Browser(
#             headless=True,
#             viewport={"width": 1440, "height": 900},
#         )
#         await browser.start()
#         try:
#             for name, url in test_urls:
#                 print(f"\n{'='*60}\nTesting: {name}\nURL: {url}\n{'='*60}")
#                 await browser.navigate_to(url)
#                 await asyncio.sleep(2)
#                 result = await get_llm_repr(browser, interactive=False, full_page=False)
#                 out_path = out_dir / f"{name}.yaml"
#                 out_path.write_text(result, encoding="utf-8")
#                 print(f"Wrote: {out_path}")
#         finally:
#             await browser.kill()

#     asyncio.run(main())
