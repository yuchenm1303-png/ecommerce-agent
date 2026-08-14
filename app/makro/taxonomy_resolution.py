"""Semantic policy for Makro taxonomy fallback.

Makro's live taxonomy is sometimes sparse or commercially coarse. Navigation
therefore prefers genuine taxonomy ancestry but may traverse the most plausible
retail branch when no exact ancestry exists. The final leaf is accepted when it
is the same product type, a genuine broader class, or the closest reasonable
best-fit live class selected by AI from the real Makro taxonomy.
"""

from __future__ import annotations

import json
from typing import Any

from .listing_creation import JSONTaskProvider, ListingBootstrapHints, normalize_label


_ANCESTOR_BRANCH = "ancestor_branch"
_BEST_AVAILABLE_BRANCH = "best_available_branch"
_SAME_PRODUCT_TYPE = "same_product_type"
_BROADER_VALID_CLASS = "broader_valid_class"
_BEST_AVAILABLE_FIT = "best_available_fit"
_NO_VALID_CLASS = "none"
_PATH_RELATIONS = {
    _ANCESTOR_BRANCH,
    _BEST_AVAILABLE_BRANCH,
    _SAME_PRODUCT_TYPE,
    _BROADER_VALID_CLASS,
    _NO_VALID_CLASS,
}
_LEAF_RELATIONS = {
    _SAME_PRODUCT_TYPE,
    _BROADER_VALID_CLASS,
    _BEST_AVAILABLE_FIT,
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
    allowed = list(
        dict.fromkeys(
            str(item or "").strip()
            for item in candidates
            if str(item or "").strip()
        )
    )
    return {
        "task": "choose_safe_makro_taxonomy_node",
        "system_instruction": (
            "Choose at most one exact live Makro taxonomy node for a physical product. Makro taxonomy "
            "may be sparse: prefer a genuine ancestor branch, but when none exists choose the most "
            "plausible retail branch that can lead to the closest usable live category. JSON only."
        ),
        "prompt_instruction": (
            "Evaluate context.current_path + each live node as a marketplace breadcrumb. Prefer strict "
            "taxonomy ancestry; otherwise choose the branch a marketplace operator would most reasonably "
            "explore for a best-fit listing category."
        ),
        "context": {
            "product_summary": hints.product_summary,
            "product_identity": _identity(hints),
            "current_path": list(current_path),
            "live_nodes": allowed,
        },
        "rules": [
            "selected_node must be copied exactly from live_nodes or be empty.",
            "Use ancestor_branch for a genuine marketplace department/product-family ancestor.",
            "Use same_product_type when the node already names the same physical product class.",
            "Use broader_valid_class when the node is a genuine semantic superclass.",
            "When no strict ancestor exists, use best_available_branch for the live branch most likely to contain the closest commercially reasonable leaf.",
            "For best_available_branch, judge retail context, buyer expectation and defining function; do not choose a branch merely because one vague word overlaps.",
            "Physical containment alone is not evidence: a product fitting inside a container, case, bag, vehicle or room does not make that object a good category branch.",
            "Accessories, consumables and spare parts should not be preferred when a branch representing the sold product itself is materially closer.",
            "Judge the complete breadcrumb, not an isolated word.",
            "Return none only when no current live branch is even a reasonable route to a usable best-fit category.",
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
                        _BEST_AVAILABLE_BRANCH,
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
        raise ValueError(
            f"AI returned a taxonomy node that is not one unique live candidate: {selected!r}"
        )
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
            "Evaluate one already reached real Makro taxonomy leaf against the grounded physical product. "
            "Makro taxonomy may not contain an exact class, so a closest practical live best-fit is allowed. JSON only."
        ),
        "prompt_instruction": (
            "Classify the full breadcrumb as same product type, genuine broader class, closest available "
            "best-fit, or unusable. Do not require perfect taxonomy alignment when Makro does not offer it."
        ),
        "context": {
            "product_summary": hints.product_summary,
            "product_identity": _identity(hints),
            "taxonomy_breadcrumb": path,
            "leaf": leaf,
        },
        "rules": [
            "Use same_product_type for the same physical product class.",
            "Use broader_valid_class for a genuine merchandise superclass.",
            "Use best_available_fit when the leaf is not a strict superclass but is the most commercially reasonable live Makro category available for this product.",
            "A best_available_fit may differ in form, use-case or specificity when Makro has no exact class; describe those tradeoffs in unsupported_defining_constraints and reason instead of automatically rejecting it.",
            "Prefer shared defining function, normal retail context and buyer expectation over literal word overlap.",
            "Do not use best_available_fit for a plainly unrelated class when a meaningfully closer live category exists.",
            "unsupported_defining_constraints is diagnostic: list meaningful mismatches so logs show the compromise.",
            "Use none only when this leaf would severely misrepresent the sold product even as a marketplace fallback.",
        ],
        "json_contract": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "selection_relation": {
                    "type": "string",
                    "enum": [
                        _SAME_PRODUCT_TYPE,
                        _BROADER_VALID_CLASS,
                        _BEST_AVAILABLE_FIT,
                        _NO_VALID_CLASS,
                    ],
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

    if relation == _BEST_AVAILABLE_FIT:
        accepted = True
    elif relation in {_SAME_PRODUCT_TYPE, _BROADER_VALID_CLASS}:
        accepted = not unsupported
    else:
        accepted = False

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
