from __future__ import annotations

import json
from pathlib import Path

from app.ai_decisions import BUSINESS_LOCKED, MISSING, READY, REVIEW, field_id
from app.evidence_contract import ProductIdentity
from app.field_mapping import run_field_mapping, target_scope
from app.product_profile import ProductFact, ProductProfile, ProfileCandidate
from app.ai_decisions import DecisionCitation, source_manifest_digest
from app.semantic_grounding import GroundedSource, GroundingCatalog, TEXT_KIND


def field(index: int, *, business: bool = False):
    return {
        "attribute_key": "flipkart_selling_price" if business else f"field_{index}",
        "label": "Your selling price" if business else f"Field {index}",
        "section_heading": "Price, Stock and Shipping Information" if business else "Additional Description",
        "required": False,
        "multi_value": False,
        "options": [],
        "qualifier_options": [],
        "controls": [],
        "help_text": "",
    }


def grounding() -> GroundingCatalog:
    return GroundingCatalog(
        sources=[
            GroundedSource(
                source_id="supplier:001:text:0001:abc",
                source_type="supplier_web",
                kind=TEXT_KIND,
                origin="supplier",
                content="Value: known",
                sha256="a" * 64,
            )
        ]
    )


def profile(*, scope: str = "product", status: str = "supported") -> ProductProfile:
    sources = grounding()
    return ProductProfile(
        identity=ProductIdentity(sku="SKU-1"),
        source_manifest_sha256=source_manifest_digest(sources),
        facts=[
            ProductFact(
                name="known_fact",
                scope=scope,
                status=status,
                candidates=(
                    ProfileCandidate(
                        value="known",
                        citations=(
                            DecisionCitation(
                                "supplier:001:text:0001:abc",
                                "Value: known",
                            ),
                        ),
                    ),
                ),
            )
        ],
        summary="compact profile",
        extractor="profile-ai",
    )


class FakeMapProvider:
    name = "fake-map"

    def __init__(self, fail_call: int | None = None, *, include_fact_ids: bool = True):
        self.calls = 0
        self.requests = []
        self.fail_call = fail_call
        self.include_fact_ids = include_fact_ids

    def extract_json(self, request):
        self.calls += 1
        self.requests.append(request)
        if self.fail_call == self.calls:
            raise RuntimeError("batch failed")
        profile_payload = json.loads(request["grounded_sources"][0]["content"])
        fact_id = profile_payload["facts"][0]["fact_id"]
        decisions = []
        for target in request["target_fields"]:
            item = {
                "field_id": target["field_id"],
                "status": "ready",
                "values": ["known"],
                "citations": [
                    {
                        "source_reference": "supplier:001:text:0001:abc",
                        "evidence_text": "Value: known",
                    }
                ],
            }
            if self.include_fact_ids:
                item["profile_fact_ids"] = [fact_id]
            decisions.append(item)
        return {"decisions": decisions}


def test_mapping_mechanically_batches_non_business_fields_without_images():
    fields = [field(index) for index in range(5)] + [field(99, business=True)]
    provider = FakeMapProvider()
    result = run_field_mapping(
        provider,
        fields,
        profile(),
        grounding(),
        batch_size=2,
        concurrency=3,
    )
    assert result.batch_count == 3
    assert result.model_calls == 3
    assert len(provider.requests) == 3
    assert all(len(request["target_fields"]) <= 2 for request in provider.requests)
    assert all(
        all(source["kind"] == "text" for source in request["grounded_sources"])
        for request in provider.requests
    )
    assert result.packet.decisions[-1].status == BUSINESS_LOCKED
    assert all(item.status == READY for item in result.packet.decisions[:-1])


def test_mapping_ready_without_profile_fact_id_is_blocked_to_review():
    target = field(1)
    result = run_field_mapping(
        FakeMapProvider(include_fact_ids=False),
        [target],
        profile(),
        grounding(),
    )
    assert result.packet.decisions[0].status == REVIEW
    assert "profile_fact_ids" in result.packet.decisions[0].reason


