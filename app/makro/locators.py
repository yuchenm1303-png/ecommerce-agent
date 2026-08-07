"""Field locator strategy for Makro multi-value controls.

Prefer selectors that remain unique for Makro multi-value controls (stable
``name`` first, then the captured DOM path, then selector candidates).
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
