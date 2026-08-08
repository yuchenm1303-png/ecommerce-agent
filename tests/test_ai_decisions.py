from __future__ import annotations

from pathlib import Path

from app.ai_decisions import (
    BUSINESS_LOCKED,
    CONFLICT,
    MISSING,
    READY,
    REVIEW,
    AIDecisionPacket,
    DecisionAlternative,
    DecisionCitation,
    FieldDecision,
    field_id,
    schema_digest,
    source_manifest_digest,
    validate_ai_decision_packet,
)
from app.evidence_contract import ProductIdentity
from app.semantic_grounding import GroundedSource, GroundingCatalog, IMAGE_KIND, TEXT_KIND


def field(key: str, label: str, *, options=(), section="Product Description"):
    return {
        "attribute_key": key,
        "label": label,
        "section_heading": section,
        "required": True,
        "multi_value": False,
        "options": [{"text": item, "value": item} for item in options],
        "controls": [],
        "help_text": "",
    }


def grounding(tmp_path: Path) -> GroundingCatalog:
    image = tmp_path / "product.png"
    image.write_bytes(b"fake-image")
    return GroundingCatalog(
        sources=[
            GroundedSource(
                source_id="image:001",
                source_type="product_image",
                kind=IMAGE_KIND,
                origin=str(image),
                image_path=str(image),
                sha256="a" * 64,
            ),
            GroundedSource(
                source_id="supplier:001:text:0001:abc",
                source_type="supplier_web",
                kind=TEXT_KIND,
                origin="https://supplier.test/item",
                content=(
                    "Colour: Black\n"
                    "Resolution: 720p\n"
                    "Model: M8\n"
                    "Button operation\n"
                    "Package contents: camera, charger, bracket\n"
                    "Remote Control: No\n"
                    "Body Length: 86 mm; Body Width: 36 mm; Body Height: 32 mm"
                ),
                sha256="b" * 64,
            ),
        ]
    )


def test_ungrounded_text_citation_cannot_authorize_ready(tmp_path):
    colour = field("colour", "Colour", options=("Black", "White"))
    sources = grounding(tmp_path)
    packet = AIDecisionPacket(
        identity=ProductIdentity(sku="SKU-1"),
        schema_sha256=schema_digest([colour]),
        source_manifest_sha256=source_manifest_digest(sources),
        decisions=[
            FieldDecision(
                field_id=field_id(colour),
                status=READY,
                values=["Black"],
                citations=[
                    DecisionCitation(
                        source_reference="supplier:001:text:0001:abc",
                        evidence_text="This text does not exist",
                    )
                ],
            )
        ],
    )
    validated = validate_ai_decision_packet(
        packet,
        [colour],
        sources,
        expected_identity=ProductIdentity(sku="SKU-1"),
    )
    assert validated.decisions[0].status == REVIEW
    assert validated.decisions[0].citations == []


def test_business_field_is_forced_locked_even_if_ai_returns_ready(tmp_path):
    selling = field(
        "flipkart_selling_price",
        "Your selling price",
        section="Price, Stock and Shipping Information",
    )
    sources = grounding(tmp_path)
    packet = AIDecisionPacket(
        identity=ProductIdentity(sku="SKU-1"),
        schema_sha256=schema_digest([selling]),
        source_manifest_sha256=source_manifest_digest(sources),
        decisions=[
            FieldDecision(
                field_id=field_id(selling),
                status=READY,
                values=["999"],
                citations=[
                    DecisionCitation(
                        source_reference="supplier:001:text:0001:abc",
                        evidence_text="Colour: Black",
                    )
                ],
            )
        ],
    )
    validated = validate_ai_decision_packet(
        packet,
        [selling],
        sources,
        expected_identity=ProductIdentity(sku="SKU-1"),
    )
    decision = validated.decisions[0]
    assert decision.status == BUSINESS_LOCKED
    assert decision.values == []
    assert decision.citations == []


def test_real_cross_source_conflict_is_preserved(tmp_path):
    resolution = field("recording_resolution", "Recording Resolution")
    sources = grounding(tmp_path)
    packet = AIDecisionPacket(
        identity=ProductIdentity(sku="SKU-1"),
        schema_sha256=schema_digest([resolution]),
        source_manifest_sha256=source_manifest_digest(sources),
        decisions=[
            FieldDecision(
                field_id=field_id(resolution),
                status=CONFLICT,
                alternatives=[
                    DecisionAlternative(
                        values=("720p",),
                        citations=(
                            DecisionCitation(
                                "supplier:001:text:0001:abc",
                                "Resolution: 720p",
                            ),
                        ),
                    ),
                    DecisionAlternative(
                        values=("1080p",),
                        citations=(
                            DecisionCitation(
                                "image:001",
                                "visible FHD 1080P marking",
                            ),
                        ),
                    ),
                ],
            )
        ],
    )
    validated = validate_ai_decision_packet(
        packet,
        [resolution],
        sources,
        expected_identity=ProductIdentity(sku="SKU-1"),
    )
    assert validated.decisions[0].status == CONFLICT
    assert len(validated.decisions[0].alternatives) == 2


