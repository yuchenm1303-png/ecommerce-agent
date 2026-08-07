from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from playwright.sync_api import Page

from .answer_resolver import RESOLVED, ResolvedAnswer
from .makro.locators import scoped_selector_for_control, selector_for_control  # noqa: F401


@dataclass(slots=True)
class FillVerification:
    attribute_key: str
    label: str
    status: str
    expected: list[str] = field(default_factory=list)
    actual: list[str] = field(default_factory=list)
    selectors: list[str] = field(default_factory=list)
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "attribute_key": self.attribute_key,
            "label": self.label,
            "status": self.status,
            "expected": self.expected,
            "actual": self.actual,
            "selectors": self.selectors,
            "detail": self.detail,
        }


def _value_index(control: dict[str, Any]) -> tuple[int, str]:
    name = str(control.get("name") or "")
    match = re.search(r"_(\d+)_value$", name)
    return (int(match.group(1)) if match else 0, name)


def _value_controls(semantic_field: dict[str, Any]) -> list[dict[str, Any]]:
    controls: list[dict[str, Any]] = []
    for control in semantic_field.get("controls") or []:
        name = str(control.get("name") or "")
        if name.endswith("_qualifier"):
            continue
        if control.get("field_kind") == "option":
            continue
        controls.append(control)
    return sorted(controls, key=_value_index)


def _qualifier_controls(semantic_field: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        control
        for control in semantic_field.get("controls") or []
        if str(control.get("name") or "").endswith("_qualifier")
    ]


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


def _single_locator(
    page: Page,
    control: dict[str, Any],
    section_path: str | None,
) -> tuple[Any, str]:
    """Resolve one control scoped to its section, refusing ambiguous matches.

    A global ``[name=...]`` selector can collide with a duplicate or stale React
    instance elsewhere in the document; scoping by the section card path keeps
    the write/read on the visible control the user actually sees.
    """

    selector = scoped_selector_for_control(section_path, control)
    all_locator = page.locator(selector)
    if all_locator.count() != 1:
        raise RuntimeError(
            f"定位器 {selector} 匹配到 {all_locator.count()} 个控件，期望恰好 1 个；"
            "已拒绝填写/回读以避免命中错误 DOM 实例。"
        )
    locator = all_locator.first
    if not locator.is_visible():
        raise RuntimeError(
            f"定位器 {selector} 指向不可见控件，已拒绝填写/回读。"
        )
    return locator, selector


def _fill_control(
    page: Page, control: dict[str, Any], value: str, section_path: str | None = None
) -> str:
    locator, selector = _single_locator(page, control, section_path)
    locator.wait_for(state="visible")
    kind = str(control.get("field_kind") or "input")

    if kind in {"input", "textarea", "contenteditable", "custom_textbox", "custom_spinbutton"}:
        locator.fill(value)
        return selector
    if kind == "select":
        try:
            locator.select_option(label=value)
        except Exception:
            locator.select_option(value=value)
        return selector
    if kind in {"checkbox", "custom_checkbox"}:
        should_check = _norm(value) in {"1", "true", "yes", "y", "是", "有", "checked"}
        if should_check:
            locator.check()
        else:
            locator.uncheck()
        return selector
    if kind in {"dropdown", "autocomplete", "listbox"}:
        locator.click()
        page.get_by_text(value, exact=True).last.click()
        return selector

    raise ValueError(f"暂不支持 Makro 控件类型：{kind}")


def _read_control(
    page: Page,
    control: dict[str, Any],
    section_path: str | None = None,
    *,
    timeout_ms: int = 15_000,
) -> str:
    locator, _ = _single_locator(page, control, section_path)
    kind = str(control.get("field_kind") or "input")
    if kind == "select":
        return locator.locator("option:checked").inner_text(timeout=timeout_ms).strip()
    if kind in {"checkbox", "custom_checkbox"}:
        return "true" if locator.is_checked() else "false"
    if kind in {"dropdown", "autocomplete", "listbox"}:
        value = locator.get_attribute("value", timeout=timeout_ms)
        return (value if value is not None else locator.inner_text(timeout=timeout_ms)).strip()
    return locator.input_value(timeout=timeout_ms).strip()


