from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from playwright.sync_api import Page

from .makro.field_engine import (
    execution_contract,
    fill_control as _engine_fill_control,
    fill_radio_group,
    is_radio_group,
    radio_group_values_equivalent,
    read_control as _engine_read_control,
    read_radio_group,
    values_equivalent,
)
from .makro.locators import scoped_selector_for_control, selector_for_control  # noqa: F401
from .resolution_types import RESOLVED, ResolvedAnswer


@dataclass(slots=True)
class FillVerification:
    attribute_key: str
    label: str
    status: str
    expected: list[str] = field(default_factory=list)
    actual: list[str] = field(default_factory=list)
    selectors: list[str] = field(default_factory=list)
    detail: str = ""
    execution_family: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "attribute_key": self.attribute_key,
            "label": self.label,
            "status": self.status,
            "expected": self.expected,
            "actual": self.actual,
            "selectors": self.selectors,
            "detail": self.detail,
            "execution_family": self.execution_family,
        }


def _value_index(control: dict[str, Any]) -> tuple[int, str]:
    name = str(control.get("name") or "")
    match = re.search(r"_(\d+)_value$", name)
    return (int(match.group(1)) if match else 0, name)


def _qualifier_index(control: dict[str, Any]) -> tuple[int, str]:
    name = str(control.get("name") or "")
    match = re.search(r"_(\d+)_qualifier$", name)
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
    controls = [
        control
        for control in semantic_field.get("controls") or []
        if str(control.get("name") or "").endswith("_qualifier")
    ]
    return sorted(controls, key=_qualifier_index)


def _qualifier_targets(
    semantic_field: dict[str, Any], value_count: int
) -> list[dict[str, Any]]:
    """Return every qualifier control that must be committed for this answer.

    Makro uses both one shared qualifier and one qualifier per repeated value
    slot. A partially repeated shape (e.g. three values but only two qualifier
    controls) is ambiguous and must fail before any write.
    """

    controls = _qualifier_controls(semantic_field)
    if not controls or value_count <= 0:
        return []
    if len(controls) == 1:
        return controls
    if len(controls) >= value_count:
        return controls[:value_count]
    raise ValueError(
        f"当前页面有 {value_count} 个待写 value，但只有 {len(controls)} 个 qualifier control；"
        "无法确定单位对应关系，未执行任何部分写入。"
    )


def _fill_control(
    page: Page, control: dict[str, Any], value: str, section_path: str | None = None
) -> str:
    """Compatibility wrapper; concrete DOM mechanics live in field_engine."""

    return _engine_fill_control(page, control, value, section_path=section_path)


def _read_control(
    page: Page,
    control: dict[str, Any],
    section_path: str | None = None,
    *,
    timeout_ms: int = 15_000,
) -> str:
    """Compatibility wrapper; concrete DOM mechanics live in field_engine."""

    return _engine_read_control(
        page,
        control,
        section_path=section_path,
        timeout_ms=timeout_ms,
    )


def _compare_answer_values(
    expected: list[str],
    actual: list[str],
    controls: list[dict[str, Any]],
) -> bool:
    if len(expected) != len(actual) or len(expected) > len(controls):
        return False
    return all(
        values_equivalent(control, expected_value, actual_value)
        for control, expected_value, actual_value in zip(controls, expected, actual)
    )


def _preflight_answer_capacity(
    semantic_field: dict[str, Any],
    answer: ResolvedAnswer,
) -> str | None:
    values = list(answer.answer_values)
    controls = _value_controls(semantic_field)
    contract = execution_contract(semantic_field, answer)
    if not values:
        return "resolved answer 没有可写 answer_values。"
    if not controls:
        return "semantic field 中没有可填写 value control。"
    if not contract.supported:
        return f"Generic Field Engine 不支持当前 live control：{contract.reason}"
    if is_radio_group(controls):
        if len(values) != 1:
            return (
                f"radio semantic field 需要恰好 1 个答案值，当前有 {len(values)} 个；"
                "未执行任何部分写入。"
            )
    elif len(values) > len(controls):
        return (
            f"答案有 {len(values)} 个值，但当前页面只有 {len(controls)} 个 value control；"
            "未执行任何部分写入。"
        )
    if answer.qualifier:
        if not _qualifier_controls(semantic_field):
            return "答案包含 qualifier，但当前 semantic field 没有 qualifier control；未执行写入。"
        try:
            _qualifier_targets(semantic_field, len(values))
        except ValueError as exc:
            return str(exc)
    return None


