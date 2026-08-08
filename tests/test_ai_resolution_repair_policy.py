from __future__ import annotations

from pathlib import Path

import pytest

from app.ai_decisions import AIDecisionError, run_ai_resolution
from app.evidence_contract import ProductIdentity
from app.providers.errors import JSONTaskResponseError, JSONTaskTransportError
from app.semantic_grounding import GroundedSource, GroundingCatalog, TEXT_KIND


def _field():
    return {
        "attribute_key": "colour",
        "label": "Colour",
        "section_heading": "Product Description",
        "required": True,
        "multi_value": False,
        "options": [{"text": "Black", "value": "Black"}],
        "controls": [],
        "help_text": "",
    }


def _grounding(tmp_path: Path) -> GroundingCatalog:
    return GroundingCatalog(
        sources=[
            GroundedSource(
                source_id="supplier:001:text:0001:test",
                source_type="supplier_web",
                kind=TEXT_KIND,
                origin="supplier.test",
                content="Colour: Black",
                sha256="a" * 64,
            )
        ]
    )


class TransportFailureProvider:
    name = "transport-failure"

    def __init__(self):
        self.calls = 0

    def extract_json(self, request_payload):
        self.calls += 1
        raise JSONTaskTransportError("timeout")


class RepairableResponseProvider:
    name = "repairable-response"

    def __init__(self):
        self.calls = 0

    def extract_json(self, request_payload):
        self.calls += 1
        if self.calls == 1:
            raise JSONTaskResponseError("invalid JSON envelope")
        target = request_payload["target_fields"][0]
        return {
            "decisions": [
                {
                    "field_id": target["field_id"],
                    "status": "ready",
                    "values": ["Black"],
                    "citations": [
                        {
                            "source_reference": "supplier:001:text:0001:test",
                            "evidence_text": "Colour: Black",
                        }
                    ],
                }
            ]
        }


def test_transport_failure_never_triggers_semantic_repair(tmp_path):
    provider = TransportFailureProvider()
    with pytest.raises(AIDecisionError, match="no semantic repair"):
        run_ai_resolution(
            provider,
            [_field()],
            _grounding(tmp_path),
            expected_identity=ProductIdentity(sku="SKU-1"),
            max_repair_attempts=1,
        )
    assert provider.calls == 1


def test_invalid_response_does_not_rerun_whole_product_by_default(tmp_path):
    provider = RepairableResponseProvider()
    with pytest.raises(AIDecisionError, match="structural validation"):
        run_ai_resolution(
            provider,
            [_field()],
            _grounding(tmp_path),
            expected_identity=ProductIdentity(sku="SKU-1"),
        )
    assert provider.calls == 1


def test_explicit_diagnostic_mode_may_receive_one_structural_repair(tmp_path):
    provider = RepairableResponseProvider()
    result = run_ai_resolution(
        provider,
        [_field()],
        _grounding(tmp_path),
        expected_identity=ProductIdentity(sku="SKU-1"),
        max_repair_attempts=1,
    )
    assert provider.calls == 2
    assert result.model_calls == 2
    assert result.repair_attempts == 1
    assert result.packet.decisions[0].status == "ready"
