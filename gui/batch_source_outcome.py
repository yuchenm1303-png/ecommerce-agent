from __future__ import annotations

from typing import Any


def source_media_review(outcome: dict[str, Any] | None) -> tuple[str, str, str] | None:
    """Return REVIEW presentation fields for exhausted mandatory source media."""

    payload = outcome if isinstance(outcome, dict) else {}
    if str(payload.get("failure_kind") or "").strip().upper() != "LISTING_MEDIA_UNAVAILABLE":
        return None
    reason = str(payload.get("failure_reason") or "").strip()
    return (
        "Source 图片验收",
        reason or "供应商页面没有留下可提交的商品图片。",
        "缺少可提交商品图片",
    )


__all__ = ["source_media_review"]