def _read_values(
    page: Page,
    controls: list[dict[str, Any]],
    value_count: int,
    *,
    section_path: str | None,
    timeout_ms: int,
) -> tuple[list[str], list[str]]:
    if is_radio_group(controls):
        selectors = [scoped_selector_for_control(section_path, control) for control in controls]
        return [read_radio_group(page, controls, section_path=section_path)], selectors

    actual: list[str] = []
    selectors: list[str] = []
    for control in controls[:value_count]:
        selectors.append(scoped_selector_for_control(section_path, control))
        actual.append(
            _read_control(
                page,
                control,
                section_path=section_path,
                timeout_ms=timeout_ms,
            )
        )
    return actual, selectors


def _values_passed(
    controls: list[dict[str, Any]], expected: list[str], actual: list[str]
) -> bool:
    if is_radio_group(controls):
        return (
            len(expected) == 1
            and len(actual) == 1
            and radio_group_values_equivalent(controls, expected[0], actual[0])
        )
    return _compare_answer_values(expected, actual, controls)


def _read_qualifiers(
    page: Page,
    semantic_field: dict[str, Any],
    expected_qualifier: str | None,
    value_count: int,
    *,
    section_path: str | None,
    timeout_ms: int,
) -> tuple[bool, list[str], list[str]]:
    if not expected_qualifier:
        return True, [], []
    controls = _qualifier_targets(semantic_field, value_count)
    if not controls:
        return False, [], []
    actual: list[str] = []
    selectors: list[str] = []
    passed = True
    for control in controls:
        selectors.append(scoped_selector_for_control(section_path, control))
        value = _read_control(
            page,
            control,
            section_path=section_path,
            timeout_ms=timeout_ms,
        )
        actual.append(value)
        passed = passed and values_equivalent(control, expected_qualifier, value)
    return passed, actual, selectors


def verify_resolved_field(
    page: Page,
    semantic_field: dict[str, Any],
    answer: ResolvedAnswer,
    *,
    section_path: str | None = None,
) -> FillVerification:
    values = list(answer.answer_values)
    controls = _value_controls(semantic_field)
    contract = execution_contract(semantic_field, answer)
    capacity_error = _preflight_answer_capacity(semantic_field, answer)
    if capacity_error:
        return FillVerification(
            attribute_key=answer.attribute_key,
            label=answer.label,
            status="validation_failed",
            expected=values,
            detail=f"持久化复核失败：{capacity_error}",
            execution_family=contract.live_family,
        )

    actual: list[str] = []
    selectors: list[str] = []
    try:
        actual, selectors = _read_values(
            page,
            controls,
            len(values),
            section_path=section_path,
            timeout_ms=3_000,
        )
        qualifier_ok, _, qualifier_selectors = _read_qualifiers(
            page,
            semantic_field,
            answer.qualifier,
            len(values),
            section_path=section_path,
            timeout_ms=3_000,
        )
        for selector in qualifier_selectors:
            if selector not in selectors:
                selectors.append(selector)

        passed = _values_passed(controls, values, actual) and qualifier_ok
        return FillVerification(
            attribute_key=answer.attribute_key,
            label=answer.label,
            status="persisted_verified" if passed else "validation_failed",
            expected=values,
            actual=actual,
            selectors=selectors,
            detail=(
                "Save 后重新打开，字段值与期望一致。"
                if passed
                else "Save 后重新打开，字段值/qualifier 与期望不一致。"
            ),
            execution_family=contract.live_family,
        )
    except Exception as exc:
        return FillVerification(
            attribute_key=answer.attribute_key,
            label=answer.label,
            status="validation_failed",
            expected=values,
            actual=actual,
            selectors=selectors,
            detail=f"Save 后重新打开复核失败：{exc}",
            execution_family=contract.live_family,
        )


