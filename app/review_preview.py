from __future__ import annotations

from .answer_resolver import RESOLVED, ResolvedAnswer
from .fill_plan import READY, LiveFillPlanItem


class ReviewPreviewBlocked(ValueError):
    """Raised when a fill-plan item is not allowed into browser preview execution."""


def preview_mode_for_item(
    item: LiveFillPlanItem,
    *,
    include_review_candidates: bool,
) -> str | None:
    """Return the explicit preview mode for an item, or ``None`` when blocked.

    ``ready`` items are already eligible for normal autofill. ``review`` items
    are *not* autofill-safe; they are accepted only when the resolver marked
    them ``preview_eligible`` because low confidence was the sole blocking gate.
    Conflicts, missing evidence, unmatched-field collisions, field-constraint
    failures and missing provenance can never enter review preview through this
    function.
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
    """Create the narrow execution view consumed by the browser fill layer.

    The browser executor intentionally accepts only ``ResolvedAnswer(status=resolved)``.
    Rather than weakening that executor, this boundary performs the explicit
    review-preview authorization first and then creates a resolved execution
    copy. The original ``ResolutionRecord`` remains unchanged and still reports
    ``needs_review`` / ``eligible_for_autofill=False`` for audit purposes.
    """

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
            f"browser preview execution copy; mode={mode}; "
            f"original_status={record.status}; gate_reason={record.gate_reason or 'none'}"
        ),
    )
