"""Makro listing section discovery, safe expand/cancel and section scanning.

Unified safe strategy for every listing section (Price, Stock and Shipping
Information / Product Description / Additional Description / Product Photos):
click EDIT, wait for fields, scan that section alone, then click only the safe
Cancel. Never fills values, never uploads files, never clicks Save / Send to QC.
"""

from __future__ import annotations

import re
import time
from typing import Any

from playwright.sync_api import Page

from .fields import (
    _JS_HELPERS,
    build_semantic_fields,
    capture_controls,
    find_scroll_containers,
    merge_scans,
    scroll_container,
    scroll_window,
)

_FIND_SECTIONS_SCRIPT = (
    "() => {\n"
    + _JS_HELPERS
    + r"""
  const cards = [];
  document.querySelectorAll('[class*="styles__Card-"], [class*="Card-sc-"], [data-testid*="card" i]').forEach((card) => {
    if (!isVisible(card)) return;
    const titleEl = card.querySelector('[class*="styles__Title-"], [class*="Title-ef7o31"], [class*="Title-"]');
    const title = titleEl ? clean(titleEl.innerText || titleEl.textContent) : "";
    if (!title) return;
    const buttons = Array.from(card.querySelectorAll("button"));
    const links = Array.from(card.querySelectorAll("a"));
    const editBtn = buttons.find((b) => clean(b.innerText).toUpperCase() === "EDIT");
    const saveBtn = buttons.find((b) => clean(b.innerText).toUpperCase() === "SAVE");
    const cancelEl = [...links, ...buttons].find((b) => clean(b.innerText).toUpperCase() === "CANCEL");
    const hasFields = card.querySelectorAll('input, textarea, select, [role="combobox"], [contenteditable="true"]').length > 0;
    const imageCount = card.querySelectorAll('img').length;
    cards.push({
      path: pathOf(card),
      title,
      expanded: !editBtn,
      has_edit: Boolean(editBtn),
      has_cancel: Boolean(cancelEl),
      has_save: Boolean(saveBtn),
      has_fields: hasFields,
      image_count: imageCount,
    });
  });
  return cards;
"""
    + "\n}"
)

_CLICK_EDIT_SCRIPT = (
    "({path}) => {\n"
    + _JS_HELPERS
    + r"""
  const card = document.querySelector(path);
  if (!card) return false;
  const btn = Array.from(card.querySelectorAll("button")).find((b) => clean(b.innerText).toUpperCase() === "EDIT");
  if (!btn) return false;
  try {
    btn.scrollIntoView({ block: "center", inline: "center" });
    btn.click();
    return true;
  } catch (err) {
    return false;
  }
"""
    + "\n}"
)

_CLICK_CANCEL_SCRIPT = (
    "({path}) => {\n"
    + _JS_HELPERS
    + r"""
  const card = document.querySelector(path);
  if (!card) return false;
  const el = [...card.querySelectorAll("a, button")].find((b) => clean(b.innerText).toUpperCase() === "CANCEL");
  if (!el) return false;
  try {
    el.scrollIntoView({ block: "center", inline: "center" });
    el.click();
    return true;
  } catch (err) {
    return false;
  }
"""
    + "\n}"
)

_SECTION_STATE_SCRIPT = (
    "({path}) => {\n"
    + _JS_HELPERS
    + r"""
  const card = document.querySelector(path);
  if (!card) return { found: false };
  const buttons = Array.from(card.querySelectorAll("button"));
  const links = Array.from(card.querySelectorAll("a"));
  const editBtn = buttons.find((b) => clean(b.innerText).toUpperCase() === "EDIT");
  const cancelEl = [...links, ...buttons].find((b) => clean(b.innerText).toUpperCase() === "CANCEL");
  const hasFields = card.querySelectorAll(
    'input, textarea, select, [role="combobox"], [contenteditable="true"]'
  ).length > 0;
  return {
    found: true,
    has_edit: Boolean(editBtn),
    has_cancel: Boolean(cancelEl),
    has_fields: hasFields,
  };
"""
    + "\n}"
)

def find_sections(page: Page) -> list[dict[str, Any]]:
    """List all listing section cards with title and expanded state."""
    return page.evaluate(_FIND_SECTIONS_SCRIPT)

def scan_section_fields(
    page: Page,
    section_path: str,
    *,
    include_values: bool = False,
    wait_ms: int = 350,
    max_scroll_steps: int = 200,
) -> list[dict[str, Any]]:
    """Scroll the window plus the section's own containers and scan its fields."""

    scans: list[list[dict[str, Any]]] = [
        capture_controls(page, include_values=include_values)
    ]
    for _ in range(max_scroll_steps):
        state = scroll_window(page)
        if not state.get("moved"):
            break
        page.wait_for_timeout(wait_ms)
        scans.append(capture_controls(page, include_values=include_values))

    for container in find_scroll_containers(page):
        container_path = container.get("path", "")
        if not container_path.startswith(section_path + " > "):
            continue
        for _ in range(max_scroll_steps):
            state = scroll_container(page, container_path)
            if not state.get("moved"):
                break
            page.wait_for_timeout(wait_ms)
            scans.append(capture_controls(page, include_values=include_values))

    merged = merge_scans(scans)
    prefix = section_path + " > "
    return [item for item in merged if item.get("path", "").startswith(prefix)]

