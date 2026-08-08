from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from .source_bundle import ProductSourceBundle, SourceEvidence, normalize_key
from .value_normalization import canonical_evidence_value_for_field, canonical_scalar_for_field


RESOLVED = "resolved"
NEEDS_REVIEW = "needs_review"
MISSING = "missing"
CONFLICT = "conflict"

# These are operational listing fields, not product facts. They must come from
# an explicit table/config/rule source and are never eligible for AI fallback.
BUSINESS_ATTRIBUTE_ALIASES: dict[str, tuple[str, ...]] = {
    "sku_id": ("sku", "sku id", "sku_id", "商品sku", "商品编码"),
    "listing_status": ("listing status", "listing_status", "status", "上架状态"),
    "mrp": ("mrp", "base price", "base_price", "原价", "基础价格"),
    "flipkart_selling_price": (
        "selling price",
        "your selling price",
        "flipkart_selling_price",
        "售价",
        "销售价",
    ),
    "minimum_order_quantity": (
        "minimum order quantity",
        "minmoq",
        "min moq",
        "minimum_order_quantity",
        "最小起订量",
    ),
    "max_order_quantity_allowed": (
        "maximum order quantity",
        "maxoq",
        "max moq",
        "max_order_quantity_allowed",
        "最大订购量",
    ),
    "shipping_days": ("shipping days", "pick pack sla", "shipping_days", "发货天数"),
    "service_profile": (
        "service profile",
        "service_profile",
        "fulfillment by",
        "fulfilment by",
        "fulfillment",
        "fbs",
    ),
    "forbid_shipping": (
        "selling region preference",
        "selling region",
        "shipping region",
        "forbid_shipping",
    ),
}
BUSINESS_ALLOWED_SOURCE_TYPES = {"structured", "business", "config", "rule"}

_SET_LIKE_FIELD_NAMES = {
    "salespackage",
    "technologyused",
    "otherfeatures",
    "otherconveniencefeatures",
    "otherimageandvideofeatures",
    "otherstoragefeatures",
    "otherconnectivityfeatures",
    "recordingmodes",
    "videoformats",
}
_COUNT_FIELD_NAMES = {
    "numberofcameras",
    "numberofcamera",
    "cameracount",
    "numberoflenses",
    "lenscount",
}
_STORAGE_CAPACITY_FIELD_NAMES = {
    "storagecapacity",
    "memorycapacity",
    "storagecapacitygb",
    "memorycapacitygb",
}
_PRODUCT_DIMENSION_FIELD_NAMES = {"width", "depth", "height"}
_PACKAGE_EVIDENCE_MARKERS = (
    "package",
    "packaging",
    "packing size",
    "package size",
    "carton",
    "outer box",
    "包装",
    "包装尺寸",
    "包装规格",
)
_CAPACITY_VALUE_RE = re.compile(r"(?<!\w)\d+(?:\.\d+)?\s*(?:gb|mb|tb)?(?!\w)", re.IGNORECASE)
_EXACT_COUNT_RE = re.compile(r"^\s*(\d+)\s*(?:cameras?|lenses?|镜头|摄像头)?\s*$", re.IGNORECASE)


@dataclass(slots=True)
class ResolvedAnswer:
    attribute_key: str
    label: str
    status: str
    answer: str | None = None
    answer_values: list[str] = field(default_factory=list)
    qualifier: str | None = None
    source_type: str | None = None
    source_reference: str | None = None
    evidence: str | None = None
    confidence: float = 0.0
    option_match: list[dict[str, str]] = field(default_factory=list)
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "attribute_key": self.attribute_key,
            "label": self.label,
            "status": self.status,
            "answer": self.answer,
            "answer_values": self.answer_values,
            "qualifier": self.qualifier,
            "source_type": self.source_type,
            "source_reference": self.source_reference,
            "evidence": self.evidence,
            "confidence": self.confidence,
            "option_match": self.option_match,
            "detail": self.detail,
        }


def _raw_values(value: str | tuple[str, ...], *, multi_value: bool) -> list[str]:
    if isinstance(value, tuple):
        return [item.strip() for item in value if item.strip()]
    text = value.strip()
    if not text:
        return []
    if not multi_value:
        return [text]
    if re.search(r"[|;\n]", text):
        return [item.strip() for item in re.split(r"[|;\n]+", text) if item.strip()]
    if "," in text:
        return [item.strip() for item in text.split(",") if item.strip()]
    return [text]


