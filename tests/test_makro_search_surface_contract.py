from __future__ import annotations

import inspect

from app.makro import search_surface


def test_search_surface_uses_generation_scoped_dom_ownership() -> None:
    source = inspect.getsource(search_surface)
    assert "__makroQuerySurfaceState" in source
    assert "baseline = new Map()" in source
    assert "generation = Number(previous && previous.generation || 0) + 1" in source
    assert "new MutationObserver" in source
    assert "const touched = new WeakSet()" in source
    assert "!baseline.has(el) || baseline.get(el) !== current" in source
    assert "const fresh = (el) => changed(el) || !!(touched && touched.has(el));" in source


def test_discovery_requires_current_generation_freshness_even_for_aria_rows() -> None:
    read_script = search_surface._READ_ROWS_JS
    assert "if (!fresh(el)) continue;" in read_script
    assert "Explicit ownership says where a row lives" in read_script
    assert "if (!changed(el) && !explicitOwned(el)) continue;" not in read_script


def test_stable_exact_replay_is_explicit_opt_in_only() -> None:
    source = inspect.getsource(search_surface)
    click_script = search_surface._CLICK_ROW_JS
    assert "allow_stable_exact" in source
    assert "allowStableExact" in click_script
    assert "const eligible = (el) => fresh(el) || allowStableExact;" in click_script
    assert "if (!fresh(row) && !allowStableExact) continue;" in click_script


def test_search_surface_never_accepts_underlay_as_topmost() -> None:
    source = inspect.getsource(search_surface)
    assert "el === hit || el.contains(hit)" in source
    assert "hit.contains(el)" not in source


def test_search_surface_has_no_pagewide_unowned_discovery_fallback() -> None:
    source = inspect.getsource(search_surface)
    assert "if (!state || !state.baseline) return []" in source
    assert "begin_search_query" in source
    assert "wait_for_search_rows" in source
