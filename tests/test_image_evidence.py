from __future__ import annotations

import pytest

import app.image_evidence as image_evidence
from app.image_evidence import ImageEvidenceError, run_image_evidence
from app.semantic_grounding import GroundedSource, IMAGE_KIND


def images(count: int) -> list[GroundedSource]:
    return [
        GroundedSource(
            source_id=f"image:{index:03d}",
            source_type="product_image",
            kind=IMAGE_KIND,
            origin=f"image-{index}.jpg",
            image_path=f"image-{index}.jpg",
            sha256=f"{index:064x}",
        )
        for index in range(1, count + 1)
    ]


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
    sources = images(7)
    first = run_image_evidence(
        provider,
        sources,
        batch_size=3,
        concurrency=3,
        cache_dir=tmp_path,
        cache_namespace="contract-a",
    )
    second = run_image_evidence(
        provider,
        sources,
        batch_size=2,
        concurrency=2,
        cache_dir=tmp_path,
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
    run_image_evidence(provider, images(2), batch_size=2, cache_dir=tmp_path)
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
        images(2),
        batch_size=2,
        concurrency=1,
        cache_dir=tmp_path,
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
            images(2),
            batch_size=2,
            concurrency=1,
            cache_dir=tmp_path,
        )

    assert provider.calls == 3


def test_invalid_model_partition_is_retried_once_and_can_recover(tmp_path, monkeypatch):
    monkeypatch.setattr(image_evidence.time, "sleep", lambda _seconds: None)
    provider = WrongPartitionThenSuccessProvider()

    result = run_image_evidence(
        provider,
        images(2),
        batch_size=2,
        concurrency=1,
        cache_dir=tmp_path,
    )

    assert provider.calls == 2
    assert result.model_calls == 2
    assert len(result.observations) == 2


def test_non_retryable_image_batch_failure_stops_immediately(tmp_path):
    provider = FakeImageProvider(fail=True)
    with pytest.raises(ImageEvidenceError, match="vision unavailable"):
        run_image_evidence(
            provider,
            images(2),
            batch_size=2,
            cache_dir=tmp_path,
        )
    assert provider.calls == 1