def fill_resolved_field(
    page: Page,
    semantic_field: dict[str, Any],
    answer: ResolvedAnswer,
    *,
    section_path: str | None = None,
    recheck_wait_ms: int = 800,
) -> FillVerification:
    """Fill one resolved semantic field and verify it survives a render cycle.

    The locator is scoped to the owning listing section card (``section_path``)
    so a duplicate/hidden React instance can never be written or read instead
    of the visible control. After the immediate readback we wait one render
    cycle (``recheck_wait_ms``) and re-read: a controlled React input may accept
    a native DOM write and report it, then reset on the next render. Only a
    value that still matches after the wait is reported as ``validated``.

    This function never clicks section Save or Send to QC. It only touches the
    controls belonging to the supplied semantic field.
    """

    if answer.status != RESOLVED:
        return FillVerification(
            attribute_key=answer.attribute_key,
            label=answer.label,
            status="skipped",
            detail=f"resolver status={answer.status}",
        )

    values = list(answer.answer_values)
    controls = _value_controls(semantic_field)
    if not controls:
        return FillVerification(
            attribute_key=answer.attribute_key,
            label=answer.label,
            status="fill_error",
            expected=values,
            detail="semantic field 中没有可填写 value control。",
        )

    selectors: list[str] = []
    actual: list[str] = []
    try:
        for control, value in zip(controls, values):
            selectors.append(_fill_control(page, control, value, section_path=section_path))
        if answer.qualifier:
            qualifier_controls = _qualifier_controls(semantic_field)
            if qualifier_controls:
                selectors.append(
                    _fill_control(page, qualifier_controls[0], answer.qualifier, section_path=section_path)
                )

        for control in controls[: len(values)]:
            actual.append(_read_control(page, control, section_path=section_path))

        expected_norm = [_norm(item) for item in values[: len(actual)]]
        actual_norm = [_norm(item) for item in actual]
        immediate_passed = expected_norm == actual_norm and len(actual) == len(values)

        if len(values) > len(controls):
            return FillVerification(
                attribute_key=answer.attribute_key,
                label=answer.label,
                status="validation_failed",
                expected=values,
                actual=actual,
                selectors=selectors,
                detail=(
                    f"答案有 {len(values)} 个值，但当前页面只有 {len(controls)} 个 value control；"
                    "已只填写当前可用槽位。"
                ),
            )

        # Stable re-readback after a render cycle: a controlled React input may
        # accept a native DOM write and immediately report it, then reset on the
        # next render (observed on Makro Additional Description fields).
        page.wait_for_timeout(recheck_wait_ms)
        settled: list[str] = []
        try:
            for control in controls[: len(values)]:
                settled.append(
                    _read_control(page, control, section_path=section_path, timeout_ms=3_000)
                )
        except Exception as exc:
            return FillVerification(
                attribute_key=answer.attribute_key,
                label=answer.label,
                status="validation_failed",
                expected=values,
                actual=actual,
                selectors=selectors,
                detail=(
                    f"填写后立即回读一致，但等待 {recheck_wait_ms}ms 后控件不可读"
                    f"（疑似 React 重渲染移除/重置）：{exc}"
                ),
            )

        settled_norm = [_norm(item) for item in settled]
        settled_passed = settled_norm == expected_norm and len(settled) == len(values)

        if immediate_passed and settled_passed:
            passed = True
            detail = "填写后回读一致，且等待 React 渲染周期后再次回读一致。"
        elif immediate_passed and not settled_passed:
            passed = False
            detail = (
                f"填写后立即回读一致，但等待 {recheck_wait_ms}ms 后值被重置为 {settled!r}；"
                "疑似 React 重渲染回滚，未视为 validated。"
            )
        else:
            passed = False
            detail = "填写后回读与期望不一致。"

        return FillVerification(
            attribute_key=answer.attribute_key,
            label=answer.label,
            status="validated" if passed else "validation_failed",
            expected=values,
            actual=actual,
            selectors=selectors,
            detail=detail,
        )
    except Exception as exc:
        return FillVerification(
            attribute_key=answer.attribute_key,
            label=answer.label,
            status="fill_error",
            expected=values,
            actual=actual,
            selectors=selectors,
            detail=str(exc),
        )
