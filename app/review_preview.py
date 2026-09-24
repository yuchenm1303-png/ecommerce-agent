from __future__ import annotations

from .fill_plan import READY, LiveFillPlanItem
from .resolution_types import RESOLVED, ResolvedAnswer


class ReviewPreviewBlocked(ValueError):
    """Raised when a fill-plan item is not allowed into browser preview execution."""


def preview_mode_for_item(
    item: LiveFillPlanItem,
    *,
    include_review_candidates: bool,
) -> str | None:
    """Return the explicit execution mode for a plan item, or ``None``.

    READY items are autofill-safe. REVIEW items enter browser preview only after
    explicit opt-in. CONFLICT/MISSING/business/hard-constraint failures never
    pass this boundary.
    """

    if item.action == READY and item.resolution.eligible_for_autofill:
        return "ready"
    if include_review_candidates and item.resolution.preview_eligible:
        return "review"
    return None


def execution_answer_for_item(
    item: LiveFillPlanItem,
    *,
    include_review_candidates: bool,
) -> ResolvedAnswer:
    """Create the narrow, product-semantic-free view consumed by the browser."""

    mode = preview_mode_for_item(
        item,
        include_review_candidates=include_review_candidates,
    )
    if mode is None:
        raise ReviewPreviewBlocked(
            f"字段 {item.label or item.attribute_key!r} 不允许进入浏览器 review preview。"
        )

    record = item.resolution
    if not record.answer_values:
        raise ReviewPreviewBlocked(
            f"字段 {item.label or item.attribute_key!r} 没有可执行 answer_values。"
        )

    return ResolvedAnswer(
        attribute_key=record.attribute_key,
        label=record.label,
        status=RESOLVED,
        answer=record.answer,
        answer_values=list(record.answer_values),
        qualifier=record.qualifier,
        source_type=record.source_type,
        source_reference=record.source_reference,
        evidence=record.evidence,
        confidence=record.confidence,
        detail=(
            f"browser execution copy; mode={mode}; "
            f"original_status={record.status}; gate_reason={record.gate_reason or 'none'}"
        ),
    )
