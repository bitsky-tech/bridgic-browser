from __future__ import annotations

from bridgic.browser.tools._browser_tool_spec import BrowserToolSpec


class DummyBrowser:
    pass


class NamedBrowser:
    name = "named-browser"


async def sample_tool(browser, query: str) -> str:
    return query


def test_browser_tool_spec_dump_uses_class_name_when_name_missing():
    browser = DummyBrowser()
    spec = BrowserToolSpec.from_raw(sample_tool, browser)

    dumped = spec.dump_to_dict()

    assert dumped["browser_name"] == "DummyBrowser"
    assert dumped["browser_id"] == str(id(browser))


def test_browser_tool_spec_dump_prefers_explicit_browser_name():
    browser = NamedBrowser()
    spec = BrowserToolSpec.from_raw(sample_tool, browser)

    dumped = spec.dump_to_dict()

    assert dumped["browser_name"] == "named-browser"
    assert dumped["browser_id"] == str(id(browser))
