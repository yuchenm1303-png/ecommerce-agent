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

from ..live_schema import schema_field_signature
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

_RESET_SECTION_SCAN_ORIGIN_SCRIPT = r"""
({sectionPath}) => {
  const doc = document.scrollingElement || document.documentElement;
  doc.scrollTop = 0;
  doc.scrollLeft = 0;
  const section = document.querySelector(sectionPath);
  if (!section) return false;
  section.scrollIntoView({block: 'start', inline: 'nearest'});
  const reset = (el) => {
    try {
      if (el.scrollHeight > el.clientHeight + 2) el.scrollTop = 0;
      if (el.scrollWidth > el.clientWidth + 2) el.scrollLeft = 0;
    } catch (_) {}
  };
  reset(section);
  section.querySelectorAll('*').forEach(reset);
  return true;
}
"""

_RESET_ONE_SCROLL_CONTAINER_SCRIPT = r"""
({path}) => {
  const el = document.querySelector(path);
  if (!el) return false;
  if (el.scrollHeight > el.clientHeight + 2) el.scrollTop = 0;
  if (el.scrollWidth > el.clientWidth + 2) el.scrollLeft = 0;
  return true;
}
"""

_SCAN_STABLE_SAMPLES = 2
_SCAN_MAX_PASSES = 4


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


def _section_scan_signature(controls: list[dict[str, Any]]) -> tuple[tuple[object, ...], ...]:
    """Return the exact production schema contract from one canonical pass."""

    return tuple(
        sorted(
            schema_field_signature(field)
            for field in build_semantic_fields(controls)
        )
    )


def _reset_section_scan_origin(page: Page, section_path: str, *, wait_ms: int) -> None:
    """Put the page and nested scrollers at a deterministic start position."""

    if not page.evaluate(
        _RESET_SECTION_SCAN_ORIGIN_SCRIPT,
        {"sectionPath": section_path},
    ):
        raise RuntimeError("Makro section scan lost its target DOM node before canonical reset.")
    page.wait_for_timeout(max(80, min(int(wait_ms), 350)))


def _scan_section_once(
    page: Page,
    section_path: str,
    *,
    include_values: bool,
    wait_ms: int,
    max_scroll_steps: int,
) -> list[dict[str, Any]]:
    """Perform one top-to-bottom pass from a canonical scroll origin."""

    _reset_section_scan_origin(page, section_path, wait_ms=wait_ms)
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
        container_path = str(container.get("path") or "")
        if not container_path.startswith(section_path + " > "):
            continue
        try:
            page.locator(container_path).scroll_into_view_if_needed(timeout=2_000)
        except Exception:
            pass
        if page.evaluate(
            _RESET_ONE_SCROLL_CONTAINER_SCRIPT,
            {"path": container_path},
        ):
            page.wait_for_timeout(max(60, min(int(wait_ms), 250)))
            scans.append(capture_controls(page, include_values=include_values))
        for _ in range(max_scroll_steps):
            state = scroll_container(page, container_path)
            if not state.get("moved"):
                break
            page.wait_for_timeout(wait_ms)
            scans.append(capture_controls(page, include_values=include_values))

    merged = merge_scans(scans)
    prefix = section_path + " > "
    return [item for item in merged if item.get("path", "").startswith(prefix)]


def scan_section_fields(
    page: Page,
    section_path: str,
    *,
    include_values: bool = False,
    wait_ms: int = 350,
    max_scroll_steps: int = 200,
) -> list[dict[str, Any]]:
    """Return a deterministic section schema after consecutive stable full passes.

    Makro mounts some attribute groups asynchronously and can leave the page at
    an arbitrary scroll offset after earlier scans. A single pass can therefore
    miss a suffix of one card and falsely look like schema drift. Every pass now
    starts from the same page/section/container origin and the result is accepted
    only after two consecutive complete passes expose the exact same production
    schema signature, including value options and qualifier/unit options.
    """

    previous_signature: tuple[tuple[object, ...], ...] | None = None
    stable_samples = 0
    observed_counts: list[int] = []
    latest: list[dict[str, Any]] = []

    for _ in range(_SCAN_MAX_PASSES):
        latest = _scan_section_once(
            page,
            section_path,
            include_values=include_values,
            wait_ms=wait_ms,
            max_scroll_steps=max_scroll_steps,
        )
        signature = _section_scan_signature(latest)
        observed_counts.append(len(signature))
        if not signature:
            # The target card was known to contain fields before scanning. An
            # empty pass is therefore a render/path race, never a valid stable
            # contract. Keep trying and ultimately fail closed if it persists.
            previous_signature = None
            stable_samples = 0
            page.wait_for_timeout(max(120, min(int(wait_ms), 400)))
            continue
        if previous_signature is not None and signature == previous_signature:
            stable_samples += 1
            if stable_samples >= _SCAN_STABLE_SAMPLES - 1:
                return latest
        else:
            stable_samples = 0
        previous_signature = signature
        page.wait_for_timeout(max(120, min(int(wait_ms), 400)))

    raise RuntimeError(
        "Makro section live fields did not stabilize across canonical scans; "
        f"semantic_counts={observed_counts}. Refusing to emit a partial schema."
    )


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
        "scan_contract": "canonical-origin + consecutive-stable-pass",
    }
    section_results: list[dict[str, Any]] = []
    flat_scans: list[list[dict[str, Any]]] = []

    for section in sections:
        title = section.get("title", "")
        current_section = find_section(page, title) or section
        section_path = current_section.get("path")
        if not section_path:
            continue

        was_collapsed = bool(current_section.get("has_edit"))
        expanded = not was_collapsed
        if was_collapsed:
            open_section_for_edit(page, current_section)
            stats["sections_expanded_by_scan"] += 1
            expanded = True

        # Expanding a card can make React replace it with a newly rendered
        # node. Reacquire the card by its stable title instead of continuing
        # with the collapsed card's now-stale structural path.
        ready_section = _wait_for_section_fields(
            page, title, wait_ms=wait_ms, timeout_s=10.0
        )
        current_section = ready_section or find_section(page, title) or current_section
        section_path = current_section.get("path")
        if not section_path:
            continue

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
            cancel_section(page, title, wait_ms=wait_ms)
            stats["sections_cancelled"] += 1

    flat_controls = merge_scans(flat_scans)
    return section_results, flat_controls, stats


