from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

import app.image_evidence as image_evidence
from app.image_evidence import ImageEvidenceError, run_image_evidence
from app.semantic_grounding import GroundedSource, IMAGE_KIND, TEXT_KIND


def _write_jpeg(path: Path, *, size: tuple[int, int] = (32, 24)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, "white").save(path, format="JPEG")


def images(count: int, root: Path) -> list[GroundedSource]:
    output: list[GroundedSource] = []
    for index in range(1, count + 1):
        path = root / f"image-{index}.jpg"
        _write_jpeg(path)
        output.append(
            GroundedSource(
                source_id=f"image:{index:03d}",
                source_type="product_image",
                kind=IMAGE_KIND,
                origin=f"image-{index}.jpg",
                image_path=str(path),
                sha256=f"{index:064x}",
            )
        )
    return output


def text_source() -> GroundedSource:
    return GroundedSource(
        source_id="text:001",
        source_type="supplier_visible_text",
        kind=TEXT_KIND,
        origin="supplier page",
        content="Product title and grounded product specifications are available.",
        sha256="f" * 64,
    )


def _success_response(request):
    return {
        "images": {
            image_id: {
                "visible_text": f"text on {image_id}",
                "facts": [
                    {
                        "name": "dimension",
                        "scope": "packaging",
                        "value": "16 x 11 x 7",
                        "qualifier": "cm",
                        "evidence_text": f"{image_id} shows package dimensions",
                    }
                ],
                "notes": "",
            }
            for image_id in request["image_ids"]
        },
        "summary": "independent image observations",
    }


class FakeImageProvider:
    name = "fake-image-provider"

    def __init__(self, *, fail: bool = False):
        self.calls = 0
        self.requests = []
        self.fail = fail

    def extract_json(self, request):
        self.calls += 1
        self.requests.append(request)
        if self.fail:
            raise RuntimeError("vision unavailable")
        return _success_response(request)


class TransientStructuredOutputProvider:
    name = "transient-json-provider"

    def __init__(self, *, failures: int):
        self.failures = int(failures)
        self.calls = 0

    def extract_json(self, request):
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError(
                "OpenAI-compatible JSON task 调用失败: Error code: 400 - "
                "InternalError.Algo.InvalidParameter: Model output became abnormal while generating "
                "a JSON response for response_format. The generation was aborted because the partial "
                "output may be incomplete or invalid JSON. Please retry the request."
            )
        return _success_response(request)


class WrongPartitionThenSuccessProvider:
    name = "wrong-partition-provider"

    def __init__(self):
        self.calls = 0

    def extract_json(self, request):
        self.calls += 1
        if self.calls == 1:
            return {"images": {}, "summary": "bad first response"}
        return _success_response(request)


def test_image_evidence_is_mechanically_batched_and_cached_per_image(tmp_path):
    provider = FakeImageProvider()
    sources = images(7, tmp_path / "raw")
    first = run_image_evidence(
        provider,
        sources,
        batch_size=3,
        concurrency=3,
        cache_dir=tmp_path / "cache",
        cache_namespace="contract-a",
    )
    second = run_image_evidence(
        provider,
        sources,
        batch_size=2,
        concurrency=2,
        cache_dir=tmp_path / "cache",
        cache_namespace="contract-a",
    )

    assert first.batch_count == 3
    assert first.model_calls == 3
    assert first.cache_hits == 0
    assert second.batch_count == 0
    assert second.model_calls == 0
    assert second.cache_hits == 7
    assert provider.calls == 3
    assert [item.image_id for item in second.observations] == [item.source_id for item in sources]


def test_image_request_has_no_marketplace_schema_or_non_image_sources(tmp_path):
    provider = FakeImageProvider()
    run_image_evidence(provider, images(2, tmp_path / "raw"), batch_size=2, cache_dir=tmp_path / "cache")
    request = provider.requests[0]
    assert request["task"] == "extract_independent_product_image_evidence"
    assert request["target_fields"] == []
    assert all(source["kind"] == "image" for source in request["grounded_sources"])
    assert set(request["json_contract"]["properties"]["images"]["required"]) == {
        "image:001",
        "image:002",
    }


def test_transient_response_format_json_failure_retries_and_recovers(tmp_path, monkeypatch):
    monkeypatch.setattr(image_evidence.time, "sleep", lambda _seconds: None)
    provider = TransientStructuredOutputProvider(failures=1)

    result = run_image_evidence(
        provider,
        images(2, tmp_path / "raw"),
        batch_size=2,
        concurrency=1,
        cache_dir=tmp_path / "cache",
    )

    assert provider.calls == 2
    assert result.model_calls == 2
    assert result.failed_batches == 0
    assert len(result.observations) == 2


