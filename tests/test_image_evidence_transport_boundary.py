from __future__ import annotations

import base64
import hashlib
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from app.image_evidence import run_image_evidence
from app.providers.openai_compatible import _image_data_uri
from app.semantic_grounding import GroundedSource, IMAGE_KIND


class _CapturingProvider:
    name = "transport-test"
    model = "transport-test-model"

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def extract_json(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(request_payload)
        sources = request_payload["grounded_sources"]
        assert isinstance(sources, list)
        images: dict[str, dict[str, Any]] = {}
        for source in sources:
            image_path = str(source["image_path"])
            assert image_path.startswith("data:image/jpeg;base64,")
            payload = base64.b64decode(image_path.split(",", 1)[1], validate=True)
            with Image.open(BytesIO(payload)) as opened:
                assert opened.format == "JPEG"
                opened.load()
            images[str(source["source_id"])] = {
                "visible_text": "",
                "facts": [],
                "notes": "transport ok",
            }
        return {"images": images, "summary": "ok"}


def _source(path: Path, source_id: str) -> GroundedSource:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return GroundedSource(
        source_id=source_id,
        source_type="supplier_image",
        kind=IMAGE_KIND,
        origin=str(path),
        image_path=str(path),
        sha256=digest,
    )


def test_image_evidence_canonicalizes_once_and_sends_inline_verified_jpeg(tmp_path: Path) -> None:
    image_path = tmp_path / "source.png"
    Image.new("RGBA", (900, 700), (20, 80, 140, 180)).save(image_path)
    provider = _CapturingProvider()

    result = run_image_evidence(
        provider,
        [_source(image_path, "image:good")],
        batch_size=1,
        concurrency=1,
    )

    assert [item.image_id for item in result.observations] == ["image:good"]
    assert result.failed_images == ()
    assert result.model_calls == 1
    assert len(provider.requests) == 1


def test_bad_local_image_isolated_before_model_call_while_valid_sibling_continues(tmp_path: Path) -> None:
    good_path = tmp_path / "good.jpg"
    Image.new("RGB", (640, 640), (220, 210, 200)).save(good_path, format="JPEG")
    bad_path = tmp_path / "bad.jpg"
    bad_path.write_bytes(b"not-an-image")
    provider = _CapturingProvider()

    result = run_image_evidence(
        provider,
        [
            _source(good_path, "image:good"),
            _source(bad_path, "image:bad"),
        ],
        batch_size=2,
        concurrency=1,
    )

    assert [item.image_id for item in result.observations] == ["image:good"]
    assert result.failed_images == ("image:bad",)
    assert result.failed_batches == 1
    assert len(provider.requests) == 1
    sent_ids = [item["source_id"] for item in provider.requests[0]["grounded_sources"]]
    assert sent_ids == ["image:good"]
    assert any("image:bad" in warning for warning in result.warnings)


def test_openai_compatible_provider_forwards_canonical_jpeg_data_uri_verbatim() -> None:
    buffer = BytesIO()
    Image.new("RGB", (16, 16), (1, 2, 3)).save(buffer, format="JPEG")
    data_uri = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")

    assert _image_data_uri(data_uri) == data_uri
