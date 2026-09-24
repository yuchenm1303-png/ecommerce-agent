"""DOM-first Makro field behavior contracts.

The old schema/coverage pipeline could discover a field and prove that a raw DOM
value survived a React render cycle, but that is not the same thing as proving the
field's execution contract.  This module gives discovery and coverage one stable,
label-independent identity for each live control family and keeps evidence about
which identities have actually been exercised.

The fingerprint intentionally excludes product data, labels, DOM ids, option
values and the current number of repeatable slots.  Those are observations, not
mechanical control contracts.  Fixed unit *presence* is part of the contract;
the concrete unit remains observation metadata because CM and KG use the same
browser mutation mechanics while unit compatibility is validated separately.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from .unit_contract import fixed_rendered_unit, normalize_unit, qualifier_controls

CONTRACT_VERSION = 1

UNVERIFIED = "unverified"
REACT_STABLE = "react_stable"
REPEATABLE_COMMIT = "repeatable_commit"
PLATFORM_ACCEPTED = "platform_accepted"

_VERIFICATION_RANK = {
    UNVERIFIED: 0,
    REACT_STABLE: 1,
    REPEATABLE_COMMIT: 2,
    PLATFORM_ACCEPTED: 3,
}

_TEXT_KINDS = {
    "input",
    "custom_textbox",
    "custom_searchbox",
}
_LONG_TEXT_KINDS = {"textarea", "contenteditable"}
_SELECTION_KINDS = {"select", "dropdown", "autocomplete", "listbox"}
_BOOLEAN_KINDS = {"checkbox", "custom_checkbox"}
_RADIO_KINDS = {"radio", "custom_radio"}
_NUMERIC_KINDS = {"custom_spinbutton", "custom_slider"}


@dataclass(slots=True, frozen=True)
class BehaviorContractSignature:
    """State-independent mechanical identity of one live semantic field."""

    base_family: str
    primary_kind: str
    primary_tag: str
    input_type: str
    role: str
    inputmode: str
    control_kinds: tuple[str, ...]
    cardinality: str
    add_value_control: bool
    qualifier_model: str
    qualifier_kinds: tuple[str, ...]
    commit_model: str

    def identity_dict(self) -> dict[str, Any]:
        return {
            "base_family": self.base_family,
            "primary_kind": self.primary_kind,
            "primary_tag": self.primary_tag,
            "input_type": self.input_type,
            "role": self.role,
            "inputmode": self.inputmode,
            "control_kinds": list(self.control_kinds),
            "cardinality": self.cardinality,
            "add_value_control": self.add_value_control,
            "qualifier_model": self.qualifier_model,
            "qualifier_kinds": list(self.qualifier_kinds),
            "commit_model": self.commit_model,
        }

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            self.identity_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


@dataclass(slots=True, frozen=True)
class FieldContractObservation:
    section: str
    subsection: str
    attribute_key: str
    label: str
    signature: BehaviorContractSignature
    fixed_unit: str
    observed_value_control_count: int
    required: bool

    @property
    def fingerprint(self) -> str:
        return self.signature.fingerprint

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.section, self.attribute_key, self.label)

    def as_dict(self) -> dict[str, Any]:
        return {
            "section": self.section,
            "subsection": self.subsection,
            "attribute_key": self.attribute_key,
            "label": self.label,
            "fingerprint": self.fingerprint,
            "signature": self.signature.identity_dict(),
            "fixed_unit": self.fixed_unit,
            "observed_value_control_count": self.observed_value_control_count,
            "required": self.required,
        }


def _norm(value: object) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _value_controls(semantic_field: Mapping[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for raw in semantic_field.get("controls") or []:
        control = dict(raw)
        if str(control.get("name") or "").endswith("_qualifier"):
            continue
        if str(control.get("field_kind") or "").casefold() == "option":
            continue
        output.append(control)
    return output


def _primary_control(
    semantic_field: Mapping[str, Any], controls: list[dict[str, Any]]
) -> dict[str, Any] | None:
    key = str(semantic_field.get("attribute_key") or "")
    for control in controls:
        if key and str(control.get("id") or "") == key:
            return control
    return controls[0] if controls else None


def _is_numeric(control: Mapping[str, Any]) -> bool:
    kind = _norm(control.get("field_kind"))
    input_type = _norm(control.get("type"))
    role = _norm(control.get("role"))
    inputmode = _norm(control.get("inputmode"))
    return (
        kind in _NUMERIC_KINDS
        or input_type in {"number", "range"}
        or role in {"spinbutton", "slider"}
        or inputmode in {"numeric", "decimal"}
    )


def _is_radio_group(controls: Iterable[Mapping[str, Any]]) -> bool:
    items = list(controls)
    return bool(items) and all(_norm(control.get("field_kind")) in _RADIO_KINDS for control in items)


def _base_family(primary: Mapping[str, Any] | None, controls: list[dict[str, Any]]) -> str:
    if primary is None:
        return "unsupported"
    if _is_radio_group(controls):
        return "selection"
    kind = _norm(primary.get("field_kind"))
    if _is_numeric(primary):
        return "numeric"
    if kind in _SELECTION_KINDS:
        return "selection"
    if kind in _BOOLEAN_KINDS:
        return "boolean"
    if kind in _LONG_TEXT_KINDS:
        return "long_text"
    if kind in _TEXT_KINDS:
        return "text"
    return "unsupported"


def signature_for_field(semantic_field: Mapping[str, Any]) -> BehaviorContractSignature:
    controls = _value_controls(semantic_field)
    primary = _primary_control(semantic_field, controls)
    qualifiers = qualifier_controls(dict(semantic_field))
    fixed_unit = fixed_rendered_unit(dict(semantic_field))

    has_add = bool(semantic_field.get("has_add_value_control"))
    radio_group = _is_radio_group(controls)
    # Multiple radio controls are one single-choice value domain, not a repeatable
    # multi-value attribute.  Only indexed value slots / + semantics create
    # repeatable cardinality.
    repeatable = (
        bool(semantic_field.get("multi_value"))
        or has_add
        or (len(controls) > 1 and not radio_group)
    )
    cardinality = "repeatable" if repeatable else "single"

    if qualifiers:
        qualifier_model = "selectable"
    elif fixed_unit:
        qualifier_model = "fixed"
    else:
        qualifier_model = "none"

    base_family = _base_family(primary, controls)
    if has_add:
        commit_model = "repeatable_add"
    elif repeatable:
        commit_model = "repeatable_slots"
    elif base_family == "selection":
        commit_model = "selection"
    else:
        commit_model = "direct"

    return BehaviorContractSignature(
        base_family=base_family,
        primary_kind=_norm((primary or {}).get("field_kind")),
        primary_tag=_norm((primary or {}).get("tag")),
        input_type=_norm((primary or {}).get("type")),
        role=_norm((primary or {}).get("role")),
        inputmode=_norm((primary or {}).get("inputmode")),
        control_kinds=tuple(
            sorted(
                {
                    _norm(control.get("field_kind"))
                    for control in controls
                    if _norm(control.get("field_kind"))
                }
            )
        ),
        cardinality=cardinality,
        add_value_control=has_add,
        qualifier_model=qualifier_model,
        qualifier_kinds=tuple(
            sorted(
                {
                    _norm(control.get("field_kind"))
                    for control in qualifiers
                    if _norm(control.get("field_kind"))
                }
            )
        ),
        commit_model=commit_model,
    )


def observe_field(semantic_field: Mapping[str, Any]) -> FieldContractObservation:
    controls = _value_controls(semantic_field)
    fixed_unit = fixed_rendered_unit(dict(semantic_field))
    return FieldContractObservation(
        section=str(semantic_field.get("section_heading") or ""),
        subsection=str(semantic_field.get("subsection_heading") or ""),
        attribute_key=str(semantic_field.get("attribute_key") or ""),
        label=str(semantic_field.get("label") or ""),
        signature=signature_for_field(semantic_field),
        fixed_unit=normalize_unit(fixed_unit),
        observed_value_control_count=len(controls),
        required=bool(semantic_field.get("required")),
    )


def verification_level(
    observation: FieldContractObservation,
    result: Any | Mapping[str, Any] | None,
) -> tuple[str, str]:
    """Grade synthetic evidence without pretending raw readback means commitment.

    Direct/selectable controls can be mechanically verified by stable React
    readback.  Repeatable ``+`` controls require the coverage runner to prove that
    the add action created and filled another slot.  A repeatable field that only
    exposes pre-rendered slots is deliberately left unverified until a dedicated
    adapter exercises more than the first slot.
    """

    if result is None:
        return UNVERIFIED, "当前 contract 只被观察到，没有可关联的空字段行为样本。"

    def get(name: str, default: Any = None) -> Any:
        if isinstance(result, Mapping):
            return result.get(name, default)
        return getattr(result, name, default)

    if str(get("status", "")) != "pass":
        return UNVERIFIED, f"行为样本未通过：status={get('status', '')!r}。"

    model = observation.signature.commit_model
    if model == "repeatable_add":
        if bool(get("plus_available")) and str(get("plus_status", "")) == "pass":
            return REPEATABLE_COMMIT, "主值稳定回读，并证明 + 创建的新槽位可写且稳定。"
        return UNVERIFIED, "repeatable_add 只证明了原始输入值，没有证明 + 提交/新增槽位契约。"

    if model == "repeatable_slots":
        return UNVERIFIED, "repeatable_slots 目前只验证第一槽，不能把局部回读当成完整多值契约。"

    return REACT_STABLE, "使用稳定 React 回读证明该直接/选择型机械写入契约。"


def _best_level(levels: Iterable[str]) -> str:
    return max(
        levels,
        key=lambda level: _VERIFICATION_RANK.get(level, -1),
        default=UNVERIFIED,
    )


def build_run_contracts(
    *,
    vertical: str,
    observations: Iterable[FieldContractObservation],
    results: Iterable[Any],
    source_url: str = "",
) -> dict[str, Any]:
    observed = list(observations)
    result_index: dict[tuple[str, str, str], Any] = {}
    for result in results:
        identity = (
            str(getattr(result, "section", "")),
            str(getattr(result, "attribute_key", "")),
            str(getattr(result, "label", "")),
        )
        result_index[identity] = result

    contracts: dict[str, dict[str, Any]] = {}
    for observation in observed:
        level, detail = verification_level(
            observation,
            result_index.get(observation.identity),
        )
        entry = contracts.setdefault(
            observation.fingerprint,
            {
                "fingerprint": observation.fingerprint,
                "signature": observation.signature.identity_dict(),
                "verification_levels": [],
                "best_verification": UNVERIFIED,
                "verified_for_execution": False,
                "observations": [],
            },
        )
        entry["verification_levels"].append(level)
        example = observation.as_dict()
        example["verification"] = level
        example["verification_detail"] = detail
        entry["observations"].append(example)

    for entry in contracts.values():
        best = _best_level(entry["verification_levels"])
        entry["best_verification"] = best
        entry["verified_for_execution"] = (
            _VERIFICATION_RANK.get(best, 0) >= _VERIFICATION_RANK[REACT_STABLE]
        )
        entry["verification_levels"] = dict(
            sorted(
                {
                    level: entry["verification_levels"].count(level)
                    for level in set(entry["verification_levels"])
                }.items()
            )
        )

    verified = sum(
        1 for entry in contracts.values() if entry["verified_for_execution"]
    )
    return {
        "contract_version": CONTRACT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "vertical": vertical,
        "source_url": source_url,
        "stats": {
            "observed_field_count": len(observed),
            "unique_contract_count": len(contracts),
            "verified_contract_count": verified,
            "unverified_contract_count": len(contracts) - verified,
            "fully_verified": bool(contracts) and verified == len(contracts),
        },
        "contracts": dict(sorted(contracts.items())),
    }


def merge_contract_registry(
    existing: Mapping[str, Any] | None,
    run_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Merge one full-vertical run into the durable cross-vertical registry."""

    now = datetime.now(timezone.utc).isoformat()
    registry: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "generated_at": now,
        "contracts": {},
        "verticals": {},
    }
    if existing and int(existing.get("contract_version") or 0) == CONTRACT_VERSION:
        registry["contracts"] = json.loads(
            json.dumps(existing.get("contracts") or {})
        )
        registry["verticals"] = json.loads(
            json.dumps(existing.get("verticals") or {})
        )

    vertical = str(run_payload.get("vertical") or "")
    run_contracts = run_payload.get("contracts") or {}
    observed_fingerprints: list[str] = []

    for fingerprint, incoming in run_contracts.items():
        observed_fingerprints.append(fingerprint)
        target = registry["contracts"].setdefault(
            fingerprint,
            {
                "fingerprint": fingerprint,
                "signature": incoming.get("signature") or {},
                "best_verification": UNVERIFIED,
                "verified_for_execution": False,
                "verticals": [],
                "field_examples": [],
                "evidence_counts": {},
            },
        )
        if vertical and vertical not in target["verticals"]:
            target["verticals"].append(vertical)
            target["verticals"].sort(key=str.casefold)

        incoming_best = str(incoming.get("best_verification") or UNVERIFIED)
        target["best_verification"] = _best_level(
            [str(target.get("best_verification") or UNVERIFIED), incoming_best]
        )
        target["verified_for_execution"] = (
            _VERIFICATION_RANK.get(target["best_verification"], 0)
            >= _VERIFICATION_RANK[REACT_STABLE]
        )

        counts = target.setdefault("evidence_counts", {})
        for level, count in (incoming.get("verification_levels") or {}).items():
            counts[level] = int(counts.get(level) or 0) + int(count or 0)

        examples = target.setdefault("field_examples", [])
        existing_keys = {
            (
                item.get("section"),
                item.get("attribute_key"),
                item.get("label"),
                item.get("fixed_unit"),
            )
            for item in examples
        }
        for item in incoming.get("observations") or []:
            key = (
                item.get("section"),
                item.get("attribute_key"),
                item.get("label"),
                item.get("fixed_unit"),
            )
            if key in existing_keys:
                continue
            compact = {
                "vertical": vertical,
                "section": item.get("section"),
                "attribute_key": item.get("attribute_key"),
                "label": item.get("label"),
                "fixed_unit": item.get("fixed_unit"),
            }
            examples.append(compact)
            existing_keys.add(key)
            if len(examples) >= 25:
                break

    vertical_verified = all(
        bool(
            registry["contracts"].get(fingerprint, {}).get(
                "verified_for_execution"
            )
        )
        for fingerprint in observed_fingerprints
    )
    registry["verticals"][vertical] = {
        "last_audited_at": now,
        "source_url": str(run_payload.get("source_url") or ""),
        "observed_field_count": int(
            (run_payload.get("stats") or {}).get("observed_field_count") or 0
        ),
        "observed_contracts": sorted(observed_fingerprints),
        "fully_verified": bool(observed_fingerprints) and vertical_verified,
    }

    total = len(registry["contracts"])
    verified = sum(
        1
        for item in registry["contracts"].values()
        if item.get("verified_for_execution")
    )
    registry["stats"] = {
        "audited_vertical_count": len(registry["verticals"]),
        "unique_contract_count": total,
        "verified_contract_count": verified,
        "unverified_contract_count": total - verified,
        "all_observed_contracts_verified": bool(total) and verified == total,
    }
    registry["generated_at"] = now
    return registry


