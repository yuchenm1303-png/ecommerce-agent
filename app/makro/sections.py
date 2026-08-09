"""Makro listing section discovery, scanning and persistence primitives.

This module owns the section lifecycle for every Step 3 card:

- discover a card and normalize its title;
- open only that card's EDIT control;
- scan fields inside that card;
- Cancel only that card when the caller explicitly wants to discard edits;
- Save only that card and prove Makro accepted the save by observing collapse
  back to EDIT with no residual validation badge.

It never clicks Send to QC and contains no product/category-specific field list.
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


def _await_section_cards(
    page: Page, *, wait_ms: int = 500, timeout_s: float = 30.0
) -> list[dict[str, Any]]:
    """Poll until at least one Step 3 section card is rendered.

    The section cards and their attribute fields load asynchronously after the
    listing draft / vertical attribute schema is ready. Scanning before that
    would find zero fields, so wait for the first card to appear.
    """
    sections = find_sections(page)
    deadline = time.monotonic() + timeout_s
    while not sections and time.monotonic() < deadline:
        page.wait_for_timeout(int(wait_ms))
        sections = find_sections(page)
    return sections


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
    """Expand every listing section that has EDIT, scan it, then discard scan-only state.

    This function is intentionally read-only from the caller's perspective: a
    section opened only for discovery is Cancelled immediately afterwards. Real
    fill/persist workflows use :func:`save_section` explicitly instead.
    """

    sections = _await_section_cards(page)
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
    """Poll until the section renders fields or shows the Cancel control."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        state = page.evaluate(_SECTION_STATE_SCRIPT, {"path": section_path})
        if state.get("has_fields") or state.get("has_cancel"):
            return True
        page.wait_for_timeout(int(wait_ms))
    return False


def base_section_title(title: str) -> str:
    """Return a stable Makro section identity, ignoring UI-only suffixes."""

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
    """Click only the safe EDIT button of a collapsed listing section."""

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


def visible_section_errors(page: Page, section_path: str) -> list[str]:
    """Return visible Makro validation messages inside one expanded card."""

    card = page.locator(section_path)
    texts: list[str] = []
    selectors = ".form-error, [role='alert'], [class*='FormError'], [class*='error' i]"
    try:
        for text in card.locator(selectors).all_inner_texts():
            clean = re.sub(r"\s+", " ", text).strip()
            if clean and clean not in texts:
                texts.append(clean)
    except Exception:
        pass
    return texts[:30]


def collapsed_error_badges(page: Page, section_title: str) -> list[str]:
    """Read a collapsed-card validation summary such as ``1 Error``."""

    section = find_section(page, section_title)
    if section is None or not section.get("path"):
        return []
    try:
        text = page.locator(str(section["path"])).inner_text(timeout=3_000)
    except Exception:
        return []
    return list(dict.fromkeys(re.findall(r"\b\d+\s+Errors?\b", text, flags=re.I)))


def cancel_section(page: Page, section_title: str, *, wait_ms: int = 450) -> None:
    """Discard the target section's current edits and prove it collapsed."""

    section = find_section(page, section_title)
    if section is None:
        raise RuntimeError(f"Cancel 前找不到 section：{section_title}")
    if section.get("has_edit"):
        return
    path = str(section.get("path") or "")
    if not path:
        raise RuntimeError(f"section {section_title!r} 缺少稳定 DOM path。")
    card = page.locator(path)
    cancel = card.get_by_text("Cancel", exact=True)
    if cancel.count() != 1 or not cancel.first.is_visible():
        raise RuntimeError(
            f"section {section_title!r} 没有唯一可见 Cancel；拒绝猜测其它按钮。"
        )
    cancel.first.scroll_into_view_if_needed()
    cancel.first.click()
    page.wait_for_timeout(wait_ms)
    collapsed = find_section(page, section_title)
    if collapsed is None or not collapsed.get("has_edit"):
        raise RuntimeError(f"section {section_title!r} Cancel 后未恢复折叠态。")


def save_section(page: Page, section_title: str, *, timeout_s: float = 15.0) -> None:
    """Save one Step 3 card and prove Makro accepted the persistence operation.

    Success means all of the following are true:
    1. the expanded card has one unique visible Save button;
    2. there are no already-visible validation errors before clicking Save;
    3. after clicking Save the card collapses back to EDIT within ``timeout_s``;
    4. the collapsed card has no ``N Error(s)`` badge.

    This function never clicks Send to QC.
    """

    section = find_section(page, section_title)
    if section is None:
        raise RuntimeError(f"Save 前找不到 section：{section_title}")
    if section.get("has_edit"):
        raise RuntimeError(f"Save 前 section 已折叠：{section_title}")
    path = str(section.get("path") or "")
    if not path:
        raise RuntimeError(f"Save 前 section 缺少 DOM path：{section_title}")

    inline_errors = visible_section_errors(page, path)
    if inline_errors:
        raise RuntimeError(
            f"{section_title} Save 前仍存在 Makro validation error："
            + " | ".join(inline_errors)
        )

    card = page.locator(path)
    save = card.locator("button").filter(has_text=re.compile(r"^\s*Save\s*$", re.I))
    if save.count() != 1 or not save.first.is_visible():
        raise RuntimeError(f"{section_title} 没有唯一可见 Save 按钮。")
    save.first.scroll_into_view_if_needed()
    save.first.click()

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        page.wait_for_timeout(250)
        live = find_section(page, section_title)
        if live is not None and live.get("has_edit"):
            page.wait_for_timeout(300)
            badges = collapsed_error_badges(page, section_title)
            if badges:
                raise RuntimeError(
                    f"{section_title} 保存后仍有 Makro validation error："
                    + " | ".join(badges)
                )
            return

    errors = visible_section_errors(page, path)
    detail = " | ".join(errors) if errors else "未读取到可见 validation error"
    raise RuntimeError(f"{section_title} 点击 Save 后未恢复 EDIT：{detail}")
