from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Iterable

from .locators import scoped_selector_for_control


_TEXT_KINDS = {
    "input",
    "textarea",
    "contenteditable",
    "custom_textbox",
    "custom_searchbox",
}
_SELECT_KINDS = {"select", "dropdown", "autocomplete", "listbox"}
_BOOLEAN_KINDS = {"checkbox", "custom_checkbox"}
_RADIO_KINDS = {"radio", "custom_radio"}
_NUMERIC_KINDS = {"custom_spinbutton", "custom_slider"}

_TRUE_VALUES = {"1", "true", "yes", "y", "是", "有", "checked", "on"}
_FALSE_VALUES = {"0", "false", "no", "n", "否", "无", "unchecked", "off"}


@dataclass(slots=True, frozen=True)
class FieldExecutionContract:
    """Pure mechanical execution contract derived from the current live field.

    Harvested registry metadata can be attached to a semantic field as
    ``execution_family``/``schema_execution_family``, but it never overrides the
    current DOM. The DOM decides which adapter may actually write the control.
    """

    live_family: str
    schema_family: str
    value_control_count: int
    multi_value: bool
    qualifier: bool
    supported: bool
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "live_family": self.live_family,
            "schema_family": self.schema_family,
            "value_control_count": self.value_control_count,
            "multi_value": self.multi_value,
            "qualifier": self.qualifier,
            "supported": self.supported,
            "reason": self.reason,
        }


def _norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def is_numeric_control(control: dict[str, Any]) -> bool:
    field_kind = str(control.get("field_kind") or "").casefold()
    input_type = str(control.get("type") or "").casefold()
    role = str(control.get("role") or "").casefold()
    inputmode = str(control.get("inputmode") or "").casefold()
    return (
        field_kind in _NUMERIC_KINDS
        or input_type in {"number", "range"}
        or role in {"spinbutton", "slider"}
        or inputmode in {"numeric", "decimal"}
    )


def is_radio_control(control: dict[str, Any]) -> bool:
    return str(control.get("field_kind") or "").casefold() in _RADIO_KINDS


def is_radio_group(controls: Iterable[dict[str, Any]]) -> bool:
    items = list(controls)
    return bool(items) and all(is_radio_control(control) for control in items)