def test_malformed_conflict_is_downgraded_to_review(tmp_path):
    resolution = field("recording_resolution", "Recording Resolution")
    sources = grounding(tmp_path)
    packet = AIDecisionPacket(
        identity=ProductIdentity(sku="SKU-1"),
        schema_sha256=schema_digest([resolution]),
        source_manifest_sha256=source_manifest_digest(sources),
        decisions=[
            FieldDecision(
                field_id=field_id(resolution),
                status=CONFLICT,
                alternatives=[
                    DecisionAlternative(
                        values=("720p",),
                        citations=(
                            DecisionCitation(
                                "supplier:001:text:0001:abc",
                                "Resolution: 720p",
                            ),
                        ),
                    )
                ],
            )
        ],
    )
    validated = validate_ai_decision_packet(packet, [resolution], sources)
    assert validated.decisions[0].status == REVIEW


def test_omitted_field_becomes_missing_without_local_semantic_guess(tmp_path):
    colour = field("colour", "Colour")
    model = field("model_name", "Model Name")
    sources = grounding(tmp_path)
    packet = AIDecisionPacket(
        identity=ProductIdentity(sku="SKU-1"),
        schema_sha256=schema_digest([colour, model]),
        source_manifest_sha256=source_manifest_digest(sources),
        decisions=[
            FieldDecision(
                field_id=field_id(colour),
                status=READY,
                values=["Black"],
                citations=[
                    DecisionCitation(
                        source_reference="supplier:001:text:0001:abc",
                        evidence_text="Colour: Black",
                    )
                ],
            )
        ],
    )
    validated = validate_ai_decision_packet(packet, [colour, model], sources)
    assert [item.status for item in validated.decisions] == [READY, MISSING]


def test_grounded_web_ready_is_not_reinterpreted_by_seller_sku(tmp_path):
    mic = field("built_in_mic", "Built in Mic")
    sources = grounding(tmp_path)
    external_ref = "web-search:generic-m8"
    external_content = (
        "Search result title: M8 dash cam manual\n"
        "Search evidence: Built in Mic: Yes"
    )
    packet = AIDecisionPacket(
        identity=ProductIdentity(
            sku="237581229555",
            model_number="M8",
            brand="other",
        ),
        schema_sha256=schema_digest([mic]),
        source_manifest_sha256=source_manifest_digest(sources),
        decisions=[
            FieldDecision(
                field_id=field_id(mic),
                status=READY,
                values=["Yes"],
                citations=[DecisionCitation(external_ref, "Built in Mic: Yes")],
            )
        ],
    )
    validated = validate_ai_decision_packet(
        packet,
        [mic],
        sources,
        expected_identity=ProductIdentity(
            sku="237581229555",
            model_number="M8",
            brand="other",
        ),
        external_sources={external_ref: external_content},
    )
    assert validated.decisions[0].status == READY


def test_python_validator_does_not_reinterpret_negative_or_dimension_semantics(tmp_path):
    remote = field("remote_control", "Remote Control", options=("Yes", "No"))
    width = field("width", "Width")
    sources = grounding(tmp_path)
    packet = AIDecisionPacket(
        identity=ProductIdentity(sku="SKU-1"),
        schema_sha256=schema_digest([remote, width]),
        source_manifest_sha256=source_manifest_digest(sources),
        decisions=[
            FieldDecision(
                field_id=field_id(remote),
                status=READY,
                values=["No"],
                citations=[
                    DecisionCitation(
                        "supplier:001:text:0001:abc",
                        "Package contents: camera, charger, bracket",
                    )
                ],
            ),
            FieldDecision(
                field_id=field_id(width),
                status=READY,
                values=["86"],
                qualifier="mm",
                citations=[
                    DecisionCitation(
                        "supplier:001:text:0001:abc",
                        "Body Length: 86 mm",
                    )
                ],
            ),
        ],
    )
    validated = validate_ai_decision_packet(packet, [remote, width], sources)
    assert [item.status for item in validated.decisions] == [READY, READY]
