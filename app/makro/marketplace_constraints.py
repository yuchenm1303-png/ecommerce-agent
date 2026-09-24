"""Compatibility boundary for Makro decision handling.

Product semantics belong exclusively to the Resolver.  Once an AI decision packet
has been validated, Makro-specific Python code must not rewrite, normalize,
translate, strip, downgrade, or otherwise reinterpret its values.

This module intentionally keeps the historical public helpers because execution
and older callers import them, but both helpers are semantic no-ops.  Mechanical
browser failures are handled later by the executor as execution failures rather
than by changing the AI decision.
"""

from __future__ import annotations

from typing import Any, Iterable

from ..ai_decisions import AIDecisionPacket


def _is_model_name_field(field: dict[str, Any]) -> bool:
    """Identify the historical Model Name field for adapter compatibility."""

    key = str(field.get("attribute_key") or "").strip().casefold()
    label = " ".join(str(field.get("label") or "").casefold().split())
    return key == "model_name" or label == "model name"


def _strip_known_brand(value: str, brand: str) -> str:
    """Return the AI-selected value unchanged.

    Kept only for compatibility with the browser adapter.  Makro presentation
    preferences are not authority to mutate product meaning after the Resolver.
    """

    del brand
    return str(value or "").strip()


def apply_makro_decision_constraints(
    packet: AIDecisionPacket,
    fields: Iterable[dict[str, Any]],
) -> dict[str, int]:
    """Preserve a validated AI packet exactly; never post-process semantics."""

    del packet, fields
    return {
        "model_name_brand_removed": 0,
        "model_name_blocked": 0,
    }