def test_mapping_blocks_product_body_fact_from_packaging_dimension():
    target = field(1)
    target.update(
        attribute_key="package_length",
        label="Length",
        section_heading="Price, Stock and Shipping Information",
        qualifier_options=["mm", "cm"],
    )
    assert target_scope(target) == "packaging"
    result = run_field_mapping(
        FakeMapProvider(),
        [target],
        profile(scope="product_body"),
        grounding(),
    )
    assert result.packet.decisions[0].status == REVIEW
    assert "target_scope=packaging" in result.packet.decisions[0].reason


def test_mapping_blocks_generic_viewing_angle_from_exterior_fov():
    target = field(1)
    target.update(
        attribute_key="exterior_field_of_view",
        label="Exterior Field of View",
        qualifier_options=["degree"],
    )
    assert target_scope(target) == "exterior_camera"
    result = run_field_mapping(
        FakeMapProvider(),
        [target],
        profile(scope="product"),
        grounding(),
    )
    assert result.packet.decisions[0].status == REVIEW
    assert "target_scope=exterior_camera" in result.packet.decisions[0].reason


def test_mapping_conflict_fact_cannot_authorize_ready_description():
    target = field(1)
    target.update(attribute_key="description", label="Description")
    result = run_field_mapping(
        FakeMapProvider(),
        [target],
        profile(scope="product", status="conflict"),
        grounding(),
    )
    assert result.packet.decisions[0].status == REVIEW
    assert "unresolved conflict" in result.packet.decisions[0].reason


def test_mapping_manual_language_cannot_authorize_device_languages_supported():
    target = field(1)
    target.update(attribute_key="languages_supported", label="Languages Supported")
    assert target_scope(target) == "device_ui_language"
    result = run_field_mapping(
        FakeMapProvider(),
        [target],
        profile(scope="manual_documentation"),
        grounding(),
    )
    assert result.packet.decisions[0].status == REVIEW
    assert "target_scope=device_ui_language" in result.packet.decisions[0].reason


def test_mapping_negative_ready_requires_explicit_negative_evidence():
    class NegativeProvider(FakeMapProvider):
        def extract_json(self, request):
            self.calls += 1
            profile_payload = json.loads(request["grounded_sources"][0]["content"])
            fact_id = profile_payload["facts"][0]["fact_id"]
            target = request["target_fields"][0]
            return {
                "decisions": [
                    {
                        "field_id": target["field_id"],
                        "status": "ready",
                        "values": ["No"],
                        "citations": [
                            {
                                "source_reference": "supplier:001:text:0001:abc",
                                "evidence_text": "Value: known",
                            }
                        ],
                        "profile_fact_ids": [fact_id],
                    }
                ]
            }

    target = field(1)
    target.update(attribute_key="remote_control", label="Remote Control")
    result = run_field_mapping(NegativeProvider(), [target], profile(), grounding())
    assert result.packet.decisions[0].status == REVIEW
    assert "explicit negative evidence" in result.packet.decisions[0].reason


def test_mapping_scope_compatible_fact_cannot_launder_mount_citation_into_body_width():
    sources = GroundingCatalog(
        sources=[
            GroundedSource(
                source_id="supplier:body",
                source_type="supplier_web",
                kind=TEXT_KIND,
                origin="supplier",
                content="Body width 86 mm",
                sha256="b" * 64,
            ),
            GroundedSource(
                source_id="supplier:mount",
                source_type="supplier_web",
                kind=TEXT_KIND,
                origin="supplier",
                content="Mount width 52 mm",
                sha256="c" * 64,
            ),
        ]
    )
    product = ProductProfile(
        identity=ProductIdentity(sku="SKU-1"),
        source_manifest_sha256=source_manifest_digest(sources),
        facts=[
            ProductFact(
                name="body_dimensions",
                scope="product_body",
                status="supported",
                candidates=(
                    ProfileCandidate(
                        value="86 mm",
                        citations=(DecisionCitation("supplier:body", "Body width 86 mm"),),
                    ),
                ),
            ),
            ProductFact(
                name="mount_dimensions",
                scope="mount",
                status="supported",
                candidates=(
                    ProfileCandidate(
                        value="52 mm",
                        citations=(DecisionCitation("supplier:mount", "Mount width 52 mm"),),
                    ),
                ),
            ),
        ],
        extractor="profile-ai",
    )

    class LaunderedScopeProvider:
        name = "laundered-scope"

        def extract_json(self, request):
            payload = json.loads(request["grounded_sources"][0]["content"])
            ids = [item["fact_id"] for item in payload["facts"]]
            target = request["target_fields"][0]
            return {
                "decisions": [
                    {
                        "field_id": target["field_id"],
                        "status": "ready",
                        "values": ["52"],
                        "qualifier": "mm",
                        "citations": [
                            {
                                "source_reference": "supplier:mount",
                                "evidence_text": "Mount width 52 mm",
                            }
                        ],
                        "profile_fact_ids": ids,
                    }
                ]
            }

    target = field(1)
    target.update(attribute_key="width", label="Width", qualifier_options=["mm"])
    assert target_scope(target) == "product_body"
    result = run_field_mapping(LaunderedScopeProvider(), [target], product, sources)
    assert result.packet.decisions[0].status == REVIEW
    assert "scope-compatible claimed profile fact" in result.packet.decisions[0].reason


