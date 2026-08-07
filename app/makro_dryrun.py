from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from playwright.sync_api import Page

from .answer_resolver import RESOLVED, ResolvedAnswer


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


def _css_attr(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def selector_for_control(control: dict[str, Any]) -> str:
    """Prefer selectors that remain unique for Makro multi-value controls."""

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


def _fill_control(page: Page, control: dict[str, Any], value: str) -> str:
    selector = selector_for_control(control)
    locator = page.locator(selector).first
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


def _read_control(page: Page, control: dict[str, Any]) -> str:
    selector = selector_for_control(control)
    locator = page.locator(selector).first
    kind = str(control.get("field_kind") or "input")
    if kind == "select":
        return locator.locator("option:checked").inner_text().strip()
    if kind in {"checkbox", "custom_checkbox"}:
        return "true" if locator.is_checked() else "false"
    if kind in {"dropdown", "autocomplete", "listbox"}:
        value = locator.get_attribute("value")
        return (value if value is not None else locator.inner_text()).strip()
    return locator.input_value().strip()


def fill_resolved_field(
    page: Page,
    semantic_field: dict[str, Any],
    answer: ResolvedAnswer,
) -> FillVerification:
    """Fill one resolved semantic field and immediately read it back.

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
            selectors.append(_fill_control(page, control, value))
        if answer.qualifier:
            qualifier_controls = _qualifier_controls(semantic_field)
            if qualifier_controls:
                selectors.append(_fill_control(page, qualifier_controls[0], answer.qualifier))

        for control in controls[: len(values)]:
            actual.append(_read_control(page, control))

        expected_norm = [_norm(item) for item in values[: len(actual)]]
        actual_norm = [_norm(item) for item in actual]
        passed = expected_norm == actual_norm and len(actual) == len(values)
        detail = "填写后回读一致。" if passed else "填写后回读与期望不一致。"
        if len(values) > len(controls):
            passed = False
            detail = (
                f"答案有 {len(values)} 个值，但当前页面只有 {len(controls)} 个 value control；"
                "已只填写当前可用槽位。"
            )
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
