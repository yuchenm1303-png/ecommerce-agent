from __future__ import annotations

from playwright.sync_api import Page

from .models import PageField


TRUE_VALUES = {"1", "true", "yes", "y", "是", "有", "勾选", "checked"}


def _normalize(value: str) -> str:
    return " ".join(value.strip().split())


def read_field_value(page: Page, field: PageField) -> str:
    control = page.locator(field.selector).first

    if field.control_type == "text":
        return control.input_value().strip()

    if field.control_type == "select":
        option = control.locator("option:checked").first
        return option.inner_text().strip()

    if field.control_type == "checkbox":
        return "true" if control.is_checked() else "false"

    raise ValueError(f"暂不支持校验控件类型：{field.control_type}")


def validate_field(page: Page, field: PageField, expected: str) -> tuple[bool, str]:
    actual = read_field_value(page, field)

    if field.control_type == "checkbox":
        expected_bool = expected.strip().lower() in TRUE_VALUES
        actual_bool = actual == "true"
        return expected_bool == actual_bool, actual

    return _normalize(expected) == _normalize(actual), actual