def fill_resolved_field(
    page: Page,
    semantic_field: dict[str, Any],
    answer: ResolvedAnswer,
    *,
    section_path: str | None = None,
    recheck_wait_ms: int = 800,
) -> FillVerification:
    contract = execution_contract(semantic_field, answer)
    if answer.status != RESOLVED:
        return FillVerification(
            attribute_key=answer.attribute_key,
            label=answer.label,
            status="skipped",
            detail=f"resolution status={answer.status}",
            execution_family=contract.live_family,
        )

    values = list(answer.answer_values)
    preflight_error = _preflight_answer_capacity(semantic_field, answer)
    if preflight_error:
        return FillVerification(
            attribute_key=answer.attribute_key,
            label=answer.label,
            status="validation_failed",
            expected=values,
            detail=preflight_error,
            execution_family=contract.live_family,
        )

    controls = _value_controls(semantic_field)
    qualifier_targets = _qualifier_targets(semantic_field, len(values)) if answer.qualifier else []
    selectors: list[str] = []
    actual: list[str] = []
    try:
        if is_radio_group(controls):
            selectors.append(
                fill_radio_group(page, controls, values[0], section_path=section_path)
            )
        else:
            for control, value in zip(controls, values):
                selectors.append(
                    _fill_control(page, control, value, section_path=section_path)
                )
        if answer.qualifier:
            for qualifier_control in qualifier_targets:
                selectors.append(
                    _fill_control(
                        page,
                        qualifier_control,
                        answer.qualifier,
                        section_path=section_path,
                    )
                )

        actual, read_selectors = _read_values(
            page,
            controls,
            len(values),
            section_path=section_path,
            timeout_ms=15_000,
        )
        for selector in read_selectors:
            if selector not in selectors:
                selectors.append(selector)
        immediate_qualifier_ok, _, qualifier_selectors = _read_qualifiers(
            page,
            semantic_field,
            answer.qualifier,
            len(values),
            section_path=section_path,
            timeout_ms=15_000,
        )
        for selector in qualifier_selectors:
            if selector not in selectors:
                selectors.append(selector)
        immediate_passed = _values_passed(controls, values, actual) and immediate_qualifier_ok

        page.wait_for_timeout(recheck_wait_ms)
        try:
            settled, _ = _read_values(
                page,
                controls,
                len(values),
                section_path=section_path,
                timeout_ms=3_000,
            )
            settled_qualifier_ok, _, _ = _read_qualifiers(
                page,
                semantic_field,
                answer.qualifier,
                len(values),
                section_path=section_path,
                timeout_ms=3_000,
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
                execution_family=contract.live_family,
            )

        settled_passed = _values_passed(controls, values, settled) and settled_qualifier_ok
        if immediate_passed and settled_passed:
            passed = True
            detail = "Generic Field Engine 写入后立即回读一致，且等待 React 渲染周期后再次回读一致。"
        elif immediate_passed:
            passed = False
            detail = (
                f"填写后立即回读一致，但等待 {recheck_wait_ms}ms 后值被重置为 {settled!r}；"
                "疑似 React 重渲染回滚，未视为 validated。"
            )
        else:
            passed = False
            detail = "Generic Field Engine 写入后字段值/qualifier 回读与期望不一致。"

        return FillVerification(
            attribute_key=answer.attribute_key,
            label=answer.label,
            status="validated" if passed else "validation_failed",
            expected=values,
            actual=actual,
            selectors=selectors,
            detail=detail,
            execution_family=contract.live_family,
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
            execution_family=contract.live_family,
        )
