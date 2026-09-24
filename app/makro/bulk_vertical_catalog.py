from __future__ import annotations

import csv
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.sync_api import BrowserContext, Locator, Page


MAKRO_PORTAL_HOME = "https://seller.makro.co.za/index.html#dashboard/home-page"
CATALOG_SCHEMA_VERSION = 1
CATALOG_NAME = "makro-bulk-vertical-catalog.json"
CATALOG_CSV_NAME = "makro-bulk-vertical-catalog.csv"

_UI_PLACEHOLDERS = {
    "select vertical",
    "choose vertical",
    "vertical",
    "select",
    "choose",
    "search",
    "search vertical",
    "no options",
    "no data",
    "loading",
    "loading...",
}


def _clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _key(value: object) -> str:
    return _clean(value).casefold()


def _safe_portal_url(url: str) -> bool:
    try:
        parsed = urlparse(str(url or ""))
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and parsed.hostname == "seller.makro.co.za"


def _candidate_vertical(value: object) -> bool:
    text = _clean(value)
    if len(text) < 2 or len(text) > 140:
        return False
    if _key(text) in _UI_PLACEHOLDERS:
        return False
    return bool(re.search(r"[A-Za-z]", text))


def normalize_vertical_rows(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        label = _clean(row.get("vertical") or row.get("label") or row.get("text"))
        if not _candidate_vertical(label):
            continue
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        portal_value = _clean(row.get("portal_value") or row.get("value"))
        output.append({"vertical": label, "portal_value": portal_value})
    return output


def build_catalog_payload(
    rows: list[dict[str, Any]],
    *,
    source_url: str,
    extraction_mode: str,
    complete: bool,
) -> dict[str, Any]:
    verticals = normalize_vertical_rows(rows)
    underscore_count = sum("_" in item["vertical"] for item in verticals)
    return {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "makro_seller_portal_bulk_product_creation_vertical_dropdown",
        "source_url": str(source_url),
        "extraction_mode": extraction_mode,
        "complete": bool(complete and verticals),
        "stats": {
            "vertical_count": len(verticals),
            "underscore_name_count": underscore_count,
        },
        "verticals": verticals,
        "safety": {
            "dedicated_probe_tab": True,
            "vertical_selected": False,
            "template_downloaded": False,
            "file_uploaded": False,
            "listing_created": False,
            "step3_writes": 0,
            "save_clicked": False,
            "send_to_qc_clicked": False,
            "long_lived_makro_edge_closed": False,
        },
    }


def write_catalog(payload: dict[str, Any], output_dir: str | Path) -> tuple[Path, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / CATALOG_NAME
    csv_path = root / CATALOG_CSV_NAME

    temp = json_path.with_suffix(json_path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(json_path)

    csv_temp = csv_path.with_suffix(csv_path.suffix + ".tmp")
    with csv_temp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["vertical", "portal_value"])
        for item in payload.get("verticals") or []:
            writer.writerow([item.get("vertical", ""), item.get("portal_value", "")])
    csv_temp.replace(csv_path)
    return json_path, csv_path


def _visible_exact_text(page: Page, text: str) -> list[Locator]:
    candidates: list[Locator] = []
    locator = page.get_by_text(text, exact=True)
    for index in range(locator.count()):
        item = locator.nth(index)
        try:
            if item.is_visible():
                candidates.append(item)
        except Exception:
            continue
    return candidates


def _click_unique_text(page: Page, text: str, *, timeout_ms: int = 15_000) -> None:
    deadline = time.monotonic() + max(1.0, timeout_ms / 1000)
    while time.monotonic() < deadline:
        items = _visible_exact_text(page, text)
        if len(items) == 1:
            items[0].click(timeout=timeout_ms)
            return
        if len(items) > 1:
            raise RuntimeError(f"Makro bulk probe found multiple visible exact controls named {text!r}")
        page.wait_for_timeout(250)
    raise RuntimeError(f"Makro bulk probe could not find visible exact control {text!r}")


def _wait_for_text(page: Page, text: str, *, timeout_ms: int = 15_000) -> bool:
    deadline = time.monotonic() + max(1.0, timeout_ms / 1000)
    while time.monotonic() < deadline:
        if _visible_exact_text(page, text):
            return True
        page.wait_for_timeout(250)
    return bool(_visible_exact_text(page, text))


def navigate_to_bulk_create(page: Page, *, timeout_ms: int = 20_000) -> None:
    page.goto(MAKRO_PORTAL_HOME, wait_until="domcontentloaded", timeout=45_000)
    if not _safe_portal_url(page.url):
        raise RuntimeError(f"Makro bulk probe left seller.makro.co.za: {page.url!r}")
    if page.locator('input[type="password"]').count() > 0:
        raise RuntimeError("Makro bulk probe reached a login form; existing authenticated Edge session is required")

    _click_unique_text(page, "Listings", timeout_ms=timeout_ms)
    page.wait_for_timeout(350)
    _click_unique_text(page, "Bulk Product Creation", timeout_ms=timeout_ms)
    if not _safe_portal_url(page.url):
        raise RuntimeError(f"Makro bulk probe left seller.makro.co.za after Bulk Product Creation: {page.url!r}")

    if not _wait_for_text(page, "Create Product", timeout_ms=timeout_ms):
        raise RuntimeError("Makro bulk probe did not reach the Bulk Product Creation surface")
    _click_unique_text(page, "Create Product", timeout_ms=timeout_ms)
    page.wait_for_timeout(600)
    if not _safe_portal_url(page.url):
        raise RuntimeError(f"Makro bulk probe left seller.makro.co.za after Create Product: {page.url!r}")


def _native_vertical_select(page: Page) -> Locator | None:
    selects = page.locator("select")
    matches: list[Locator] = []
    for index in range(selects.count()):
        select = selects.nth(index)
        try:
            if not select.is_visible():
                continue
            context = _clean(select.evaluate("el => (el.parentElement && el.parentElement.innerText) || ''"))
            nameish = " ".join(
                [
                    _clean(select.get_attribute("name")),
                    _clean(select.get_attribute("id")),
                    _clean(select.get_attribute("aria-label")),
                    context[:300],
                ]
            ).casefold()
            if "vertical" in nameish:
                matches.append(select)
        except Exception:
            continue
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise RuntimeError("Makro bulk probe found multiple visible native Vertical selects")
    return None


def _read_native_select(select: Locator) -> list[dict[str, str]]:
    raw = select.evaluate(
        """el => Array.from(el.options || []).map(o => ({label: (o.textContent || '').trim(), value: String(o.value || '')}))"""
    )
    return normalize_vertical_rows(list(raw or []))


def _vertical_combobox(page: Page) -> Locator:
    selectors = [
        '[role="combobox"]',
        'input[placeholder*="vertical" i]',
        'input[aria-label*="vertical" i]',
        '[aria-label*="vertical" i]',
    ]
    matches: list[Locator] = []
    seen_boxes: set[tuple[int, int, int, int]] = set()
    for selector in selectors:
        locator = page.locator(selector)
        for index in range(locator.count()):
            item = locator.nth(index)
            try:
                if not item.is_visible():
                    continue
                box = item.bounding_box()
                if not box:
                    continue
                key = tuple(int(round(box[name])) for name in ("x", "y", "width", "height"))
                if key in seen_boxes:
                    continue
                context = _clean(item.evaluate("el => (el.parentElement && el.parentElement.innerText) || ''"))
                attrs = " ".join(
                    _clean(item.get_attribute(name))
                    for name in ("placeholder", "aria-label", "name", "id")
                )
                if "vertical" not in f"{attrs} {context[:300]}".casefold():
                    continue
                seen_boxes.add(key)
                matches.append(item)
            except Exception:
                continue
    if len(matches) != 1:
        raise RuntimeError(
            "Makro bulk probe expected exactly one visible Vertical dropdown/combobox, "
            f"found {len(matches)}"
        )
    return matches[0]


def _visible_option_rows(page: Page) -> list[dict[str, str]]:
    selectors = (
        '[role="option"]',
        'mat-option',
        '.mat-option',
        '.ng-option',
        '.ant-select-item-option',
        '.p-dropdown-item',
        '.p-autocomplete-item',
    )
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for selector in selectors:
        locator = page.locator(selector)
        for index in range(locator.count()):
            item = locator.nth(index)
            try:
                if not item.is_visible():
                    continue
                label = _clean(item.inner_text())
                if not _candidate_vertical(label) or label.casefold() in seen:
                    continue
                seen.add(label.casefold())
                rows.append(
                    {
                        "vertical": label,
                        "portal_value": _clean(item.get_attribute("value") or item.get_attribute("data-value")),
                    }
                )
            except Exception:
                continue
    return rows


def _scrollable_option_surface(page: Page) -> Locator | None:
    selectors = (
        '[role="listbox"]',
        '.cdk-virtual-scroll-viewport',
        '.ng-dropdown-panel-items',
        '.ant-select-dropdown',
        '.p-dropdown-items-wrapper',
    )
    candidates: list[Locator] = []
    for selector in selectors:
        locator = page.locator(selector)
        for index in range(locator.count()):
            item = locator.nth(index)
            try:
                if item.is_visible():
                    candidates.append(item)
            except Exception:
                continue
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: float((item.bounding_box() or {}).get("height") or 0),
        reverse=True,
    )
    return candidates[0]


def _read_custom_dropdown(page: Page, combo: Locator, *, max_scrolls: int = 500) -> tuple[list[dict[str, str]], bool]:
    combo.click(timeout=15_000)
    page.wait_for_timeout(500)

    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    stable_bottom = 0
    reached_bottom = False

    def absorb() -> int:
        added = 0
        for row in _visible_option_rows(page):
            key = _key(row.get("vertical"))
            if not key or key in seen:
                continue
            seen.add(key)
            rows.append(row)
            added += 1
        return added

    absorb()
    surface = _scrollable_option_surface(page)
    if surface is None:
        # Non-virtual menu: every option is already rendered.
        return normalize_vertical_rows(rows), bool(rows)

    for _ in range(max(1, int(max_scrolls))):
        metrics = surface.evaluate(
            "el => ({top: el.scrollTop, height: el.clientHeight, scrollHeight: el.scrollHeight})"
        )
        top = float((metrics or {}).get("top") or 0)
        height = float((metrics or {}).get("height") or 0)
        scroll_height = float((metrics or {}).get("scrollHeight") or 0)
        at_bottom = scroll_height <= height + 2 or top + height >= scroll_height - 2
        added = absorb()
        if at_bottom:
            stable_bottom = stable_bottom + 1 if added == 0 else 0
            if stable_bottom >= 3:
                reached_bottom = True
                break
        else:
            stable_bottom = 0
        surface.evaluate(
            "el => { el.scrollTop = Math.min(el.scrollHeight, el.scrollTop + Math.max(80, el.clientHeight * 0.8)); el.dispatchEvent(new Event('scroll', {bubbles:true})); }"
        )
        page.wait_for_timeout(160)

    absorb()
    return normalize_vertical_rows(rows), reached_bottom


def extract_bulk_verticals(page: Page) -> tuple[list[dict[str, str]], str, bool]:
    native = _native_vertical_select(page)
    if native is not None:
        rows = _read_native_select(native)
        if not rows:
            raise RuntimeError("Makro Bulk Product Creation native Vertical select is empty")
        return rows, "native_select_all_options", True

    combo = _vertical_combobox(page)
    rows, reached_bottom = _read_custom_dropdown(page, combo)
    if not rows:
        raise RuntimeError("Makro Bulk Product Creation Vertical dropdown exposed no readable options")
    return rows, "custom_dropdown_scroll_to_stable_bottom", reached_bottom


def harvest_bulk_vertical_catalog(
    context: BrowserContext,
    output_dir: str | Path,
    *,
    navigation_timeout_ms: int = 20_000,
) -> dict[str, Any]:
    """Read Makro's Bulk Product Creation Vertical dropdown in a disposable tab.

    The probe may open the dropdown so its options render, but never chooses an
    option, downloads a template, uploads a file, creates a listing, saves, or
    clicks Send to QC. Existing user tabs and the long-lived Edge are not owned.
    """

    page = context.new_page()
    try:
        navigate_to_bulk_create(page, timeout_ms=navigation_timeout_ms)
        rows, extraction_mode, complete = extract_bulk_verticals(page)
        payload = build_catalog_payload(
            rows,
            source_url=page.url,
            extraction_mode=extraction_mode,
            complete=complete,
        )
        json_path, csv_path = write_catalog(payload, output_dir)
        try:
            page.screenshot(
                path=str(Path(output_dir) / "makro-bulk-vertical-catalog.png"),
                full_page=True,
            )
        except Exception:
            pass
        payload["catalog_path"] = str(json_path.resolve())
        payload["csv_path"] = str(csv_path.resolve())
        return payload
    finally:
        try:
            page.close()
        except Exception:
            pass


__all__ = [
    "CATALOG_CSV_NAME",
    "CATALOG_NAME",
    "CATALOG_SCHEMA_VERSION",
    "MAKRO_PORTAL_HOME",
    "build_catalog_payload",
    "extract_bulk_verticals",
    "harvest_bulk_vertical_catalog",
    "navigate_to_bulk_create",
    "normalize_vertical_rows",
    "write_catalog",
]
