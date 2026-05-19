"""
Regression test for the IMPLICIT_ROLE_SELECTORS single-source invariant.

Prior to the refactor that introduced ``_IMPLICIT_ROLE_SELECTORS`` at module
scope, two copies of the role→selector mapping were hand-maintained inside
``_BUILD_ROLE_INDEX_JS`` and ``_BATCH_INFO_JS``. Every addition had to be
mirrored by hand, and any drift silently produced snapshots with a role
recognised in one pass but not the other. The test here locks the new
invariant: both JS strings reference exactly the same JSON-serialised
mapping, and it matches the Python-side constant.
"""
import json

from bridgic.browser.session import _snapshot as snap


def test_implicit_role_selectors_embedded_in_build_role_index() -> None:
    """_BUILD_ROLE_INDEX_JS must contain the JSON-serialised selector map."""
    assert snap._IMPLICIT_ROLE_SELECTORS_JS in snap._BUILD_ROLE_INDEX_JS


def test_implicit_role_selectors_embedded_in_batch_info() -> None:
    """_BATCH_INFO_JS must contain the JSON-serialised selector map."""
    assert snap._IMPLICIT_ROLE_SELECTORS_JS in snap._BATCH_INFO_JS


def test_implicit_role_selectors_json_matches_python_dict() -> None:
    """The injected JSON must round-trip back to the Python dict."""
    reparsed = json.loads(snap._IMPLICIT_ROLE_SELECTORS_JS)
    assert reparsed == snap._IMPLICIT_ROLE_SELECTORS


def test_placeholder_token_fully_substituted() -> None:
    """No ``__IMPLICIT_ROLE_SELECTORS__`` placeholder should remain in the JS."""
    assert "__IMPLICIT_ROLE_SELECTORS__" not in snap._BUILD_ROLE_INDEX_JS
    assert "__IMPLICIT_ROLE_SELECTORS__" not in snap._BATCH_INFO_JS
