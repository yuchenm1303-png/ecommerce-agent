from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from playwright.sync_api import Page


MAKRO_SELLER_HOME = "https://seller.makro.co.za/"
_ALLOWED_NAVIGATION_LABELS = ("Listings", "Bulk Product Creation", "Create Product")
_PLACEHOLDERS = {
    "",
    "select",
    "select one",
    "select vertical",
    "choose",
    "choose one",
    "please select",
}


class MakroSchemaHarvestError(RuntimeError):
    """Raised when read-only schema harvesting cannot proceed deterministically."""


@dataclass(slots=True)
class VerticalControl:
    kind: str
    locator: Any


def _text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _safe_slug(value: str) -> str:
    rendered = re.sub(r"[^A-Za-z0-9._-]+", "_", _text(value)).strip("_.")
    return rendered or "vertical"


def _visible(locator: Any) -> list[Any]:
    output: list[Any] = []
    for index in range(locator.count()):
        candidate = locator.nth(index)
        try:
            if candidate.is_visible():
                output.append(candidate)
        except Exception:
            continue
    return output


def _click_exact_role(page: Page, label: str) -> bool:
    matches: list[Any] = []
    for role in ("link", "button"):
        matches.extend(_visible(page.get_by_role(role, name=label, exact=True)))
    if len(matches) > 1:
        raise MakroSchemaHarvestError(
            f"只读导航 {label!r} 同时匹配到 {len(matches)} 个可见 link/button；拒绝猜目标。"
        )
    if len(matches) == 1:
        matches[0].click()
        return True
    return False


def _has_exact_role(page: Page, label: str) -> bool:
    return any(
        _visible(page.get_by_role(role, name=label, exact=True))
        for role in ("link", "button")
    )


def navigate_to_bulk_product_creation(page: Page) -> None:
    """Open Makro's Bulk Product Creation surface using read-only navigation only."""

    page.goto(MAKRO_SELLER_HOME, wait_until="domcontentloaded")
    page.wait_for_timeout(600)

    if not _has_exact_role(page, "Bulk Product Creation"):
        if not _click_exact_role(page, "Listings"):
            raise MakroSchemaHarvestError("Makro Seller Portal 中找不到唯一可见的 Listings 导航。")
        page.wait_for_timeout(350)

    if not _click_exact_role(page, "Bulk Product Creation"):
        raise MakroSchemaHarvestError("Listings 中找不到唯一可见的 Bulk Product Creation。")
    page.wait_for_timeout(600)

    # The official flow requires Create Product before choosing a vertical. If
    # this button is not present the portal may already be on the chooser.
    if _has_exact_role(page, "Create Product"):
        _click_exact_role(page, "Create Product")
        page.wait_for_timeout(450)


def _control_metadata(locator: Any) -> str:
    parts: list[str] = []
    for attribute in ("name", "id", "aria-label", "placeholder", "data-testid"):
        try:
            parts.append(_text(locator.get_attribute(attribute)))
        except Exception:
            continue
    return " ".join(parts).casefold()


def find_vertical_control(page: Page) -> VerticalControl:
    native_candidates: list[tuple[int, Any]] = []
    for locator in _visible(page.locator("select")):
        try:
            option_count = locator.locator("option").count()
        except Exception:
            option_count = 0
        metadata = _control_metadata(locator)
        score = option_count + (1000 if "vertical" in metadata else 0)
        if option_count >= 2:
            native_candidates.append((score, locator))
    if native_candidates:
        native_candidates.sort(key=lambda item: item[0], reverse=True)
        if len(native_candidates) > 1 and native_candidates[0][0] == native_candidates[1][0]:
            raise MakroSchemaHarvestError("页面存在多个同等可信的 native Vertical select；拒绝猜目标。")
        return VerticalControl("native_select", native_candidates[0][1])

    combo_candidates: list[tuple[int, Any]] = []
    for locator in _visible(page.get_by_role("combobox")):
        metadata = _control_metadata(locator)
        score = 1000 if "vertical" in metadata else 0
        combo_candidates.append((score, locator))
    if combo_candidates:
        combo_candidates.sort(key=lambda item: item[0], reverse=True)
        if len(combo_candidates) > 1 and combo_candidates[0][0] == combo_candidates[1][0]:
            raise MakroSchemaHarvestError("页面存在多个同等可信的 combobox；无法唯一确定 Vertical 控件。")
        return VerticalControl("combobox", combo_candidates[0][1])

    raise MakroSchemaHarvestError("Bulk Product Creation 页面没有找到可唯一识别的 Vertical select/combobox。")


