"""Semantic safety for Makro taxonomy fallback.

Taxonomy navigation may traverse broad parent branches, but every selected node
must be a real marketplace-taxonomy relation to the product. Physical containment
("the product can fit inside it") or incidental usage is never category ancestry.
The final leaf receives a second independent semantic gate before Step 2.
"""

from __future__ import annotations

import json
from typing import Any

from .listing_creation import JSONTaskProvider, ListingBootstrapHints, normalize_label


_ANCESTOR_BRANCH = "ancestor_branch"
_SAME_PRODUCT_TYPE = "same_product_type"
_BROADER_VALID_CLASS = "broader_valid_class"
_NO_VALID_CLASS = "none"
_PATH_RELATIONS = {
    _ANCESTOR_BRANCH,
    _SAME_PRODUCT_TYPE,
    _BROADER_VALID_CLASS,
    _NO_VALID_CLASS,
}
_LEAF_RELATIONS = {
    _SAME_PRODUCT_TYPE,
    _BROADER_VALID_CLASS,
    _NO_VALID_CLASS,
}


def _diag(event: str, payload: dict[str, Any]) -> None:
    print(
        "MAKRO_VERTICAL_DIAG "
        + json.dumps(
            {"event": event, **payload},
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ),
        flush=True,
    )


def _identity(hints: ListingBootstrapHints) -> dict[str, Any]:
    return dict(hints.product_identity or {})


def build_taxonomy_path_choice_request(
    hints: ListingBootstrapHints,
    current_path: list[str],
    candidates: list[str],
) -> dict[str, Any]:
    allowed = list(dict.fromkeys(str(item or "").strip() for item in candidates if str(item or "").strip()))
    return {
        "task": "choose_safe_makro_taxonomy_node",
        "system_instruction": (
            "Choose at most one exact live Makro taxonomy node for a physical product. "
            "The relationship is marketplace category ancestry, never physical containment or incidental use. JSON only."
        ),
        "prompt_instruction": (
            "Evaluate context.current_path + each live node as a marketplace taxonomy breadcrumb. "
            "Choose a node only when that branch genuinely categorizes the product or leads toward its product class."
        ),
        "context": {
            "product_summary": hints.product_summary,
            "product_identity": _identity(hints),
            "current_path": list(current_path),
            "live_nodes": allowed,
        },
        "rules": [
            "selected_node must be copied exactly from live_nodes or be empty.",
            "selection_relation describes the selected node's taxonomy relation to the product: ancestor_branch, same_product_type, broader_valid_class, or none.",
            "ancestor_branch is allowed only for a genuine marketplace department/product-family branch under which this product would normally be sold.",
            "Physical containment is irrelevant: a product fitting inside a container, case, box, bag, cabinet, vehicle, room, or holder does not make that object a taxonomy ancestor.",
            "Incidental compatibility or usage is irrelevant: accessories, consumables, spare parts, adjacent tools and sibling products are not ancestor branches.",
            "same_product_type means the node describes the same physical product class.",
            "broader_valid_class means the node is a genuine semantic superclass of the physical product without adding a different defining purpose, mechanism or form.",
            "Judge the complete breadcrumb, not an isolated word. A generic-looking leaf under an incompatible department must be rejected.",
            "Prefer the branch matching the product's defining purpose and normal retail context, not a branch sharing a vague noun.",
            "If no live node is a genuine ancestor/same/broader class, return selected_node='' and selection_relation='none'.",
        ],
        "json_contract": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "selected_node": {"type": "string", "enum": ["", *allowed]},
                "selection_relation": {
                    "type": "string",
                    "enum": [
                        _ANCESTOR_BRANCH,
                        _SAME_PRODUCT_TYPE,
                        _BROADER_VALID_CLASS,
                        _NO_VALID_CLASS,
                    ],
                },
            },
            "required": ["selected_node", "selection_relation"],
        },
        "strict_json_schema": True,
    }


