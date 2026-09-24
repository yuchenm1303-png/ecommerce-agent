from __future__ import annotations

from typing import Any

from .listing import parse_makro_listing_url


DRAFT_IDENTITY_FIELD = "_listing_draft_identity"


def listing_draft_identity_from_url(url: str) -> dict[str, str]:
    """Build a short-lived ownership identity for one Makro listing draft.

    ``requestId`` is intentionally treated only as a prepare->execute nonce. It
    is not a stable product identifier and must never be persisted as catalogue
    semantics. ``vertical`` and ``brand`` are normalized only for comparison;
    ``requestId`` and ``vid`` remain exact opaque portal tokens.
    """

    target = parse_makro_listing_url(str(url or "").strip())
    request_id = str(target.request_id or "").strip()
    if not request_id:
        raise RuntimeError(
            "Makro listing URL does not expose requestId; refusing to create an unowned live schema"
        )
    return {
        "request_id": request_id,
        "vid": str(target.vid or "").strip(),
        "vertical": str(target.vertical or "").strip().casefold(),
        "brand": str(target.brand or "").strip().casefold(),
    }


def normalized_listing_draft_identity(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    request_id = str(value.get("request_id") or "").strip()
    if not request_id:
        return None
    return {
        "request_id": request_id,
        "vid": str(value.get("vid") or "").strip(),
        "vertical": str(value.get("vertical") or "").strip().casefold(),
        "brand": str(value.get("brand") or "").strip().casefold(),
    }


def assert_same_listing_draft(prepared: Any, current: Any) -> None:
    expected = normalized_listing_draft_identity(prepared)
    actual = normalized_listing_draft_identity(current)
    if expected is None:
        raise RuntimeError("prepared live schema is missing Makro draft ownership identity")
    if actual is None:
        raise RuntimeError("current Makro page is missing draft ownership identity")
    if expected != actual:
        raise RuntimeError(
            "current Makro page is not the draft that produced this live schema; "
            f"prepared={expected!r}, current={actual!r}"
        )


__all__ = [
    "DRAFT_IDENTITY_FIELD",
    "assert_same_listing_draft",
    "listing_draft_identity_from_url",
    "normalized_listing_draft_identity",
]
