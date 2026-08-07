"""Field locator strategy for Makro multi-value controls.

Prefer selectors that remain unique for Makro multi-value controls (stable
``name`` first, then the captured DOM path, then selector candidates).

Fill/readback must scope the selector to the owning listing section card so a
duplicate or stale React instance elsewhere in the document can never be
written/read instead of the visible control.
"""

from __future__ import annotations

from typing import Any


def _css_attr(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def selector_for_control(control: dict[str, Any]) -> str:
    """Return the deterministic Playwright selector for one Makro control."""

    name = str(control.get("name") or "")
    if name:
        return f'[name="{_css_attr(name)}"]'
    path = str(control.get("path") or "")
    if path:
        return path
    candidates = control.get("selector_candidates") or []
    if candidates:
        return str(candidates[0])
    raise ValueError("控件没有可用 selector。")


def scoped_selector_for_control(
    section_path: str | None, control: dict[str, Any]
) -> str:
    """Scope a control selector to its listing section card.

    ``section_path`` is the DOM path of the section card captured by
    ``find_sections`` (e.g. ``body > div#app-container > ... > div``). Playwright
    ``>>`` chaining restricts the control match to inside that card, so a
    same-named hidden/duplicate React instance outside the section can never be
    hit. When no section path is known the plain selector is returned.
    """

    inner = selector_for_control(control)
    if not section_path:
        return inner
    return f"{section_path} >> {inner}"