def _clean_verticals(values: Iterable[object]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = _text(raw)
        key = value.casefold()
        if not value or key in _PLACEHOLDERS or key in seen:
            continue
        seen.add(key)
        output.append(value)
    return output


def _listbox(page: Page) -> Any | None:
    boxes = _visible(page.get_by_role("listbox"))
    if len(boxes) > 1:
        raise MakroSchemaHarvestError("Vertical combobox 展开后出现多个可见 listbox；拒绝猜目标。")
    return boxes[0] if boxes else None


def _collect_visible_options(page: Page) -> list[str]:
    options = _clean_verticals(page.get_by_role("option").all_text_contents())
    if options:
        return options
    for selector in ("[role='option']", "li", ".MuiAutocomplete-option", ".ant-select-item-option"):
        values = []
        for locator in _visible(page.locator(selector)):
            try:
                values.append(locator.inner_text())
            except Exception:
                continue
        cleaned = _clean_verticals(values)
        if cleaned:
            return cleaned
    return []


def _collect_virtualized_options(page: Page, *, max_rounds: int = 250) -> list[str]:
    """Collect custom-dropdown options, including virtualized lists, without selection."""

    collected: list[str] = []
    seen: set[str] = set()
    stale_rounds = 0
    box = _listbox(page)
    for _ in range(max_rounds):
        added = 0
        for value in _collect_visible_options(page):
            key = value.casefold()
            if key not in seen:
                seen.add(key)
                collected.append(value)
                added += 1
        stale_rounds = 0 if added else stale_rounds + 1
        if box is None:
            break
        state = box.evaluate(
            "el => ({top: el.scrollTop, height: el.clientHeight, total: el.scrollHeight})"
        )
        top = float(state.get("top") or 0)
        height = float(state.get("height") or 0)
        total = float(state.get("total") or 0)
        if top + height >= total - 2:
            break
        box.evaluate("el => { el.scrollTop = Math.min(el.scrollHeight, el.scrollTop + Math.max(1, el.clientHeight * 0.8)); }")
        page.wait_for_timeout(60)
        if stale_rounds >= 8:
            break
    return collected


def list_verticals(page: Page, control: VerticalControl) -> list[str]:
    if control.kind == "native_select":
        return _clean_verticals(control.locator.locator("option").all_text_contents())
    control.locator.click()
    page.wait_for_timeout(150)
    values = _collect_virtualized_options(page)
    try:
        control.locator.press("Escape")
    except Exception:
        pass
    if not values:
        raise MakroSchemaHarvestError("Vertical combobox 已展开，但没有读取到任何 option。")
    return values


def _select_vertical(page: Page, control: VerticalControl, vertical: str) -> None:
    if control.kind == "native_select":
        try:
            control.locator.select_option(label=vertical)
        except Exception:
            control.locator.select_option(value=vertical)
        page.wait_for_timeout(250)
        return

    control.locator.click()
    page.wait_for_timeout(100)
    try:
        control.locator.fill(vertical)
        page.wait_for_timeout(150)
    except Exception:
        pass
    exact = _visible(page.get_by_role("option", name=vertical, exact=True))
    if len(exact) != 1:
        exact = _visible(page.get_by_text(vertical, exact=True))
    if len(exact) != 1:
        raise MakroSchemaHarvestError(
            f"Vertical {vertical!r} 无法唯一匹配当前 dropdown option；matches={len(exact)}。"
        )
    exact[0].click()
    page.wait_for_timeout(300)


def _download_control(page: Page) -> Any:
    matches: list[Any] = []
    for role in ("button", "link"):
        matches.extend(_visible(page.get_by_role(role, name="Download", exact=True)))
    enabled: list[Any] = []
    for locator in matches:
        try:
            if locator.is_enabled():
                enabled.append(locator)
        except Exception:
            enabled.append(locator)
    if len(enabled) != 1:
        raise MakroSchemaHarvestError(
            f"当前 Vertical 的 Download 控件不是唯一可见且可用目标；matches={len(enabled)}。"
        )
    return enabled[0]


def download_vertical_loadsheet(
    page: Page,
    control: VerticalControl,
    vertical: str,
    destination_root: str | Path,
    *,
    timeout_ms: int = 30_000,
) -> Path:
    _select_vertical(page, control, vertical)
    download_button = _download_control(page)
    with page.expect_download(timeout=timeout_ms) as info:
        download_button.click()
    download = info.value
    target_dir = Path(destination_root) / _safe_slug(vertical)
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = _text(download.suggested_filename) or f"{_safe_slug(vertical)}.xlsx"
    target = target_dir / filename
    download.save_as(str(target))
    return target


def write_harvest_diagnostics(page: Page, output_dir: str | Path, *, prefix: str = "bulk-schema") -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    screenshot = root / f"{prefix}-{stamp}.png"
    html = root / f"{prefix}-{stamp}.html"
    page.screenshot(path=str(screenshot), full_page=True)
    html.write_text(page.content(), encoding="utf-8")
    return {"screenshot": str(screenshot.resolve()), "html": str(html.resolve())}


def harvest_vertical_loadsheets(
    page: Page,
    destination_root: str | Path,
    *,
    discover_only: bool = False,
    requested_verticals: Iterable[str] = (),
    limit: int = 0,
    timeout_ms: int = 30_000,
) -> dict[str, Any]:
    navigate_to_bulk_product_creation(page)
    control = find_vertical_control(page)
    discovered = list_verticals(page, control)
    if not discovered:
        raise MakroSchemaHarvestError("Makro 返回的 Vertical 列表为空。")

    requested = {_text(value).casefold(): _text(value) for value in requested_verticals if _text(value)}
    selected = discovered
    if requested:
        selected = [value for value in discovered if value.casefold() in requested]
        missing = sorted(set(requested) - {value.casefold() for value in selected})
        if missing:
            raise MakroSchemaHarvestError(
                "请求的 Vertical 不在当前 Seller Portal 列表中：" + " | ".join(requested[key] for key in missing)
            )
    if limit > 0:
        selected = selected[:limit]

    report: dict[str, Any] = {
        "read_only": True,
        "discovered_vertical_count": len(discovered),
        "discovered_verticals": discovered,
        "selected_vertical_count": len(selected),
        "downloads": [],
        "failures": [],
    }
    if discover_only:
        return report

    for vertical in selected:
        try:
            target = download_vertical_loadsheet(
                page,
                control,
                vertical,
                destination_root,
                timeout_ms=timeout_ms,
            )
            report["downloads"].append({"vertical": vertical, "file": str(target.resolve())})
        except Exception as exc:
            report["failures"].append({"vertical": vertical, "error": str(exc)})
    return report


def write_harvest_report(report: dict[str, Any], path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return target
