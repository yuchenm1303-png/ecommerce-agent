"""Single-open visual hold for Makro synthetic coverage.

This mode is intentionally different from the normal per-field coverage runner:
it opens one section once, fills every currently empty semantic field with the
same safe synthetic strategies, verifies that all successful values still coexist
after the final React render, and then leaves the section open for human visual
inspection. The caller must eventually Cancel; this module never Save/Submit.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .coverage import (
    FAIL,
    PASS,
    SKIPPED_EXISTING,
    CoverageResult,
    _equivalent,
    _match_field,
    _qualifier_controls,
    _read_control,
    _require_collapsed_section,
    _unique_visible_locator,
    _value_controls,
    cancel_section,
    exercise_live_field,
    field_shape,
    semantic_field_is_empty,
)

ResultCallback = Callable[[CoverageResult, int, int], None]


def _verify_final_hold(
    adapter: Any,
    section_title: str,
    section_path: str,
    results: list[CoverageResult],
    *,
    wait_ms: int,
    max_scroll_steps: int,
) -> None:
    """Prove every PASS value is still present after all fields were filled.

    Per-field readback alone is not enough for visual-hold mode: a later React
    update could theoretically reset an earlier field. This final full-section
    pass re-discovers the live DOM and re-reads every successful primary value
    (plus its qualifier when present) before the user is invited to inspect it.
    """

    controls = adapter.scan_section_fields(
        section_path,
        include_values=True,
        wait_ms=wait_ms,
        max_scroll_steps=max_scroll_steps,
    )
    fields = adapter.build_semantic_fields(controls)

    for result in results:
        if result.status != PASS:
            continue
        field_item = _match_field(fields, result.attribute_key, result.label)
        if field_item is None:
            result.status = FAIL
            result.detail = "最终整页复核时无法重新发现该字段。"
            continue

        expected = list(result.candidate)
        values = _value_controls(field_item)
        qualifiers = _qualifier_controls(field_item)
        if not values or not expected:
            result.status = FAIL
            result.detail = "最终整页复核缺少主值控件或候选值。"
            continue

        try:
            primary_locator, primary_selector = _unique_visible_locator(
                adapter.page, section_path, values[0]
            )
            primary_actual = _read_control(primary_locator, values[0])
            if primary_selector not in result.selectors:
                result.selectors.append(primary_selector)
            if not _equivalent(expected[0], primary_actual, values[0]):
                result.status = FAIL
                result.detail = (
                    "最终整页复核发现主值被后续 React 更新重置："
                    f"expected={expected[0]!r}, actual={primary_actual!r}"
                )
                continue

            if qualifiers:
                if len(expected) < 2:
                    result.status = FAIL
                    result.detail = "最终整页复核发现 qualifier 缺少预期候选值。"
                    continue
                qualifier_locator, qualifier_selector = _unique_visible_locator(
                    adapter.page, section_path, qualifiers[0]
                )
                qualifier_actual = _read_control(qualifier_locator, qualifiers[0])
                if qualifier_selector not in result.selectors:
                    result.selectors.append(qualifier_selector)
                if not _equivalent(expected[1], qualifier_actual, qualifiers[0]):
                    result.status = FAIL
                    result.detail = (
                        "最终整页复核发现 qualifier 被后续 React 更新重置："
                        f"expected={expected[1]!r}, actual={qualifier_actual!r}"
                    )
                    continue

            result.detail = (
                "控件立即/延迟回读通过；全部字段填完后的整页复核仍保持该值。"
            )
        except Exception as exc:
            result.status = FAIL
            result.detail = f"最终整页复核失败：{exc}"


def fill_section_for_visual_hold(
    adapter: Any,
    section_title: str,
    *,
    recheck_wait_ms: int = 800,
    wait_ms: int = 250,
    max_scroll_steps: int = 200,
    on_result: ResultCallback | None = None,
) -> list[CoverageResult]:
    """Fill all currently empty fields while keeping one section open.

    Safety invariants:
    - the section must be collapsed before starting, so no unsaved user edits
      can be discarded accidentally;
    - existing non-placeholder values are never overwritten;
    - no multi-value ``+`` slots are created here (normal coverage already tests
      that capability; visual hold is for seeing all *existing* empty fields
      filled simultaneously);
    - on unexpected exceptions this function immediately Cancels before raising;
    - on success it deliberately leaves the section expanded so the caller/user
      can inspect it, and the caller is responsible for a final Cancel.
    """

    section = _require_collapsed_section(adapter, section_title)
    adapter.open_section_for_edit(section)

    try:
        live = adapter.find_section(section_title) or section
        section_path = str(live.get("path") or "")
        if not section_path:
            raise RuntimeError("section 缺少 DOM path，无法进入 visual-hold。")
        if live.get("has_edit"):
            raise RuntimeError("点击 EDIT 后 section 仍处于折叠状态。")

        controls = adapter.scan_section_fields(
            section_path,
            include_values=True,
            wait_ms=wait_ms,
            max_scroll_steps=max_scroll_steps,
        )
        fields = adapter.build_semantic_fields(controls)

        targets: list[dict[str, Any]] = []
        results: list[CoverageResult] = []
        for field_item in fields:
            if semantic_field_is_empty(field_item):
                targets.append(field_item)
            else:
                results.append(
                    CoverageResult(
                        section=str(field_item.get("section_heading") or section_title),
                        subsection=str(field_item.get("subsection_heading") or ""),
                        attribute_key=str(field_item.get("attribute_key") or ""),
                        label=str(field_item.get("label") or ""),
                        shape=field_shape(field_item),
                        status=SKIPPED_EXISTING,
                        detail="当前字段已有非 placeholder 值；visual-hold 不覆盖已有数据。",
                    )
                )

        total = len(targets)
        for ordinal, field_item in enumerate(targets, start=1):
            result = exercise_live_field(
                adapter,
                field_item,
                section_path,
                ordinal,
                recheck_wait_ms=recheck_wait_ms,
                exercise_multi_value=False,
                wait_ms=wait_ms,
                max_scroll_steps=max_scroll_steps,
            )
            results.append(result)
            if on_result is not None:
                on_result(result, ordinal, total)

        # One final whole-section re-discovery proves earlier values did not get
        # reset while later fields were being filled.
        adapter.page.wait_for_timeout(recheck_wait_ms)
        _verify_final_hold(
            adapter,
            section_title,
            section_path,
            results,
            wait_ms=wait_ms,
            max_scroll_steps=max_scroll_steps,
        )
        return results
    except BaseException:
        # Fail closed. Even KeyboardInterrupt during population must not leave
        # synthetic values sitting in the real seller draft.
        try:
            cancel_section(adapter, section_title)
        finally:
            raise


def cleanup_visual_hold_section(
    adapter: Any,
    section_title: str,
    *,
    wait_ms: int = 450,
) -> bool:
    """Cancel visual-hold values, tolerating an already-collapsed section.

    Returns True when this call clicked Cancel, False when the user had already
    collapsed the section manually. It never clicks Save / Send to QC.
    """

    section = adapter.find_section(section_title)
    if section is None:
        raise RuntimeError(f"清理 visual-hold 时找不到 section：{section_title}")
    if section.get("has_edit"):
        return False
    cancel_section(adapter, section_title, wait_ms=wait_ms)
    return True
