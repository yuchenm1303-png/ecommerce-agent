from __future__ import annotations

from playwright.sync_api import Page

from .models import PageField


TRUE_VALUES = {"1", "true", "yes", "y", "是", "有", "勾选", "checked"}


def _as_bool(value: str) -> bool:
    return value.strip().lower() in TRUE_VALUES


def fill_field(page: Page, field: PageField, answer: str) -> None:
    control = page.locator(field.selector).first
    control.wait_for(state="visible")

    if field.control_type == "text":
        control.fill(answer)
        return

    if field.control_type == "select":
        try:
            control.select_option(label=answer)
        except Exception:
            control.select_option(value=answer)
        return

    if field.control_type == "checkbox":
        should_check = _as_bool(answer)
        if should_check:
            control.check()
        else:
            control.uncheck()
        return

    raise ValueError(f"暂不支持控件类型：{field.control_type}")