def choose_taxonomy_path_candidate(
    provider: JSONTaskProvider,
    hints: ListingBootstrapHints,
    current_path: list[str],
    candidates: list[str],
) -> str:
    if not candidates:
        return ""
    request = build_taxonomy_path_choice_request(hints, current_path, candidates)
    raw = provider.extract_json(request)
    if not isinstance(raw, dict):
        raise ValueError("taxonomy path chooser response must be a JSON object")

    selected = str(raw.get("selected_node") or "").strip()
    relation = str(raw.get("selection_relation") or "").strip()
    if relation not in _PATH_RELATIONS:
        raise ValueError(f"invalid taxonomy selection_relation={relation!r}")
    if not selected:
        if relation != _NO_VALID_CLASS:
            raise ValueError("empty taxonomy selection requires selection_relation='none'")
        _diag(
            "taxonomy_path_decision",
            {
                "current_path": list(current_path),
                "candidates": list(candidates),
                "selected_node": "",
                "selection_relation": relation,
            },
        )
        return ""
    if relation == _NO_VALID_CLASS:
        raise ValueError("non-empty taxonomy selection cannot use selection_relation='none'")

    wanted = normalize_label(selected)
    matches = [item for item in candidates if normalize_label(item) == wanted]
    if len(matches) != 1:
        raise ValueError(f"AI returned a taxonomy node that is not one unique live candidate: {selected!r}")
    selected = matches[0]
    _diag(
        "taxonomy_path_decision",
        {
            "current_path": list(current_path),
            "candidates": list(candidates),
            "selected_node": selected,
            "selection_relation": relation,
        },
    )
    return selected


def build_taxonomy_leaf_validation_request(
    hints: ListingBootstrapHints,
    breadcrumb: list[str],
) -> dict[str, Any]:
    path = [str(item or "").strip() for item in breadcrumb if str(item or "").strip()]
    leaf = path[-1] if path else ""
    return {
        "task": "validate_makro_taxonomy_leaf",
        "system_instruction": (
            "Validate one already reached Makro taxonomy leaf against the grounded physical product identity. "
            "This is a fail-closed safety gate before Step 2. JSON only."
        ),
        "prompt_instruction": (
            "Classify whether the full taxonomy breadcrumb is the same product type or a genuine broader valid class. "
            "Reject sibling products, physical containers, accessories and any class that adds unsupported defining constraints."
        ),
        "context": {
            "product_summary": hints.product_summary,
            "product_identity": _identity(hints),
            "taxonomy_breadcrumb": path,
            "leaf": leaf,
        },
        "rules": [
            "selection_relation must be same_product_type, broader_valid_class, or none.",
            "A valid broader class must semantically contain the product as a merchandise class, not merely physically contain, store, carry or be used with it.",
            "Reject a concrete sibling or adjacent product even when it shares one word or appears under a nearby department.",
            "Reject a leaf that introduces a defining mechanism, purpose, form, audience or product type not grounded by product_identity.",
            "unsupported_defining_constraints must list every such unsupported defining constraint; it must be empty for an accepted leaf.",
            "If the breadcrumb is not clearly valid, use selection_relation='none'.",
        ],
        "json_contract": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "selection_relation": {
                    "type": "string",
                    "enum": [_SAME_PRODUCT_TYPE, _BROADER_VALID_CLASS, _NO_VALID_CLASS],
                },
                "unsupported_defining_constraints": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "reason": {"type": "string"},
            },
            "required": [
                "selection_relation",
                "unsupported_defining_constraints",
                "reason",
            ],
        },
        "strict_json_schema": True,
    }


def validate_taxonomy_leaf_candidate(
    provider: JSONTaskProvider,
    hints: ListingBootstrapHints,
    breadcrumb: list[str],
) -> bool:
    raw = provider.extract_json(build_taxonomy_leaf_validation_request(hints, breadcrumb))
    if not isinstance(raw, dict):
        raise ValueError("taxonomy leaf validator response must be a JSON object")
    relation = str(raw.get("selection_relation") or "").strip()
    if relation not in _LEAF_RELATIONS:
        raise ValueError(f"invalid taxonomy leaf selection_relation={relation!r}")
    unsupported = [
        str(item or "").strip()
        for item in (raw.get("unsupported_defining_constraints") or [])
        if str(item or "").strip()
    ]
    accepted = relation in {_SAME_PRODUCT_TYPE, _BROADER_VALID_CLASS} and not unsupported
    _diag(
        "taxonomy_leaf_validation",
        {
            "breadcrumb": list(breadcrumb),
            "selection_relation": relation,
            "unsupported_defining_constraints": unsupported,
            "reason": str(raw.get("reason") or "").strip(),
            "accepted": accepted,
        },
    )
    return accepted


__all__ = [
    "build_taxonomy_leaf_validation_request",
    "build_taxonomy_path_choice_request",
    "choose_taxonomy_path_candidate",
    "validate_taxonomy_leaf_candidate",
]