def marketplace_completeness(
    schema_registry: Mapping[str, Any],
    behavior_registry: Mapping[str, Any],
) -> dict[str, Any]:
    expected = sorted(
        (schema_registry.get("verticals") or {}).keys(),
        key=str.casefold,
    )
    audited_map = behavior_registry.get("verticals") or {}
    audited = sorted(audited_map.keys(), key=str.casefold)
    expected_set = set(expected)
    missing = [vertical for vertical in expected if vertical not in audited_map]
    incomplete = [
        vertical
        for vertical in expected
        if vertical in audited_map
        and not bool(audited_map[vertical].get("fully_verified"))
    ]
    return {
        "expected_vertical_count": len(expected),
        "audited_vertical_count": len(
            [vertical for vertical in expected if vertical in audited_map]
        ),
        "extra_audited_verticals": [
            vertical for vertical in audited if vertical not in expected_set
        ],
        "missing_verticals": missing,
        "incomplete_verticals": incomplete,
        "marketplace_complete": bool(expected) and not missing and not incomplete,
    }


__all__ = [
    "BehaviorContractSignature",
    "CONTRACT_VERSION",
    "FieldContractObservation",
    "PLATFORM_ACCEPTED",
    "REACT_STABLE",
    "REPEATABLE_COMMIT",
    "UNVERIFIED",
    "build_run_contracts",
    "marketplace_completeness",
    "merge_contract_registry",
    "observe_field",
    "signature_for_field",
    "verification_level",
]
