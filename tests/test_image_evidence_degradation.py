from __future__ import annotations

from pathlib import Path

from PIL import Image

from app.image_evidence import run_image_evidence
from app.semantic_grounding import GroundedSource, IMAGE_KIND, TEXT_KIND


def _write_jpeg(path: Path, *, size: tuple[int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, "white").save(path, format="JPEG")


def _image(path: Path, source_id: str, *, size: tuple[int, int]) -> GroundedSource:
    _write_jpeg(path, size=size)
    return GroundedSource(
        source_id=source_id,
        source_type="product_image",
        kind=IMAGE_KIND,
        origin=path.name,
        image_path=str(path),
        sha256=(source_id.encode("utf-8").hex() + "0" * 64)[:64],
    )


def _text() -> GroundedSource:
    return GroundedSource(
        source_id="text:001",
        source_type="supplier_visible_text",
        kind=TEXT_KIND,
        origin="supplier page",
        content="Grounded supplier product text remains available.",
        sha256="f" * 64,
    )


def _success_response(request):
    return {
        "images": {
            image_id: {
                "visible_text": "",
                "facts": [],
                "notes": "",
            }
            for image_id in request["image_ids"]
        },
        "summary": "ok",
    }


class RecordingProvider:
    name = "recording-provider"
    model = "test-model"

    def __init__(self):
        self.requests = []

    def extract_json(self, request):
        self.requests.append(request)
        return _success_response(request)


class DimensionRejectingProvider:
    name = "dimension-rejecting-provider"
    model = "test-model"

    def __init__(self, bad_id: str):
        self.bad_id = bad_id
        self.requests = []

    def extract_json(self, request):
        self.requests.append(request)
        if self.bad_id in request["image_ids"]:
            raise RuntimeError(
                "OpenAI-compatible JSON task 调用失败：Error code: 400 - "
                "{'error': {'message': '<400> InternalError.Algo.InvalidParameter: "
                "The image length and width do not meet the model restrictions. "
                "[height:1 or width:1 must be larger than 10]', "
                "'type': 'invalid_request_error', 'code': 'invalid_parameter_error'}}"
            )
        return _success_response(request)


def test_tiny_image_is_quarantined_before_model_call_and_valid_sibling_continues(tmp_path):
    valid = _image(tmp_path / "valid.jpg", "image:001", size=(32, 24))
    tiny = _image(tmp_path / "tiny.jpg", "image:002", size=(1, 32))
    provider = RecordingProvider()

    result = run_image_evidence(
        provider,
        [valid, tiny, _text()],
        batch_size=3,
        concurrency=1,
        cache_dir=tmp_path / "cache",
    )

    assert len(provider.requests) == 1
    assert provider.requests[0]["image_ids"] == [valid.source_id]
    assert [item.image_id for item in result.observations] == [valid.source_id]
    assert result.failed_images == (tiny.source_id,)
    assert result.failed_batches == 1
    assert any("below provider minimum" in warning for warning in result.warnings)


def test_provider_dimension_400_isolated_to_bad_image_without_killing_product(tmp_path):
    bad = _image(tmp_path / "bad.jpg", "image:001", size=(32, 24))
    good = _image(tmp_path / "good.jpg", "image:002", size=(32, 24))
    provider = DimensionRejectingProvider(bad.source_id)

    result = run_image_evidence(
        provider,
        [bad, good, _text()],
        batch_size=2,
        concurrency=1,
        cache_dir=tmp_path / "cache",
    )

    requested_partitions = [tuple(request["image_ids"]) for request in provider.requests]
    assert requested_partitions[0] == (bad.source_id, good.source_id)
    assert set(requested_partitions[1:]) == {(bad.source_id,), (good.source_id,)}
    assert [item.image_id for item in result.observations] == [good.source_id]
    assert result.failed_images == (bad.source_id,)
    assert result.failed_batches == 1
    assert result.model_calls == 3
    assert any("must be larger than 10" in warning for warning in result.warnings)
