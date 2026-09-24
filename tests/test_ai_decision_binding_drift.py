from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.ai_decisions import (
    MISSING,
    READY,
    AIDecisionPacket,
    DecisionCitation,
    FieldDecision,
    field_id,
    load_ai_decision_packet,
    schema_digest,
    source_manifest_digest,
    validate_ai_decision_packet,
)
from app.evidence_contract import ProductIdentity
from app.semantic_grounding import GroundedSource, GroundingCatalog, TEXT_KIND


def _field(key: str, label: str) -> dict[str, object]:
    return {
        "attribute_key": key,
        "label": label,
        "section_heading": "Product Description",
        "required": False,
        "multi_value": False,
        "options": [],
        "controls": [],
        "help_text": "",
        "context_text": "",
    }


def _source(source_id: str, content: str) -> GroundedSource:
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return GroundedSource(
        source_id=source_id,
        source_type="supplier_web",
        kind=TEXT_KIND,
        origin="https://supplier.test/item",
        content=content,
        sha256=digest,
    )


def _ready(field: dict[str, object], source_id: str, value: str) -> FieldDecision:
    return FieldDecision(
        field_id=field_id(field),
        status=READY,
        values=[value],
        citations=[DecisionCitation(source_id, f"AI chose {value}")],
    )


def test_source_manifest_digest_ignores_source_order():
    first = _source("supplier:001:text:0001:first", "Colour: Black")
    second = _source("supplier:001:text:0002:second", "Power: 950 W")

    forward = GroundingCatalog(sources=[first, second])
    reverse = GroundingCatalog(sources=[second, first])

    assert source_manifest_digest(forward) == source_manifest_digest(reverse)


def test_source_fingerprint_drift_is_warning_not_product_failure():
    colour = _field("colour", "Colour")
    source = _source("supplier:001:text:0001:colour", "Colour: Black")
    grounding = GroundingCatalog(sources=[source])
    packet = AIDecisionPacket(
        identity=ProductIdentity(),
        schema_sha256=schema_digest([colour]),
        source_manifest_sha256="old-source-fingerprint",
        decisions=[_ready(colour, source.source_id, "Black")],
    )

    validated = validate_ai_decision_packet(packet, [colour], grounding)

    assert validated.decisions[0].status == READY
    assert any("source manifest fingerprint changed" in item for item in validated.warnings)


def test_schema_fingerprint_drift_rebinds_by_field_id():
    colour = _field("colour", "Colour")
    source = _source("supplier:001:text:0001:colour", "Colour: Black")
    grounding = GroundingCatalog(sources=[source])
    packet = AIDecisionPacket(
        identity=ProductIdentity(),
        schema_sha256="old-schema-fingerprint",
        source_manifest_sha256=source_manifest_digest(grounding),
        decisions=[_ready(colour, source.source_id, "Black")],
    )

    validated = validate_ai_decision_packet(packet, [colour], grounding)

    assert validated.decisions[0].status == READY
    assert any("live schema fingerprint changed" in item for item in validated.warnings)


def test_unverifiable_citation_isolated_to_its_field():
    colour = _field("colour", "Colour")
    power = _field("power", "Power")
    source = _source("supplier:001:text:0001:colour", "Colour: Black")
    grounding = GroundingCatalog(sources=[source])
    packet = AIDecisionPacket(
        identity=ProductIdentity(),
        schema_sha256=schema_digest([colour, power]),
        source_manifest_sha256="different-source-fingerprint",
        decisions=[
            _ready(colour, source.source_id, "Black"),
            _ready(power, "customer-file:001:text:0001:missing", "950 W"),
        ],
    )

    validated = validate_ai_decision_packet(packet, [colour, power], grounding)

    assert [item.status for item in validated.decisions] == [READY, MISSING]
    assert validated.decisions[0].values == ["Black"]
    assert validated.decisions[1].values == []


def test_loader_reuses_resolver_source_manifest_instead_of_rebuilding(tmp_path: Path):
    power = _field("power", "Power")
    canonical_source = _source(
        "customer-file:001:text:0001:power",
        "Customer supplemental specification: Power 950 W",
    )
    canonical = GroundingCatalog(sources=[canonical_source])
    fallback = GroundingCatalog(
        sources=[_source("supplier:001:text:0001:other", "Supplier page shell")]
    )
    packet = AIDecisionPacket(
        identity=ProductIdentity(),
        schema_sha256=schema_digest([power]),
        source_manifest_sha256=source_manifest_digest(canonical),
        decisions=[_ready(power, canonical_source.source_id, "950 W")],
    )

    (tmp_path / "source-manifest.json").write_text(
        json.dumps(canonical.as_manifest(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    packet_path = tmp_path / "ai-decisions.json"
    packet_path.write_text(
        json.dumps(packet.as_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    loaded = load_ai_decision_packet(packet_path, [power], fallback)

    assert loaded.decisions[0].status == READY
    assert loaded.decisions[0].citations[0].source_reference == canonical_source.source_id
    assert not any("source manifest fingerprint changed" in item for item in loaded.warnings)
    assert not any("canonical Resolver source manifest is unavailable" in item for item in loaded.warnings)