def _wait_for_section_fields(
    page: Page, section_title: str, *, wait_ms: int, timeout_s: float
) -> dict[str, Any] | None:
    """Return the current section node once its first actual fields have rendered.

    This is only the initial-render gate. Completeness is established later by
    :func:`scan_section_fields`, which requires consecutive stable full scans.
    React may replace the card while expanding it, so every poll resolves the
    card again by its stable title rather than retaining a structural DOM path.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        section = find_section(page, section_title)
        if section and section.get("has_fields"):
            return section
        page.wait_for_timeout(int(wait_ms))
    return None


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
    if section.get("has_edit") and not section.get("has_cancel"):
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


def save_section(page: Page, section_title: str, *, timeout_s: float = 45.0) -> None:
    """Click one Step 3 card's Save and prove Makro accepted persistence.

    Makro persists Step 3 cards asynchronously. A card can remain expanded for
    noticeably longer than the field-write transaction, and React can briefly
    render a stale aggregate ``N Error`` badge while the accepted save is still
    settling. Treat neither transient state as an immediate rejection.

    Success requires two consecutive observations of the collapsed ``EDIT``
    state with no validation badge. Persistent badges or an editor that never
    collapses are reported only after the bounded hard timeout. This function
    never clicks Send to QC.
    """

    section = find_section(page, section_title)
    if section is None:
        raise RuntimeError(f"Save 前找不到 section：{section_title}")
    if section.get("has_edit"):
        raise RuntimeError(f"Save 前 section 已折叠：{section_title}")
    path = str(section.get("path") or "")
    if not path:
        raise RuntimeError(f"Save 前 section 缺少 DOM path：{section_title}")

    card = page.locator(path)
    save = card.locator("button").filter(has_text=re.compile(r"^\s*Save\s*$", re.I))
    if save.count() != 1 or not save.first.is_visible():
        raise RuntimeError(f"{section_title} 没有唯一可见 Save 按钮。")
    save.first.scroll_into_view_if_needed()
    save.first.click()

    deadline = time.monotonic() + timeout_s
    clean_collapsed_samples = 0
    last_badges: list[str] = []
    while time.monotonic() < deadline:
        page.wait_for_timeout(250)
        live = find_section(page, section_title)
        if live is None or not live.get("has_edit"):
            clean_collapsed_samples = 0
            continue

        last_badges = collapsed_error_badges(page, section_title)
        if last_badges:
            # Do not reopen on the first transient badge. Makro/React can expose
            # the previous validation summary for a short period after an
            # accepted asynchronous save. Only a badge that survives the whole
            # bounded settle window is treated as a real rejection.
            clean_collapsed_samples = 0
            continue

        clean_collapsed_samples += 1
        if clean_collapsed_samples >= 2:
            return

    live = find_section(page, section_title)
    if live is not None and live.get("has_edit"):
        badges = collapsed_error_badges(page, section_title)
        if not badges:
            # The hard deadline may land between the first and second clean
            # sample. A final clean collapsed state is still direct evidence
            # that Makro accepted the transaction.
            return

        field_errors: list[str] = []
        try:
            open_section_for_edit(page, live)
            page.wait_for_timeout(400)
            expanded = find_section(page, section_title)
            expanded_path = str((expanded or {}).get("path") or "")
            if expanded_path:
                field_errors = visible_section_errors(page, expanded_path)
        except Exception:
            pass
        detail = (
            "；字段错误：" + " | ".join(field_errors)
            if field_errors
            else ""
        )
        raise RuntimeError(
            f"{section_title} 保存后仍有 Makro validation error："
            + " | ".join(badges or last_badges)
            + detail
        )

    live_path = str((live or {}).get("path") or path)
    errors = visible_section_errors(page, live_path)
    detail = " | ".join(errors) if errors else "未读取到可见 validation error"
    raise RuntimeError(
        f"{section_title} 点击 Save 后 {timeout_s:.0f}s 内未恢复 EDIT：{detail}"
    )