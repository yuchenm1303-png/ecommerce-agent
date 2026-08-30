from __future__ import annotations

from PIL import Image

from app.product_identity import infer_product_identity
from app.source_snapshot import SourceSnapshot


class RecordingProvider:
    name = "fake"

    def __init__(self):
        self.requests: list[dict] = []

    def extract_json(self, request_payload):
        self.requests.append(request_payload)
        return {
            "entity_kind": "physical_product",
            "product_type_en": "blended Scotch whisky",
            "brand_identity": "Vat 69",
            "product_summary": "Vat 69 blended Scotch whisky",
            "confidence": 0.99,
            "evidence_refs": ["identity:page-title"],
        }


def _snapshot() -> SourceSnapshot:
    return SourceSnapshot(
        requested_url="https://supplier.test/vat-69",
        final_url="https://supplier.test/vat-69",
        title="Vat 69 Blended Scotch Whisky",
        captured_at="2026-08-30T00:00:00Z",
        visible_text="Vat 69 Blended Scotch Whisky",
    )


def test_undecodable_image_is_removed_before_the_single_ai_request(tmp_path):
    bad = tmp_path / "source-image-03-bad.img"
    bad.write_bytes(b"<html><body>cdn error</body></html>")

    good = tmp_path / "source-image-01-good.img"
    Image.new("RGB", (320, 240), "white").save(good, format="JPEG")

    provider = RecordingProvider()
    identity = infer_product_identity(provider, _snapshot(), image_paths=[bad, good])

    assert identity.product_type_en == "blended Scotch whisky"
    assert len(provider.requests) == 1

    request = provider.requests[0]
    image_sources = [
        source for source in request["grounded_sources"] if source.get("kind") == "image"
    ]
    assert len(image_sources) == 1
    assert image_sources[0]["source_id"] == "identity:image:1"
    assert image_sources[0]["image_path"] == str(good)
    assert str(bad) not in repr(request)
    assert "identity:image:1" in request["context"]["allowed_evidence_refs"]


def test_all_undecodable_images_still_make_one_text_grounded_ai_request(tmp_path):
    bad = tmp_path / "source-image-03-bad.img"
    bad.write_bytes(b"not image pixels")

    provider = RecordingProvider()
    identity = infer_product_identity(provider, _snapshot(), image_paths=[bad])

    assert identity.product_type_en == "blended Scotch whisky"
    assert len(provider.requests) == 1
    assert not any(
        source.get("kind") == "image"
        for source in provider.requests[0]["grounded_sources"]
    )
