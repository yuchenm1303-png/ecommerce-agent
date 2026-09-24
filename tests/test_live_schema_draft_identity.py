from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.live_schema import assert_live_schema_matches, load_live_schema, write_live_schema
from app.makro.domain import MakroDomainAdapter
from app.makro.listing_draft_identity import (
    DRAFT_IDENTITY_FIELD,
    listing_draft_identity_from_url,
)


FIELD = {
    "attribute_key": "material",
    "label": "Material",
    "section_heading": "Additional Description",
    "required": False,
    "multi_value": False,
    "options": [],
    "qualifier_options": [],
    "help_text": "",
    "context_text": "",
}


def _url(request_id: str) -> str:
    return (
        "https://seller.makro.co.za/#dashboard/addListings/single?"
        f"requestId={request_id}&vid=VID-7&vertical=Lamp&brand=Acme"
    )


def test_listing_draft_identity_is_ephemeral_request_owned() -> None:
    identity = listing_draft_identity_from_url(_url("REQ-ONE"))
    assert identity == {
        "request_id": "REQ-ONE",
        "vid": "VID-7",
        "vertical": "lamp",
        "brand": "acme",
    }


def test_listing_draft_identity_requires_request_id() -> None:
    with pytest.raises(RuntimeError, match="requestId"):
        listing_draft_identity_from_url(
            "https://seller.makro.co.za/#dashboard/addListings/single?vertical=Lamp&brand=Acme"
        )


def test_live_schema_rejects_same_fields_from_another_draft(tmp_path) -> None:
    schema = tmp_path / "live-schema.json"
    prepared_identity = listing_draft_identity_from_url(_url("REQ-PREPARED"))
    write_live_schema(
        [FIELD],
        schema,
        listing_draft_identity=prepared_identity,
    )
    planned = load_live_schema(schema)

    current = [
        {
            **FIELD,
            DRAFT_IDENTITY_FIELD: listing_draft_identity_from_url(_url("REQ-OTHER")),
        }
    ]
    with pytest.raises(RuntimeError, match="not the draft"):
        assert_live_schema_matches(planned, current)


def test_live_schema_accepts_same_draft_and_same_field_contract(tmp_path) -> None:
    schema = tmp_path / "live-schema.json"
    identity = listing_draft_identity_from_url(_url("REQ-SAME"))
    write_live_schema([FIELD], schema, listing_draft_identity=identity)
    planned = load_live_schema(schema)
    current = [{**FIELD, DRAFT_IDENTITY_FIELD: dict(identity)}]

    assert_live_schema_matches(planned, current)


def test_domain_adapter_stamps_current_draft_identity(monkeypatch) -> None:
    page = SimpleNamespace(url=_url("REQ-DOMAIN"))
    adapter = MakroDomainAdapter(page)
    monkeypatch.setattr(
        "app.makro.domain.build_semantic_fields",
        lambda controls: [dict(FIELD)],
    )
    monkeypatch.setattr(
        "app.makro.domain.coalesce_radio_semantic_fields",
        lambda fields: fields,
    )

    fields = adapter.build_semantic_fields([])
    assert fields[0][DRAFT_IDENTITY_FIELD]["request_id"] == "REQ-DOMAIN"