def _option_norm(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", value.casefold())


def _clean_options(options: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for option in options:
        text = str(option.get("text") or "").strip()
        value = str(option.get("value") or "").strip()
        if not text and not value:
            continue
        if _option_norm(text or value) in {"selectone", "select", "choose", "请选择"}:
            continue
        key = (text, value)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(option)
    return cleaned


def _value_options(semantic_field: dict[str, Any]) -> list[dict[str, Any]]:
    controls = semantic_field.get("controls") or []
    options: list[dict[str, Any]] = []
    for control in controls:
        name = str(control.get("name") or "")
        if name.endswith("_qualifier"):
            continue
        if control.get("field_kind") not in {"select", "dropdown", "autocomplete", "listbox"}:
            continue
        options.extend(control.get("options") or [])
    if not options:
        options = list(semantic_field.get("options") or [])
    return _clean_options(options)


def _qualifier_options(semantic_field: dict[str, Any]) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    for control in semantic_field.get("controls") or []:
        if str(control.get("name") or "").endswith("_qualifier"):
            options.extend(control.get("options") or [])
    return _clean_options(options)


def _match_one_option(value: str, options: list[dict[str, Any]]) -> dict[str, str] | None:
    wanted = _option_norm(value)
    if not wanted:
        return None
    matches: list[dict[str, str]] = []
    for option in options:
        text = str(option.get("text") or "").strip()
        raw_value = str(option.get("value") or "").strip()
        if wanted in {_option_norm(text), _option_norm(raw_value)}:
            matches.append({"input": value, "text": text, "value": raw_value})
    unique = {(item["text"], item["value"]) for item in matches}
    if len(unique) != 1:
        return None
    return matches[0]


def _extract_qualifier(
    values: list[str], qualifier_options: list[dict[str, Any]]
) -> tuple[list[str], str | None, dict[str, str] | None]:
    if not qualifier_options or not values:
        return values, None, None
    if len(values) != 1:
        return values, None, None
    raw = values[0].strip()
    ordered = sorted(
        qualifier_options,
        key=lambda item: max(
            len(str(item.get("text") or "")), len(str(item.get("value") or ""))
        ),
        reverse=True,
    )
    for option in ordered:
        for candidate in (
            str(option.get("text") or "").strip(),
            str(option.get("value") or "").strip(),
        ):
            if not candidate:
                continue
            pattern = re.compile(rf"^(.*?)\s*{re.escape(candidate)}\s*$", re.IGNORECASE)
            match = pattern.match(raw)
            if match and match.group(1).strip():
                option_match = _match_one_option(candidate, qualifier_options)
                if option_match:
                    return [match.group(1).strip()], option_match["text"] or candidate, option_match
    return values, None, None


def _candidate_keys(semantic_field: dict[str, Any]) -> list[str]:
    attribute_key = str(semantic_field.get("attribute_key") or "")
    label = str(semantic_field.get("label") or "")
    keys = [attribute_key, label]
    keys.extend(BUSINESS_ATTRIBUTE_ALIASES.get(attribute_key, ()))
    return [key for key in keys if key]


def _field_names(semantic_field: dict[str, Any]) -> set[str]:
    return {
        normalize_key(semantic_field.get("attribute_key")),
        normalize_key(semantic_field.get("label")),
    } - {""}


def _is_business_field(attribute_key: str) -> bool:
    return attribute_key in BUSINESS_ATTRIBUTE_ALIASES


def _evidence_text(candidate: SourceEvidence) -> str:
    return "\n".join(
        part for part in (candidate.evidence_text, candidate.note) if part
    ).casefold()


def _is_package_dimension_evidence(candidate: SourceEvidence) -> bool:
    text = _evidence_text(candidate)
    return any(marker in text for marker in _PACKAGE_EVIDENCE_MARKERS)


def _has_storage_capacity_value(candidate: SourceEvidence) -> bool:
    values = candidate.value if isinstance(candidate.value, tuple) else (candidate.value,)
    return any(_CAPACITY_VALUE_RE.search(str(value)) for value in values)


def _filter_semantically_incompatible_candidates(
    candidates: list[SourceEvidence],
    semantic_field: dict[str, Any],
) -> list[SourceEvidence]:
    """Drop evidence that is clearly about a different attribute dimension.

    This is deliberately narrow: it only rejects mechanically distinguishable
    category errors. It never chooses between two competing values of the same
    semantic attribute.
    """

    names = _field_names(semantic_field)
    filtered = list(candidates)

    if names & _STORAGE_CAPACITY_FIELD_NAMES:
        filtered = [item for item in filtered if _has_storage_capacity_value(item)]

    if names & _PRODUCT_DIMENSION_FIELD_NAMES:
        filtered = [item for item in filtered if not _is_package_dimension_evidence(item)]

    return filtered


def _candidate_exact_count(candidate: SourceEvidence) -> int | None:
    if isinstance(candidate.value, tuple):
        if len(candidate.value) != 1:
            return None
        raw = candidate.value[0]
    else:
        raw = candidate.value
    text = str(raw).strip().casefold()
    match = _EXACT_COUNT_RE.match(text)
    if match:
        return int(match.group(1))
    compact = normalize_key(text)
    if compact in {"dual", "dualcamera", "dualcameras", "duallens", "twin", "双镜头", "双摄", "双摄像头"}:
        return 2
    return None


def _candidate_count_lower_bound(candidate: SourceEvidence) -> int | None:
    exact = _candidate_exact_count(candidate)
    if exact is not None:
        return exact
    compact = normalize_key(candidate.value[0] if isinstance(candidate.value, tuple) and candidate.value else candidate.value)
    if compact in {
        "multi",
        "multiple",
        "multicamera",
        "multiplecameras",
        "multilens",
        "multiplelenses",
        "多镜头",
        "多摄",
        "多摄像头",
    }:
        return 2
    return None


def _select_precise_count_candidate(
    candidates: list[SourceEvidence],
    semantic_field: dict[str, Any],
) -> SourceEvidence | None:
    if not (_field_names(semantic_field) & _COUNT_FIELD_NAMES):
        return None
    exact = [(value, item) for item in candidates if (value := _candidate_exact_count(item)) is not None]
    exact_values = {value for value, _ in exact}
    if len(exact_values) != 1:
        return None
    exact_value = next(iter(exact_values))
    for item in candidates:
        lower = _candidate_count_lower_bound(item)
        if lower is None or lower > exact_value:
            return None
    compatible_exact = [item for value, item in exact if value == exact_value]
    compatible_exact.sort(key=lambda item: (item.priority, -item.confidence, item.source_reference))
    return compatible_exact[0]


def _is_set_like_field(semantic_field: dict[str, Any]) -> bool:
    return bool(semantic_field.get("multi_value")) or bool(
        _field_names(semantic_field) & _SET_LIKE_FIELD_NAMES
    )


def _candidate_value_set(
    candidate: SourceEvidence,
    semantic_field: dict[str, Any],
) -> frozenset[str]:
    values = _raw_values(candidate.value, multi_value=True)
    return frozenset(
        canonical_scalar_for_field(semantic_field, value)
        for value in values
        if value.strip()
    )


def _select_existing_superset_candidate(
    candidates: list[SourceEvidence],
    semantic_field: dict[str, Any],
) -> SourceEvidence | None:
    """Use an existing superset only when every other source is its subset.

    No new feature/package value is synthesized here. If two sources each add a
    different item, neither contains the other and the field remains conflict.
    """

    if not _is_set_like_field(semantic_field):
        return None
    sets = [(item, _candidate_value_set(item, semantic_field)) for item in candidates]
    if not sets or any(not values for _, values in sets):
        return None
    maximal = [
        (item, values)
        for item, values in sets
        if not any(values < other_values for _, other_values in sets)
    ]
    distinct_maximal = {values for _, values in maximal}
    if len(distinct_maximal) != 1:
        return None
    target = next(iter(distinct_maximal))
    if not all(values <= target for _, values in sets):
        return None
    choices = [item for item, values in maximal if values == target]
    choices.sort(key=lambda item: (item.priority, -item.confidence, item.source_reference))
    return choices[0] if choices else None


def _select_evidence(
    candidates: list[SourceEvidence],
    semantic_field: dict[str, Any],
) -> tuple[SourceEvidence | None, str | None]:
    if not candidates:
        return None, None

    candidates = _filter_semantically_incompatible_candidates(candidates, semantic_field)
    if not candidates:
        return None, None

    precise_count = _select_precise_count_candidate(candidates, semantic_field)
    if precise_count is not None:
        return precise_count, None

    superset = _select_existing_superset_candidate(candidates, semantic_field)
    if superset is not None:
        return superset, None

    canonical_values = {
        canonical_evidence_value_for_field(semantic_field, item.value)
        for item in candidates
    }
    if len(canonical_values) > 1:
        details = " | ".join(
            f"{item.source_type}:{item.source_reference}={item.value}" for item in candidates
        )
        return None, details
    candidates = sorted(candidates, key=lambda item: (item.priority, -item.confidence, item.source_reference))
    return candidates[0], None


def _evidence_display(chosen: SourceEvidence) -> str:
    return chosen.evidence_text or str(chosen.value)


def _missing_or_fallback(
    semantic_field: dict[str, Any],
    bundle: ProductSourceBundle,
    fallback: Any | None,
    detail: str,
) -> ResolvedAnswer:
    attribute_key = str(semantic_field.get("attribute_key") or "")
    label = str(semantic_field.get("label") or attribute_key)
    if fallback is not None and not _is_business_field(attribute_key):
        candidate = fallback.try_resolve(semantic_field, bundle)
        if isinstance(candidate, ResolvedAnswer) and candidate.status == RESOLVED:
            return candidate
    return ResolvedAnswer(
        attribute_key=attribute_key,
        label=label,
        status=MISSING,
        detail=detail,
    )


def resolve_field(
    semantic_field: dict[str, Any],
    bundle: ProductSourceBundle,
    fallback: Any | None = None,
) -> ResolvedAnswer:
    attribute_key = str(semantic_field.get("attribute_key") or "")
    label = str(semantic_field.get("label") or attribute_key)
    candidates = bundle.candidates(_candidate_keys(semantic_field))

    if _is_business_field(attribute_key):
        candidates = [
            item for item in candidates if item.source_type in BUSINESS_ALLOWED_SOURCE_TYPES
        ]
        if not candidates:
            return ResolvedAnswer(
                attribute_key=attribute_key,
                label=label,
                status=NEEDS_REVIEW,
                detail="经营字段只能来自明确结构化数据/config/rule；禁止 AI 或非结构化来源猜测。",
            )

    chosen, conflict_detail = _select_evidence(candidates, semantic_field)
    if conflict_detail is not None:
        return ResolvedAnswer(
            attribute_key=attribute_key,
            label=label,
            status=CONFLICT,
            detail=f"发现来源冲突：{conflict_detail}",
        )
    if chosen is None:
        return _missing_or_fallback(
            semantic_field,
            bundle,
            fallback,
            "没有找到与 attribute_key/label 精确对应的语义兼容明确证据。",
        )

    values = _raw_values(chosen.value, multi_value=bool(semantic_field.get("multi_value")))
    qualifier_options = _qualifier_options(semantic_field)
    values, qualifier, qualifier_match = _extract_qualifier(values, qualifier_options)
    if not values:
        return _missing_or_fallback(semantic_field, bundle, fallback, "证据值为空。")

    option_matches: list[dict[str, str]] = []
    value_options = _value_options(semantic_field)
    if value_options:
        for value in values:
            matched = _match_one_option(value, value_options)
            if matched is None:
                return ResolvedAnswer(
                    attribute_key=attribute_key,
                    label=label,
                    status=NEEDS_REVIEW,
                    answer=value,
                    answer_values=values,
                    source_type=chosen.source_type,
                    source_reference=chosen.source_reference,
                    evidence=_evidence_display(chosen),
                    confidence=chosen.confidence,
                    detail=f"答案 {value!r} 无法唯一精确匹配当前 Makro 下拉选项。",
                )
            option_matches.append(matched)
        values = [item["text"] or item["value"] for item in option_matches]

    if qualifier_match:
        option_matches.append({**qualifier_match, "kind": "qualifier"})

    return ResolvedAnswer(
        attribute_key=attribute_key,
        label=label,
        status=RESOLVED,
        answer=" | ".join(values),
        answer_values=values,
        qualifier=qualifier,
        source_type=chosen.source_type,
        source_reference=chosen.source_reference,
        evidence=_evidence_display(chosen),
        confidence=chosen.confidence,
        option_match=option_matches,
        detail="仅使用明确且语义兼容的证据解析；未按优先级覆盖真实冲突。",
    )


def resolve_fields(
    semantic_fields: Iterable[dict[str, Any]],
    bundle: ProductSourceBundle,
    fallback: Any | None = None,
) -> list[ResolvedAnswer]:
    return [resolve_field(field, bundle, fallback=fallback) for field in semantic_fields]
