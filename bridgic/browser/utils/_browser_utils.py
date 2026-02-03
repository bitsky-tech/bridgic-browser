from typing import Optional, List
from playwright.async_api import Page

PAGE_SUFFIX = "page_"
PAGE_ID_LENGTH = 8


def generate_page_id(page: Page) -> str:
    """Generate a stable page identifier for a Playwright `Page`.

    Parameters
    ----------
    page : playwright.async_api.Page
        Target Playwright page instance.

    Returns
    -------
    str
        A stable identifier string in the form ``page_<suffix>``.
    """
    page_id_suffix = str(id(page))[-PAGE_ID_LENGTH:]
    return f"{PAGE_SUFFIX}{page_id_suffix}"


def extract_page_id_suffix(page_id: str) -> Optional[str]:
    """Extract the unique suffix part from a page identifier.

    Parameters
    ----------
    page_id : str
        Page identifier string in the form ``page_<suffix>``.

    Returns
    -------
    Optional[str]
        The suffix portion if `page_id` matches the expected prefix;
        otherwise None.
    """
    if page_id.startswith(PAGE_SUFFIX):
        return page_id[len(PAGE_SUFFIX):]
    return None


def find_page_by_id(pages: List[Page], page_id: str) -> Optional[Page]:
    """Find a `Page` instance by its page identifier.

    Parameters
    ----------
    pages : List[playwright.async_api.Page]
        Candidate pages to search.
    page_id : str
        Page identifier string in the form ``page_<suffix>``.

    Returns
    -------
    Optional[playwright.async_api.Page]
        The matching page if found; otherwise None.
    """
    if page_id.startswith(PAGE_SUFFIX):
        page_id_suffix = extract_page_id_suffix(page_id)
        for page in pages:
            current_suffix = str(id(page))[-PAGE_ID_LENGTH:]
            if current_suffix == page_id_suffix:
                return page
    return None

