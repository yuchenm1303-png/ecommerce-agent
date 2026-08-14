from __future__ import annotations

import inspect

from app.makro import search_surface


def test_search_surface_uses_prequery_dom_ownership() -> None:
    source = inspect.getsource(search_surface)
    assert "__makroQuerySurfaceState" in source
    assert "baseline = new Map()" in source
    assert "!baseline.has(el) || baseline.get(el) !== current" in source


def test_search_surface_never_accepts_underlay_as_topmost() -> None:
    source = inspect.getsource(search_surface)
    assert "el === hit || el.contains(hit)" in source
    assert "hit.contains(el)" not in source


def test_search_surface_has_no_pagewide_unowned_fallback() -> None:
    source = inspect.getsource(search_surface)
    assert "if (!state || !state.baseline) return []" in source
    assert "begin_search_query" in source
    assert "wait_for_search_rows" in source