def test_mapping_ready_free_text_cannot_embed_unresolved_conflict_candidate():
    sources = grounding()
    citation = DecisionCitation("supplier:001:text:0001:abc", "Value: known")
    product = ProductProfile(
        identity=ProductIdentity(sku="SKU-1"),
        source_manifest_sha256=source_manifest_digest(sources),
        facts=[
            ProductFact(
                name="package_contents",
                scope="packaging",
                status="supported",
                candidates=(ProfileCandidate(value="dash cam", citations=(citation,)),),
            ),
            ProductFact(
                name="recording_resolution",
                scope="product",
                status="conflict",
                candidates=(
                    ProfileCandidate(value="720p", citations=(citation,)),
                    ProfileCandidate(value="1080p", citations=(citation,)),
                ),
            ),
        ],
        extractor="profile-ai",
    )

    class ConflictTextProvider:
        name = "conflict-text"

        def extract_json(self, request):
            payload = json.loads(request["grounded_sources"][0]["content"])
            package_fact_id = payload["facts"][0]["fact_id"]
            target = request["target_fields"][0]
            return {
                "decisions": [
                    {
                        "field_id": target["field_id"],
                        "status": "ready",
                        "values": ["1080P Dual Dash Cam"],
                        "citations": [
                            {
                                "source_reference": "supplier:001:text:0001:abc",
                                "evidence_text": "Value: known",
                            }
                        ],
                        "profile_fact_ids": [package_fact_id],
                    }
                ]
            }

    target = field(1)
    target.update(attribute_key="sales_package", label="Sales Package")
    result = run_field_mapping(ConflictTextProvider(), [target], product, sources)
    assert result.packet.decisions[0].status == REVIEW
    assert "unresolved Product Profile conflict" in result.packet.decisions[0].reason


def test_mapping_batch_failure_only_leaves_that_batch_unresolved():
    fields = [field(index) for index in range(4)]
    provider = FakeMapProvider(fail_call=1)
    result = run_field_mapping(
        provider,
        fields,
        profile(),
        grounding(),
        batch_size=2,
        concurrency=1,
    )
    statuses = [item.status for item in result.packet.decisions]
    assert result.failed_batches == 1
    assert statuses.count(MISSING) == 2
    assert statuses.count(READY) == 2


def test_mapping_batch_cache_reuses_successful_batches(tmp_path):
    fields = [field(index) for index in range(4)]
    provider = FakeMapProvider()
    cache = tmp_path / "cache"
    first = run_field_mapping(
        provider,
        fields,
        profile(),
        grounding(),
        batch_size=2,
        concurrency=2,
        cache_dir=cache,
        cache_namespace="model",
    )
    second = run_field_mapping(
        provider,
        fields,
        profile(),
        grounding(),
        batch_size=2,
        concurrency=2,
        cache_dir=cache,
        cache_namespace="model",
    )
    assert first.model_calls == 2
    assert second.model_calls == 0
    assert second.cache_hits == 2
    assert provider.calls == 2


def test_mapping_targets_are_stable_live_field_ids():
    target = field(1)
    provider = FakeMapProvider()
    run_field_mapping(provider, [target], profile(), grounding())
    assert provider.requests[0]["target_fields"][0]["field_id"] == field_id(target)