def _value_controls(semantic_field: dict[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for control in semantic_field.get("controls") or []:
        if str(control.get("name") or "").endswith("_qualifier"):
            continue
        if control.get("field_kind") == "option":
            continue
        output.append(control)
    return output


def _qualifier_controls(semantic_field: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        control
        for control in semantic_field.get("controls") or []
        if str(control.get("name") or "").endswith("_qualifier")
    ]


def _primary_control(semantic_field: dict[str, Any], controls: list[dict[str, Any]]) -> dict[str, Any] | None:
    key = str(semantic_field.get("attribute_key") or "")
    for control in controls:
        if key and str(control.get("id") or "") == key:
            return control
    return controls[0] if controls else None


def _live_base_family(control: dict[str, Any] | None, controls: list[dict[str, Any]]) -> str:
    if not control:
        return "unsupported"
    if is_radio_group(controls):
        return "selection"
    kind = str(control.get("field_kind") or "").casefold()
    if kind in _BOOLEAN_KINDS:
        return "boolean"
    if is_numeric_control(control):
        return "numeric"
    if kind in _SELECT_KINDS:
        return "selection"
    if kind in {"textarea", "contenteditable"}:
        return "long_text"
    if kind in _TEXT_KINDS:
        return "text"
    return "unsupported"


def execution_contract(semantic_field: dict[str, Any], answer: Any | None = None) -> FieldExecutionContract:
    controls = _value_controls(semantic_field)
    primary = _primary_control(semantic_field, controls)
    base = _live_base_family(primary, controls)
    values = list(getattr(answer, "answer_values", None) or []) if answer is not None else []
    multi = bool(semantic_field.get("multi_value")) or len(values) > 1
    qualifier = bool(getattr(answer, "qualifier", None)) or bool(_qualifier_controls(semantic_field))
    suffixes: list[str] = []
    if multi:
        suffixes.append("multi")
    if qualifier:
        suffixes.append("qualified")
    live_family = "_".join([base, *suffixes]) if suffixes else base
    schema_family = str(
        semantic_field.get("schema_execution_family")
        or semantic_field.get("execution_family")
        or ""
    )
    supported = base != "unsupported"
    reason = "" if supported else "live semantic field has no supported writable control family"
    return FieldExecutionContract(
        live_family=live_family,
        schema_family=schema_family,
        value_control_count=len(controls),
        multi_value=multi,
        qualifier=qualifier,
        supported=supported,
        reason=reason,
    )


def _single_locator(page: Any, control: dict[str, Any], section_path: str | None) -> tuple[Any, str]:
    selector = scoped_selector_for_control(section_path, control)
    all_locator = page.locator(selector)
    count = all_locator.count()
    if count != 1:
        raise RuntimeError(
            f"定位器 {selector} 匹配到 {count} 个控件，期望恰好 1 个；"
            "已拒绝填写/回读以避免命中错误 DOM 实例。"
        )
    locator = all_locator.first
    if not locator.is_visible():
        raise RuntimeError(f"定位器 {selector} 指向不可见控件，已拒绝填写/回读。")
    return locator, selector


def _ensure_writable(locator: Any, control: dict[str, Any], selector: str) -> None:
    if control.get("disabled"):
        raise RuntimeError(f"控件 {selector} 当前 disabled，拒绝写入。")
    if control.get("readonly"):
        raise RuntimeError(f"控件 {selector} 当前 readonly，拒绝写入。")
    is_disabled = getattr(locator, "is_disabled", None)
    if callable(is_disabled):
        try:
            if is_disabled():
                raise RuntimeError(f"控件 {selector} 当前 disabled，拒绝写入。")
        except RuntimeError:
            raise
        except Exception:
            pass


def _finite_numeric_text(value: object) -> str:
    text = str(value or "").strip()
    try:
        number = float(text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"数值控件拒绝非数字值 {text!r}。") from exc
    if not math.isfinite(number):
        raise ValueError(f"数值控件拒绝非有限数字 {text!r}。")
    return text


def _boolean_target(value: object) -> bool:
    normalized = _norm(value)
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ValueError(f"布尔控件无法机械解释值 {value!r}；仅接受明确 true/false/yes/no/1/0。")


def _commit_value(locator: Any, *, dispatch_value_events: bool) -> None:
    """Finish one live-control mutation using the browser event contract.

    Playwright ``fill`` changes the DOM value and emits ``input``, but Makro's
    controlled React fields do not all commit their model state on the same
    event.  Every editable value therefore finishes with deterministic
    input/change/blur semantics before any readback or repeatable-slot action.
    This is mechanical DOM behaviour only; it contains no field-specific rules.
    """

    if dispatch_value_events:
        dispatch = getattr(locator, "dispatch_event", None)
        if callable(dispatch):
            dispatch("input")
            dispatch("change")
    blur = getattr(locator, "blur", None)
    if callable(blur):
        blur()


def _option_aliases(option: dict[str, Any]) -> set[str]:
    return {
        normalized
        for normalized in (
            _norm(option.get("text")),
            _norm(option.get("value")),
        )
        if normalized
    }


def _matched_option(control: dict[str, Any], value: object) -> dict[str, Any] | None:
    options = [option for option in control.get("options") or [] if not option.get("disabled")]
    if not options:
        return None
    target = _norm(value)
    matches = [option for option in options if target in _option_aliases(option)]
    if len(matches) > 1:
        raise RuntimeError(f"值 {value!r} 在当前 live options 中匹配到多个候选，拒绝猜测。")
    if not matches:
        raise ValueError(f"值 {value!r} 不在当前 live options 中，拒绝写入。")
    return matches[0]


def _click_unique_visible_text(page: Any, text: str) -> None:
    candidates = page.get_by_text(text, exact=True)
    count = candidates.count()
    visible: list[Any] = []
    for index in range(count):
        candidate = candidates.nth(index)
        if candidate.is_visible():
            visible.append(candidate)
    if len(visible) != 1:
        raise RuntimeError(
            f"下拉选项 {text!r} 当前可见精确匹配数={len(visible)}，期望恰好 1 个；拒绝全页 last/first 猜测。"
        )
    visible[0].click()


def fill_control(page: Any, control: dict[str, Any], value: str, section_path: str | None = None) -> str:
    locator, selector = _single_locator(page, control, section_path)
    _ensure_writable(locator, control, selector)
    locator.wait_for(state="visible")
    kind = str(control.get("field_kind") or "input").casefold()

    if is_numeric_control(control):
        numeric = _finite_numeric_text(value)
        if kind == "custom_slider" and str(control.get("tag") or "").casefold() != "input":
            raise ValueError("非原生 role=slider 控件缺少确定性直接赋值契约，已 fail closed。")
        locator.fill(numeric)
        _commit_value(locator, dispatch_value_events=True)
        return selector

    if kind in _TEXT_KINDS:
        locator.fill(value)
        _commit_value(locator, dispatch_value_events=True)
        return selector

    if kind == "select":
        matched = _matched_option(control, value)
        if matched is not None:
            label = str(matched.get("text") or "").strip()
            option_value = str(matched.get("value") or "").strip()
            if label:
                locator.select_option(label=label)
            elif option_value:
                locator.select_option(value=option_value)
            else:
                raise ValueError(f"live option {matched!r} 没有可写 label/value。")
        else:
            try:
                locator.select_option(label=value)
            except Exception:
                locator.select_option(value=value)
        _commit_value(locator, dispatch_value_events=True)
        return selector

    if kind in _BOOLEAN_KINDS:
        if _boolean_target(value):
            locator.check()
        else:
            locator.uncheck()
        _commit_value(locator, dispatch_value_events=False)
        return selector

    if kind in _SELECT_KINDS:
        matched = _matched_option(control, value)
        target_text = str((matched or {}).get("text") or value).strip()
        locator.click()
        _click_unique_visible_text(page, target_text)
        _commit_value(locator, dispatch_value_events=False)
        return selector

    if kind in _RADIO_KINDS:
        raise ValueError("radio 必须按同一 semantic field 的完整 radio group 执行，拒绝单个 radio 猜测。")

    raise ValueError(f"暂不支持 Makro live 控件类型：{kind}")


def _safe_inner_text(locator: Any, timeout_ms: int) -> str:
    try:
        return str(locator.inner_text(timeout=timeout_ms) or "").strip()
    except TypeError:
        return str(locator.inner_text() or "").strip()


def read_control(
    page: Any,
    control: dict[str, Any],
    section_path: str | None = None,
    *,
    timeout_ms: int = 15_000,
) -> str:
    locator, _ = _single_locator(page, control, section_path)
    kind = str(control.get("field_kind") or "input").casefold()

    if kind == "select":
        return locator.locator("option:checked").inner_text(timeout=timeout_ms).strip()
    if kind in _BOOLEAN_KINDS:
        return "true" if locator.is_checked() else "false"
    if kind in _RADIO_KINDS:
        checked = False
        is_checked = getattr(locator, "is_checked", None)
        if callable(is_checked):
            checked = bool(is_checked())
        else:
            checked = str(locator.get_attribute("aria-checked") or "").casefold() == "true"
        if not checked:
            return ""
        raw_value = locator.get_attribute("value")
        return str(raw_value or control.get("value") or control.get("label") or "true").strip()
    if kind == "contenteditable":
        return _safe_inner_text(locator, timeout_ms)
    if kind in _SELECT_KINDS:
        value = locator.get_attribute("value", timeout=timeout_ms)
        return (str(value) if value is not None else _safe_inner_text(locator, timeout_ms)).strip()
    if kind == "custom_slider":
        for attribute in ("aria-valuenow", "value"):
            value = locator.get_attribute(attribute)
            if value is not None:
                return str(value).strip()
        return _safe_inner_text(locator, timeout_ms)

    try:
        return locator.input_value(timeout=timeout_ms).strip()
    except Exception:
        value = locator.get_attribute("value")
        if value is not None:
            return str(value).strip()
        return _safe_inner_text(locator, timeout_ms)


def values_equivalent(control: dict[str, Any], expected: object, actual: object) -> bool:
    if _norm(expected) == _norm(actual):
        return True

    if str(control.get("field_kind") or "").casefold() in _BOOLEAN_KINDS:
        try:
            return _boolean_target(expected) == _boolean_target(actual)
        except ValueError:
            return False

    if is_numeric_control(control):
        try:
            left = float(str(expected).strip())
            right = float(str(actual).strip())
            return math.isfinite(left) and math.isfinite(right) and left == right
        except (TypeError, ValueError):
            return False

    options = control.get("options") or []
    expected_key = _norm(expected)
    actual_key = _norm(actual)
    for option in options:
        aliases = _option_aliases(option)
        if expected_key in aliases and actual_key in aliases:
            return True
    return False


def _radio_aliases(control: dict[str, Any], locator: Any | None = None) -> set[str]:
    aliases = {
        normalized
        for normalized in (
            _norm(control.get("value")),
            _norm(control.get("label")),
            _norm(control.get("aria_label")),
            _norm(control.get("id")),
        )
        if normalized
    }
    if locator is not None:
        try:
            value = locator.get_attribute("value")
        except Exception:
            value = None
        if _norm(value):
            aliases.add(_norm(value))
    return aliases


def fill_radio_group(
    page: Any,
    controls: list[dict[str, Any]],
    value: str,
    section_path: str | None = None,
) -> str:
    target = _norm(value)
    candidates: list[tuple[Any, str, dict[str, Any]]] = []
    for control in controls:
        locator, selector = _single_locator(page, control, section_path)
        _ensure_writable(locator, control, selector)
        if target in _radio_aliases(control, locator):
            candidates.append((locator, selector, control))
    if len(candidates) != 1:
        raise RuntimeError(
            f"radio 值 {value!r} 匹配到 {len(candidates)} 个 live radio，期望恰好 1 个；拒绝猜测。"
        )
    locator, selector, _ = candidates[0]
    check = getattr(locator, "check", None)
    if callable(check):
        check()
    else:
        locator.click()
    _commit_value(locator, dispatch_value_events=False)
    return selector


def read_radio_group(
    page: Any,
    controls: list[dict[str, Any]],
    section_path: str | None = None,
) -> str:
    selected: list[str] = []
    for control in controls:
        locator, _ = _single_locator(page, control, section_path)
        is_checked = getattr(locator, "is_checked", None)
        if callable(is_checked):
            checked = bool(is_checked())
        else:
            checked = str(locator.get_attribute("aria-checked") or "").casefold() == "true"
        if not checked:
            continue
        try:
            raw_value = locator.get_attribute("value")
        except Exception:
            raw_value = None
        selected.append(str(raw_value or control.get("value") or control.get("label") or "true").strip())
    if len(selected) != 1:
        raise RuntimeError(f"radio group 当前选中数量={len(selected)}，期望恰好 1 个。")
    return selected[0]


def radio_group_values_equivalent(
    controls: list[dict[str, Any]], expected: object, actual: object
) -> bool:
    if _norm(expected) == _norm(actual):
        return True
    expected_key = _norm(expected)
    actual_key = _norm(actual)
    return any(expected_key in _radio_aliases(control) and actual_key in _radio_aliases(control) for control in controls)


__all__ = [
    "FieldExecutionContract",
    "execution_contract",
    "fill_control",
    "fill_radio_group",
    "is_numeric_control",
    "is_radio_control",
    "is_radio_group",
    "radio_group_values_equivalent",
    "read_control",
    "read_radio_group",
    "values_equivalent",
]