def scan_sections(
    page: Page,
    *,
    include_values: bool = False,
    wait_ms: int = 350,
    max_scroll_steps: int = 200,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Expand every listing section that has an EDIT button, scan it, collapse.

    Unified safe strategy for all sections (Price, Stock and Shipping
    Information / Product Description / Additional Description / Product
    Photos): click EDIT, wait for fields to render, scan that section alone,
    then click only the safe Cancel. Never fills values, never uploads files,
    never clicks Save or Send to QC.
    """

    sections = find_sections(page)
    stats: dict[str, Any] = {
        "sections_found": len(sections),
        "sections_expanded_by_scan": 0,
        "sections_cancelled": 0,
    }
    section_results: list[dict[str, Any]] = []
    flat_scans: list[list[dict[str, Any]]] = []

    for section in sections:
        title = section.get("title", "")
        section_path = section.get("path")
        if not section_path:
            continue

        # Re-check the live state: opening one section can collapse another.
        state = page.evaluate(_SECTION_STATE_SCRIPT, {"path": section_path})
        was_collapsed = bool(state.get("has_edit"))
        expanded = not was_collapsed
        if was_collapsed:
            clicked = page.evaluate(_CLICK_EDIT_SCRIPT, {"path": section_path})
            if clicked:
                stats["sections_expanded_by_scan"] += 1
                expanded = True
                _wait_for_section_fields(
                    page, section_path, wait_ms=wait_ms, timeout_s=10.0
                )

        controls = scan_section_fields(
            page,
            section_path,
            include_values=include_values,
            wait_ms=wait_ms,
            max_scroll_steps=max_scroll_steps,
        )
        for item in controls:
            if not item.get("section_heading"):
                item["section_heading"] = title

        semantic_fields = build_semantic_fields(controls)
        section_results.append(
            {
                "title": title,
                "expanded": expanded,
                "image_count": section.get("image_count"),
                "field_count": sum(
                    1 for item in controls if item.get("field_kind") != "option"
                ),
                "semantic_field_count": len(semantic_fields),
                "semantic_fields": semantic_fields,
                "controls": controls,
            }
        )
        flat_scans.append(controls)

        if was_collapsed:
            cancelled = page.evaluate(_CLICK_CANCEL_SCRIPT, {"path": section_path})
            if cancelled:
                stats["sections_cancelled"] += 1
                page.wait_for_timeout(wait_ms)

    flat_controls = merge_scans(flat_scans)
    return section_results, flat_controls, stats

def _wait_for_section_fields(
    page: Page, section_path: str, *, wait_ms: int, timeout_s: float
) -> bool:
    """Poll until the section renders fields or shows the Cancel control.

    Field-less sections (e.g. Product Photos) only show Cancel once the EDIT
    click has taken effect; waiting for either signal avoids a fixed long sleep.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        state = page.evaluate(_SECTION_STATE_SCRIPT, {"path": section_path})
        if state.get("has_fields") or state.get("has_cancel"):
            return True
        page.wait_for_timeout(int(wait_ms))
    return False


def base_section_title(title: str) -> str:
    """Return a stable Makro section identity, ignoring UI-only suffixes.

    Makro appends changing completion counters such as ``(14/14)`` and can also
    append ``(Optional)`` to section labels. Neither suffix is part of the
    semantic section identity used by CLI selection or resolver matching.
    """

    normalized = re.sub(r"\s*\(\d+\s*/\s*\d+\)\s*$", "", title).strip()
    normalized = re.sub(
        r"\s*\(\s*optional\s*\)\s*$", "", normalized, flags=re.IGNORECASE
    ).strip()
    return normalized


def find_section(page: Page, wanted: str) -> dict[str, Any] | None:
    """Return the section card whose normalized title equals ``wanted``."""

    wanted_base = base_section_title(wanted).casefold()
    for section in find_sections(page):
        if base_section_title(str(section.get("title") or "")).casefold() == wanted_base:
            return section
    return None


def open_section_for_edit(page: Page, section: dict[str, Any]) -> None:
    """Click the safe EDIT button of a collapsed listing section.

    Only clicks the section's own EDIT control; never fills values and never
    clicks Save / Send to QC.
    """

    if not section.get("has_edit"):
        return
    path = str(section.get("path") or "")
    if not path:
        raise RuntimeError("section 缺少 DOM path，无法安全打开。")
    card = page.locator(path).first
    button = card.get_by_text("EDIT", exact=True).first
    button.scroll_into_view_if_needed()
    button.click()
    page.wait_for_timeout(500)