def test_transient_response_format_json_failure_is_bounded_to_three_attempts(tmp_path, monkeypatch):
    monkeypatch.setattr(image_evidence.time, "sleep", lambda _seconds: None)
    provider = TransientStructuredOutputProvider(failures=99)

    with pytest.raises(ImageEvidenceError, match="failed after 3 model attempt"):
        run_image_evidence(
            provider,
            images(2, tmp_path / "raw"),
            batch_size=2,
            concurrency=1,
            cache_dir=tmp_path / "cache",
        )

    assert provider.calls == 3


def test_invalid_model_partition_is_retried_once_and_can_recover(tmp_path, monkeypatch):
    monkeypatch.setattr(image_evidence.time, "sleep", lambda _seconds: None)
    provider = WrongPartitionThenSuccessProvider()

    result = run_image_evidence(
        provider,
        images(2, tmp_path / "raw"),
        batch_size=2,
        concurrency=1,
        cache_dir=tmp_path / "cache",
    )

    assert provider.calls == 2
    assert result.model_calls == 2
    assert len(result.observations) == 2


def test_non_retryable_image_batch_failure_stops_immediately(tmp_path):
    provider = FakeImageProvider(fail=True)
    with pytest.raises(ImageEvidenceError, match="vision unavailable"):
        run_image_evidence(
            provider,
            images(2, tmp_path / "raw"),
            batch_size=2,
            cache_dir=tmp_path / "cache",
        )
    assert provider.calls == 1


def test_opaque_valid_supplier_image_is_canonicalized_before_provider_transport(tmp_path):
    opaque = tmp_path / "source-image-23.img"
    _write_jpeg(opaque)
    source = GroundedSource(
        source_id="image:023",
        source_type="product_image",
        kind=IMAGE_KIND,
        origin="supplier opaque image",
        image_path=str(opaque),
        sha256="2" * 64,
    )
    provider = FakeImageProvider()

    result = run_image_evidence(provider, [source, text_source()], cache_dir=tmp_path / "cache")

    assert len(result.observations) == 1
    request_source = provider.requests[0]["grounded_sources"][0]
    assert request_source["source_id"] == source.source_id
    assert request_source["sha256"] == source.sha256
    assert Path(request_source["image_path"]).suffix == ".jpg"
    assert request_source["image_path"] != source.image_path


def test_corrupt_supplier_image_isolated_before_model_call_when_other_evidence_exists(tmp_path):
    valid = images(1, tmp_path / "raw")[0]
    corrupt_path = tmp_path / "source-image-23.img"
    corrupt_path.write_bytes(b"not-an-image" * 1024)
    corrupt = GroundedSource(
        source_id="image:023",
        source_type="product_image",
        kind=IMAGE_KIND,
        origin="supplier corrupt image",
        image_path=str(corrupt_path),
        sha256="3" * 64,
    )
    provider = FakeImageProvider()

    result = run_image_evidence(
        provider,
        [valid, corrupt, text_source()],
        batch_size=3,
        cache_dir=tmp_path / "cache",
    )

    assert provider.calls == 1
    assert provider.requests[0]["image_ids"] == [valid.source_id]
    assert [item.image_id for item in result.observations] == [valid.source_id]
    assert result.failed_images == (corrupt.source_id,)
    assert result.failed_batches == 1
    assert "before model call" in result.warnings[0]


def test_all_corrupt_images_can_degrade_to_grounded_text_without_model_call(tmp_path):
    corrupt_path = tmp_path / "source-image-23.img"
    corrupt_path.write_bytes(b"not-an-image" * 1024)
    corrupt = GroundedSource(
        source_id="image:023",
        source_type="product_image",
        kind=IMAGE_KIND,
        origin="supplier corrupt image",
        image_path=str(corrupt_path),
        sha256="4" * 64,
    )
    provider = FakeImageProvider()

    result = run_image_evidence(provider, [corrupt, text_source()], cache_dir=tmp_path / "cache")

    assert provider.calls == 0
    assert result.observations == []
    assert result.failed_images == (corrupt.source_id,)
    assert result.failed_batches == 1


def test_all_corrupt_images_without_text_fail_with_precise_pre_model_error(tmp_path):
    corrupt_path = tmp_path / "source-image-23.img"
    corrupt_path.write_bytes(b"not-an-image" * 1024)
    corrupt = GroundedSource(
        source_id="image:023",
        source_type="product_image",
        kind=IMAGE_KIND,
        origin="supplier corrupt image",
        image_path=str(corrupt_path),
        sha256="5" * 64,
    )
    provider = FakeImageProvider()

    with pytest.raises(ImageEvidenceError, match="all image evidence failed"):
        run_image_evidence(provider, [corrupt], cache_dir=tmp_path / "cache")

    assert provider.calls == 0
