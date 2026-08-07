"""Safe synthetic interaction coverage for Makro listing controls.

This module answers one question only: can the browser execution layer operate
an empty Makro field reliably? It does not resolve product facts and never
persists changes. Each field is tested in its own open -> exercise -> Cancel
transaction so every attempt starts from a clean, unsaved section state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from playwright.sync_api import Page

from .locators import scoped_selector_for_control

PASS = "pass"
FAIL = "fail"
UNSUPPORTED = "unsupported"
SKIPPED_EXISTING = "skipped_existing"
NOT_FOUND = "not_found"

_PLACEHOLDERS = {
    "",
    "select",
    "select one",
    "choose",
    "choose one",
    "please select",
    "please select one",
    "-- select --",
    "- select -",
}


@dataclass(slots=True)
class CoverageResult:
    section: str
    subsection: str
    attribute_key: str
    label: str
    shape: str
    status: str
    candidate: list[str] = field(default_factory=list)
    immediate: list[str] = field(default_factory=list)
    settled: list[str] = field(default_factory=list)
    selectors: list[str] = field(default_factory=list)
    plus_available: bool = False
    plus_status: str = "not_available"
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "section": self.section,
            "subsection": self.subsection,
            "attribute_key": self.attribute_key,
            "label": self.label,
            "shape": self.shape,
            "status": self.status,
            "candidate": self.candidate,
            "immediate": self.immediate,
            "settled": self.settled,
            "selectors": self.selectors,
            "plus_available": self.plus_available,
            "plus_status": self.plus_status,
            "detail": self.detail,
        }


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _is_placeholder(value: Any) -> bool:
    return _norm(value) in _PLACEHOLDERS


def _value_controls(semantic_field: dict[str, Any]) -> list[dict[str, Any]]:
    controls: list[dict[str, Any]] = []
    for control in semantic_field.get("controls") or []:
        name = str(control.get("name") or "")
        if name.endswith("_qualifier"):
            continue
        if control.get("field_kind") == "option":
            continue
        controls.append(control)

    def order(control: dict[str, Any]) -> tuple[int, str]:
        name = str(control.get("name") or "")
        match = re.search(r"_(\d+)_value$", name)
        return (int(match.group(1)) if match else 0, name)

    return sorted(controls, key=order)


def _qualifier_controls(semantic_field: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        control
        for control in semantic_field.get("controls") or []
        if str(control.get("name") or "").endswith("_qualifier")
    ]


def _selected_option_value(control: dict[str, Any]) -> str:
    for option in control.get("options") or []:
        if option.get("selected"):
            return str(option.get("text") or option.get("value") or "")
    return ""


def control_is_empty(control: dict[str, Any]) -> bool:
    """True when the captured live control is empty/default-placeholder."""

    if control.get("disabled") or control.get("readonly"):
        return False

    kind = str(control.get("field_kind") or "")
    if kind in {"checkbox", "custom_checkbox", "radio", "custom_radio"}:
        return True

    captured = str(control.get("value") or "") if control.get("value_recorded") else ""
    if not captured and kind in {"select", "dropdown", "autocomplete", "listbox"}:
        captured = _selected_option_value(control)

    return not captured or _is_placeholder(captured)


def semantic_field_is_empty(semantic_field: dict[str, Any]) -> bool:
    controls = _value_controls(semantic_field)
    return bool(controls) and all(control_is_empty(control) for control in controls)


def _eligible_options(control: dict[str, Any]) -> list[str]:
    options: list[str] = []
    for option in control.get("options") or []:
        if option.get("disabled"):
            continue
        text = str(option.get("text") or option.get("value") or "").strip()
        if not text or _is_placeholder(text):
            continue
        options.append(text)
    return options


def choose_option(control: dict[str, Any], *, avoid: str | None = None) -> str | None:
    """Choose one real Makro option, never a placeholder."""

    options = _eligible_options(control)
    if avoid:
        avoid_norm = _norm(avoid)
        for option in options:
            if _norm(option) != avoid_norm:
                return option
    return options[0] if options else None


def _static_unit_suffix(control: dict[str, Any]) -> str:
    """Infer a short, non-interactive suffix such as ``K`` from field context."""

    label = str(control.get("label") or "").strip()
    context = str(control.get("context_text") or "").strip()
    if not label or not context:
        return ""
    if not context.casefold().startswith(label.casefold()):
        return ""
    tail = context[len(label) :].strip()
    if not tail or tail == "+" or len(tail) > 12:
        return ""
    if "select" in tail.casefold():
        return ""
    if re.fullmatch(r"[A-Za-z0-9°%µμ/.\- ]{1,12}", tail):
        return tail
    return ""


def field_shape(semantic_field: dict[str, Any]) -> str:
    controls = _value_controls(semantic_field)
    if not controls:
        return "no-value-control"
    primary = controls[0]
    kind = str(primary.get("field_kind") or "unknown")
    qualifiers = _qualifier_controls(semantic_field)
    suffix = _static_unit_suffix(primary)

    if kind == "select":
        base = "native-select"
    elif kind in {"dropdown", "autocomplete", "listbox"}:
        base = "custom-dropdown"
    elif kind in {"checkbox", "custom_checkbox"}:
        base = "checkbox"
    elif kind in {"radio", "custom_radio"}:
        base = "radio"
    elif kind in {"custom_spinbutton", "custom_slider"} or str(primary.get("type") or "") == "number":
        base = "numeric"
    elif kind in {"textarea", "contenteditable"}:
        base = "long-text"
    else:
        base = "text"

    if qualifiers:
        base += "+qualifier"
    elif suffix:
        base += "+static-unit"
    return base


def _synthetic_text(control: dict[str, Any], ordinal: int, *, numeric: bool) -> str:
    if numeric:
        return str((ordinal % 8) + 1)

    ctype = str(control.get("type") or "").casefold()
    if ctype == "email":
        value = "coverage@example.com"
    elif ctype == "url":
        value = "https://example.com"
    elif ctype == "tel":
        value = "0123456789"
    else:
        value = f"COVERAGE_{ordinal:03d}"

    maxlength = control.get("maxlength")
    if isinstance(maxlength, int) and maxlength > 0:
        value = value[:maxlength]
    return value or "1"


def _unique_visible_locator(page: Page, section_path: str, control: dict[str, Any]) -> tuple[Any, str]:
    selector = scoped_selector_for_control(section_path, control)
    matches = page.locator(selector)
    count = matches.count()
    if count != 1:
        raise RuntimeError(f"{selector} 匹配 {count} 个控件，必须恰好为 1。")
    locator = matches.first
    if not locator.is_visible():
        raise RuntimeError(f"{selector} 当前不可见。")
    locator.scroll_into_view_if_needed()
    return locator, selector


def _read_control(locator: Any, control: dict[str, Any]) -> str:
    kind = str(control.get("field_kind") or "input")
    if kind == "select":
        return locator.locator("option:checked").inner_text(timeout=3_000).strip()
    if kind in {"checkbox", "custom_checkbox", "radio", "custom_radio"}:
        try:
            return "true" if locator.is_checked() else "false"
        except Exception:
            return _norm(locator.get_attribute("aria-checked") or "false")
    if kind == "contenteditable":
        return locator.inner_text(timeout=3_000).strip()
    if kind in {"dropdown", "autocomplete", "listbox"}:
        value = locator.get_attribute("value")
        if value:
            return value.strip()
        try:
            value = locator.input_value(timeout=3_000)
            if value:
                return value.strip()
        except Exception:
            pass
        return locator.inner_text(timeout=3_000).strip()
    return locator.input_value(timeout=3_000).strip()


def _visible_custom_options(page: Page) -> list[str]:
    return page.evaluate(
        """() => {
          const clean = (v) => String(v || '').replace(/\\s+/g, ' ').trim();
          const visible = (el) => {
            const s = getComputedStyle(el);
            const r = el.getBoundingClientRect();
            return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
          };
          const out = [];
          document.querySelectorAll("[role='option'], li[data-value], li[data-testid*='option' i], [class*='option' i]")
            .forEach((el) => {
              if (!visible(el)) return;
              const text = clean(el.innerText || el.textContent);
              if (text && !out.includes(text)) out.push(text);
            });
          return out;
        }"""
    )


def _choose_custom_option(page: Page, locator: Any) -> str | None:
    locator.click()
    page.wait_for_timeout(180)
    options = [item for item in _visible_custom_options(page) if not _is_placeholder(item)]
    page.keyboard.press("Escape")
    page.wait_for_timeout(80)
    return options[0] if options else None


def _write_control(page: Page, locator: Any, control: dict[str, Any], candidate: str) -> None:
    kind = str(control.get("field_kind") or "input")
    if kind in {
        "input", "textarea", "contenteditable", "custom_textbox",
        "custom_searchbox", "custom_spinbutton",
    }:
        locator.fill(candidate)
        return
    if kind == "select":
        locator.select_option(label=candidate)
        return
    if kind in {"dropdown", "autocomplete", "listbox"}:
        locator.click()
        page.get_by_text(candidate, exact=True).last.click()
        return
    if kind in {"checkbox", "custom_checkbox"}:
        desired = _norm(candidate) == "true"
        try:
            locator.set_checked(desired)
        except Exception:
            current = _norm(locator.get_attribute("aria-checked") or "false") == "true"
            if current != desired:
                locator.click()
        return
    if kind in {"radio", "custom_radio"}:
        try:
            locator.check()
        except Exception:
            locator.click()
        return
    raise ValueError(f"暂不支持控件类型：{kind}")


def _candidate_for_control(
    page: Page,
    locator: Any,
    control: dict[str, Any],
    ordinal: int,
    *,
    numeric: bool,
    avoid_option: str | None = None,
) -> str | None:
    kind = str(control.get("field_kind") or "input")
    if kind == "select":
        return choose_option(control, avoid=avoid_option)
    if kind in {"dropdown", "autocomplete", "listbox"}:
        candidate = choose_option(control, avoid=avoid_option)
        return candidate or _choose_custom_option(page, locator)
    if kind in {"checkbox", "custom_checkbox"}:
        try:
            current = locator.is_checked()
        except Exception:
            current = _norm(locator.get_attribute("aria-checked") or "false") == "true"
        return "false" if current else "true"
    if kind in {"radio", "custom_radio"}:
        return "true"
    if kind == "custom_slider":
        return None
    return _synthetic_text(control, ordinal, numeric=numeric)


def _equivalent(expected: str, actual: str, control: dict[str, Any]) -> bool:
    kind = str(control.get("field_kind") or "")
    if kind in {"dropdown", "autocomplete", "listbox"}:
        return _norm(expected) == _norm(actual) or _norm(expected) in _norm(actual)
    return _norm(expected) == _norm(actual)


def _exercise_one_control(
    page: Page,
    section_path: str,
    control: dict[str, Any],
    ordinal: int,
    *,
    numeric: bool,
    recheck_wait_ms: int,
    avoid_option: str | None = None,
) -> tuple[bool, str | None, str, str, str, str]:
    locator, selector = _unique_visible_locator(page, section_path, control)
    candidate = _candidate_for_control(
        page, locator, control, ordinal, numeric=numeric, avoid_option=avoid_option
    )
    if candidate is None:
        return False, None, "", "", selector, "没有可安全生成的测试值/真实选项。"

    _write_control(page, locator, control, candidate)
    immediate = _read_control(locator, control)
    page.wait_for_timeout(recheck_wait_ms)
    locator2, _ = _unique_visible_locator(page, section_path, control)
    settled = _read_control(locator2, control)

    passed = _equivalent(candidate, immediate, control) and _equivalent(candidate, settled, control)
    detail = (
        "立即回读和 React 渲染周期后的二次回读均一致。"
        if passed
        else f"回读不一致：immediate={immediate!r}, settled={settled!r}"
    )
    return passed, candidate, immediate, settled, selector, detail


def _click_add_value_if_present(locator: Any) -> dict[str, Any]:
    return locator.evaluate(
        """el => {
          const wrapper = el.closest('[class*="EditAttributeItemWrapper"]');
          if (!wrapper) return {available:false, clicked:false, reason:'no-wrapper'};
          const candidates = [...wrapper.querySelectorAll('button, a')].filter((node) => {
            const text = String(node.innerText || node.textContent || '').trim();
            const aria = String(node.getAttribute('aria-label') || '').trim().toLowerCase();
            const title = String(node.getAttribute('title') || '').trim().toLowerCase();
            return text === '+' || aria === 'add' || aria.includes('add value') || title.includes('add value');
          });
          const button = candidates.find((node) => {
            const s = getComputedStyle(node);
            const r = node.getBoundingClientRect();
            return !node.disabled && node.getAttribute('aria-disabled') !== 'true'
              && s.display !== 'none' && s.visibility !== 'hidden'
              && r.width > 0 && r.height > 0;
          });
          if (!button) return {available:false, clicked:false, reason:'no-visible-add'};
          button.click();
          return {available:true, clicked:true, reason:''};
        }"""
    )


def _match_field(fields: list[dict[str, Any]], attribute_key: str, label: str) -> dict[str, Any] | None:
    for field in fields:
        if str(field.get("attribute_key") or "") != attribute_key:
            continue
        if label and str(field.get("label") or "") != label:
            continue
        return field
    return None


def exercise_live_field(
    adapter: Any,
    semantic_field: dict[str, Any],
    section_path: str,
    ordinal: int,
    *,
    recheck_wait_ms: int = 800,
    exercise_multi_value: bool = True,
    wait_ms: int = 250,
    max_scroll_steps: int = 200,
) -> CoverageResult:
    page = adapter.page
    label = str(semantic_field.get("label") or "")
    key = str(semantic_field.get("attribute_key") or "")
    section = str(semantic_field.get("section_heading") or "")
    subsection = str(semantic_field.get("subsection_heading") or "")
    shape = field_shape(semantic_field)
    result = CoverageResult(section, subsection, key, label, shape, FAIL)

    controls = _value_controls(semantic_field)
    if not controls:
        result.status = UNSUPPORTED
        result.detail = "semantic field 没有可操作的 value control。"
        return result

    primary = controls[0]
    qualifiers = _qualifier_controls(semantic_field)
    numeric = (
        bool(qualifiers)
        or bool(_static_unit_suffix(primary))
        or str(primary.get("type") or "").casefold() == "number"
        or str(primary.get("inputmode") or "").casefold() in {"numeric", "decimal"}
        or str(primary.get("field_kind") or "") in {"custom_spinbutton", "custom_slider"}
    )

    try:
        passed, candidate, immediate, settled, selector, detail = _exercise_one_control(
            page, section_path, primary, ordinal,
            numeric=numeric, recheck_wait_ms=recheck_wait_ms,
        )
        result.selectors.append(selector)
        if candidate is not None:
            result.candidate.append(candidate)
        result.immediate.append(immediate)
        result.settled.append(settled)
        result.detail = detail
        if not passed:
            return result

        if qualifiers:
            q_passed, q_candidate, q_immediate, q_settled, q_selector, q_detail = _exercise_one_control(
                page, section_path, qualifiers[0], ordinal + 50,
                numeric=False, recheck_wait_ms=recheck_wait_ms,
            )
            result.selectors.append(q_selector)
            if q_candidate is not None:
                result.candidate.append(q_candidate)
            result.immediate.append(q_immediate)
            result.settled.append(q_settled)
            if not q_passed:
                result.detail = f"主值通过；qualifier 失败：{q_detail}"
                return result

        if exercise_multi_value:
            live_primary, _ = _unique_visible_locator(page, section_path, primary)
            add = _click_add_value_if_present(live_primary)
            result.plus_available = bool(add.get("available"))
            if result.plus_available:
                page.wait_for_timeout(300)
                refreshed_controls = adapter.scan_section_fields(
                    section_path, include_values=True, wait_ms=wait_ms,
                    max_scroll_steps=max_scroll_steps,
                )
                refreshed_fields = adapter.build_semantic_fields(refreshed_controls)
                refreshed = _match_field(refreshed_fields, key, label)
                if refreshed is None:
                    result.plus_status = FAIL
                    result.detail = "点击 + 后无法重新发现该 semantic field。"
                    return result
                refreshed_values = _value_controls(refreshed)
                if len(refreshed_values) <= len(controls):
                    result.plus_status = FAIL
                    result.detail = "点击 + 后没有出现新的 indexed value slot。"
                    return result

                second = refreshed_values[len(controls)]
                second_numeric = (
                    bool(_qualifier_controls(refreshed))
                    or bool(_static_unit_suffix(second))
                    or str(second.get("type") or "").casefold() == "number"
                    or str(second.get("inputmode") or "").casefold() in {"numeric", "decimal"}
                )
                p2, c2, i2, s2, sel2, d2 = _exercise_one_control(
                    page, section_path, second, ordinal + 100,
                    numeric=second_numeric, recheck_wait_ms=recheck_wait_ms,
                    avoid_option=result.candidate[0] if result.candidate else None,
                )
                result.selectors.append(sel2)
                if c2 is not None:
                    result.candidate.append(c2)
                result.immediate.append(i2)
                result.settled.append(s2)
                result.plus_status = PASS if p2 else FAIL
                if not p2:
                    result.detail = f"第一槽通过，但 + 新增槽位失败：{d2}"
                    return result

        result.status = PASS
        if result.plus_available:
            result.detail = "主控件稳定回读通过；+ 新增槽位也填写并稳定回读通过。"
        elif qualifiers:
            result.detail = "主值与 qualifier 均填写并稳定回读通过。"
        else:
            result.detail = "控件填写并在 React 渲染周期后稳定回读通过。"
        return result
    except Exception as exc:
        result.detail = str(exc)
        return result


def _require_collapsed_section(adapter: Any, section_title: str) -> dict[str, Any]:
    section = adapter.find_section(section_title)
    if section is None:
        raise RuntimeError(f"当前页面找不到 section：{section_title}")
    if not section.get("has_edit"):
        raise RuntimeError(
            f"section {section_title!r} 当前不是折叠状态。"
            "为避免 Cancel 丢弃用户已有未保存内容，coverage test 已停止。"
        )
    return section


def cancel_section(adapter: Any, section_title: str, *, wait_ms: int = 450) -> None:
    """Cancel only the target section and prove it returned to collapsed state."""

    section = adapter.find_section(section_title)
    if section is None:
        raise RuntimeError(f"Cancel 前找不到 section：{section_title}")
    path = str(section.get("path") or "")
    if not path:
        raise RuntimeError("section 缺少 DOM path，无法安全 Cancel。")
    card = adapter.page.locator(path)
    cancel = card.get_by_text("Cancel", exact=True)
    if cancel.count() != 1 or not cancel.first.is_visible():
        raise RuntimeError("目标 section 没有唯一可见的 Cancel；为避免误操作已停止。")
    cancel.first.click()
    adapter.page.wait_for_timeout(wait_ms)
    collapsed = adapter.find_section(section_title)
    if collapsed is None or not collapsed.get("has_edit"):
        raise RuntimeError("Cancel 后 section 未恢复为折叠态；已停止后续测试。")


def discover_fields(
    adapter: Any,
    section_title: str,
    *,
    wait_ms: int = 250,
    max_scroll_steps: int = 200,
) -> tuple[list[dict[str, str]], list[CoverageResult]]:
    """Discover current empty fields without changing persistent state."""

    section = _require_collapsed_section(adapter, section_title)
    adapter.open_section_for_edit(section)
    skipped: list[CoverageResult] = []
    try:
        live = adapter.find_section(section_title) or section
        section_path = str(live.get("path") or "")
        controls = adapter.scan_section_fields(
            section_path, include_values=True, wait_ms=wait_ms,
            max_scroll_steps=max_scroll_steps,
        )
        fields = adapter.build_semantic_fields(controls)
        targets: list[dict[str, str]] = []
        for field_item in fields:
            if semantic_field_is_empty(field_item):
                targets.append({
                    "attribute_key": str(field_item.get("attribute_key") or ""),
                    "label": str(field_item.get("label") or ""),
                })
            else:
                skipped.append(CoverageResult(
                    section=str(field_item.get("section_heading") or section_title),
                    subsection=str(field_item.get("subsection_heading") or ""),
                    attribute_key=str(field_item.get("attribute_key") or ""),
                    label=str(field_item.get("label") or ""),
                    shape=field_shape(field_item),
                    status=SKIPPED_EXISTING,
                    detail="当前字段已有非 placeholder 值；默认不覆盖已有数据。",
                ))
        return targets, skipped
    finally:
        cancel_section(adapter, section_title)


def run_section_coverage(
    adapter: Any,
    section_title: str,
    *,
    recheck_wait_ms: int = 800,
    exercise_multi_value: bool = True,
    wait_ms: int = 250,
    max_scroll_steps: int = 200,
) -> list[CoverageResult]:
    """Test each empty field in an isolated unsaved transaction."""

    targets, skipped = discover_fields(
        adapter, section_title, wait_ms=wait_ms, max_scroll_steps=max_scroll_steps
    )
    results = list(skipped)

    for ordinal, identity in enumerate(targets, start=1):
        section = _require_collapsed_section(adapter, section_title)
        adapter.open_section_for_edit(section)
        try:
            live = adapter.find_section(section_title) or section
            section_path = str(live.get("path") or "")
            controls = adapter.scan_section_fields(
                section_path, include_values=True, wait_ms=wait_ms,
                max_scroll_steps=max_scroll_steps,
            )
            fields = adapter.build_semantic_fields(controls)
            field_item = _match_field(fields, identity["attribute_key"], identity["label"])
            if field_item is None:
                results.append(CoverageResult(
                    section=section_title, subsection="",
                    attribute_key=identity["attribute_key"], label=identity["label"],
                    shape="unknown", status=NOT_FOUND,
                    detail="重新打开 section 后未找到该字段。",
                ))
                continue
            if not semantic_field_is_empty(field_item):
                results.append(CoverageResult(
                    section=str(field_item.get("section_heading") or section_title),
                    subsection=str(field_item.get("subsection_heading") or ""),
                    attribute_key=identity["attribute_key"], label=identity["label"],
                    shape=field_shape(field_item), status=SKIPPED_EXISTING,
                    detail="测试前字段已出现非 placeholder 值；为保护现有数据未覆盖。",
                ))
                continue

            results.append(exercise_live_field(
                adapter, field_item, section_path, ordinal,
                recheck_wait_ms=recheck_wait_ms,
                exercise_multi_value=exercise_multi_value,
                wait_ms=wait_ms, max_scroll_steps=max_scroll_steps,
            ))
        finally:
            cancel_section(adapter, section_title)

    return results


def summarize_results(results: list[CoverageResult]) -> dict[str, Any]:
    empty_attempts = [item for item in results if item.status != SKIPPED_EXISTING]
    by_shape: dict[str, dict[str, int]] = {}
    for item in empty_attempts:
        bucket = by_shape.setdefault(item.shape, {"total": 0, "pass": 0})
        bucket["total"] += 1
        if item.status == PASS:
            bucket["pass"] += 1

    passed = sum(1 for item in empty_attempts if item.status == PASS)
    return {
        "empty_field_attempts": len(empty_attempts),
        "passed": passed,
        "failed_or_unsupported": len(empty_attempts) - passed,
        "skipped_existing": sum(1 for item in results if item.status == SKIPPED_EXISTING),
        "all_empty_passed": bool(empty_attempts) and passed == len(empty_attempts),
        "by_shape": by_shape,
    }
