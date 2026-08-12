"""Deterministic Makro-specific value constraints applied before Step 3 writes.

These rules do not re-interpret product evidence and never call AI. They only
normalize already-resolved values when Makro itself imposes a mechanical value
constraint that can be evaluated from the decision packet identity.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from ..ai_decisions import MISSING, READY, REVIEW, AIDecisionPacket, field_id


def _is_model_name_field(field: dict[str, Any]) -> bool:
    key = str(field.get("attribute_key") or "").strip().casefold()
    label = re.sub(r"[^a-z0-9]+", " ", str(field.get("label") or "").casefold()).strip()
    return key == "model_name" or label == "model name"


def _strip_known_brand(value: str, brand: str) -> str:
    """Remove the known brand token without inventing replacement product data."""

    raw = re.sub(r"\s+", " ", value).strip()
    token = re.sub(r"\s+", " ", brand).strip()
    if not raw or not token:
        return raw

    # Makro rejects the brand as part of Model Name. A leading brand is the
    # common shape ("Brand X100" / "Brand Air Purifier"), so remove it even
    # when the following model starts with a digit and therefore has no regex
    # word boundary.
    if raw.casefold().startswith(token.casefold()):
        raw = raw[len(token) :]
    else:
        pattern = re.compile(
            rf"(?<![0-9A-Za-z]){re.escape(token)}(?![0-9A-Za-z])",
            re.IGNORECASE,
        )
        raw = pattern.sub(" ", raw)

    raw = re.sub(r"\s+", " ", raw).strip()
    return raw.strip(" -_/|,:;")


def apply_makro_decision_constraints(
    packet: AIDecisionPacket,
    fields: Iterable[dict[str, Any]],
) -> dict[str, int]:
    """Apply fail-closed Makro value normalization to a validated AI packet.

    Only Model Name currently needs such a rule: Makro rejects values containing
    the already-known Brand. We remove that known token mechanically while
    preserving the AI-selected remainder and its citations. If no meaningful
    remainder survives, the decision is downgraded to MISSING rather than
    inventing a model name.
    """

    brand = str(packet.identity.brand or "").strip()
    if not brand:
        return {"model_name_brand_removed": 0, "model_name_blocked": 0}

    by_id = {field_id(field): field for field in fields}
    removed = 0
    blocked = 0

    for decision in packet.decisions:
        target = by_id.get(decision.field_id)
        if target is None or not _is_model_name_field(target):
            continue
        if decision.status not in {READY, REVIEW} or not decision.values:
            continue

        cleaned = [_strip_known_brand(value, brand) for value in decision.values]
        changed = cleaned != decision.values
        if not changed:
            continue

        meaningful = [value for value in cleaned if value]
        if not meaningful:
            decision.status = MISSING
            decision.values = []
            decision.qualifier = ""
            decision.citations = []
            decision.alternatives = []
            decision.reason = (
                "Makro Model Name cannot contain Brand; removing the known brand "
                "left no model value, so the field was downgraded to MISSING."
            )
            packet.warnings.append(
                f"{decision.field_id}: model_name contained only known brand {brand!r}; downgraded to MISSING"
            )
            blocked += 1
            continue

        decision.values = meaningful
        suffix = "Makro Model Name brand token removed mechanically."
        decision.reason = f"{decision.reason} | {suffix}" if decision.reason else suffix
        packet.warnings.append(
            f"{decision.field_id}: removed known brand {brand!r} from model_name before Makro execution"
        )
        removed += 1

    return {
        "model_name_brand_removed": removed,
        "model_name_blocked": blocked,
    }
