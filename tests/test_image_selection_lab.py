from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.image_selection_lab.core import (
    CachedProvider,
    EvalCandidate,
    EvalCase,
    GroundTruth,
    ImageSelectionLabError,
    StrategyOutput,
    import_case_from_selection,
    load_case,
    score_case,
)


class _FakeProvider:
    name = "fake"
    model = "fake-model"

    def __init__(self) -> None:
        self.calls = 0

    def extract_json(self, request_payload):
        self.calls += 1
        return {"echo_task": request_payload.get("task"), "ok": True}


def _truth(ownership: str, *, grade: int = 0, main: bool = False) -> GroundTruth:
    allowed = ownership in {"EXACT_TARGET", "TARGET_PACKAGING_OR_DETAIL"}
    return GroundTruth(
        ownership=ownership,
        auto_upload_allowed=allowed,
        relevance_grade=grade,
        main_image_allowed=main,
    )


def _case(tmp_path: Path) -> EvalCase:
    first = tmp_path / "a.jpg"
    second = tmp_path / "b.jpg"
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    return EvalCase(
        case_id="case",
        root=tmp_path,
        suites=("all",),
        target_product={"product_type": "test"},
        candidates=(
            EvalCandidate("a", first, _truth("EXACT_TARGET", grade=3, main=True)),
            EvalCandidate("b", second, _truth("OTHER_PRODUCT")),
        ),
    )


def test_score_case_treats_wrong_product_selection_as_hard_safety_failure(tmp_path: Path) -> None:
    case = _case(tmp_path)
    output = StrategyOutput(
        strategy="test",
        selected_ids=("a", "b"),
        ownership={},
        gallery={},
        transport_rejected=(),
        semantic_calls=0,
        status="done",
    )

    metrics = score_case(case, output)

    assert metrics.wrong_product_leak_count == 1
    assert metrics.disallowed_leak_count == 1
    assert metrics.safe_gallery is False
    assert metrics.precision_at_5 == 0.5


def test_score_case_requires_all_negative_case_to_return_empty(tmp_path: Path) -> None:
    image = tmp_path / "wrong.jpg"
    image.write_bytes(b"wrong")
    case = EvalCase(
        case_id="negative",
        root=tmp_path,
        suites=("all",),
        target_product={"product_type": "target"},
        candidates=(EvalCandidate("wrong", image, _truth("OTHER_PRODUCT")),),
    )
    empty = StrategyOutput("test", (), {}, {}, (), 0, "empty")
    unsafe = StrategyOutput("test", ("wrong",), {}, {}, (), 0, "selected")

    assert score_case(case, empty).all_negative_pass is True
    assert score_case(case, unsafe).all_negative_pass is False


def test_cached_provider_replay_never_falls_through_to_network(tmp_path: Path) -> None:
    fake = _FakeProvider()
    request = {
        "task": "classify_supplier_listing_image_ownership",
        "context": {"target_product": {"product_type": "x"}},
        "grounded_sources": [
            {
                "source_id": "img_1",
                "source_type": "supplier_listing_image_candidate",
                "kind": "image",
                "image_path": "C:/private/path/image.jpg",
                "origin": "C:/private/path/image.jpg",
                "sha256": "abc",
                "source_index": 1,
            }
        ],
    }
    identity = {"provider": "fake", "model": "fake-model"}
    live = CachedProvider(
        delegate=fake,
        mode="live",
        cache_dir=tmp_path,
        strategy="current-v6",
        provider_identity=identity,
    )

    assert live.extract_json(request)["ok"] is True
    assert fake.calls == 1

    replay = CachedProvider(
        delegate=None,
        mode="replay",
        cache_dir=tmp_path,
        strategy="current-v6",
        provider_identity=identity,
    )
    assert replay.extract_json(request)["ok"] is True
    assert replay.network_calls == 0
    assert replay.cache_hits == 1

    changed = dict(request)
    changed["context"] = {"target_product": {"product_type": "different"}}
    with pytest.raises(ImageSelectionLabError, match="replay cache miss"):
        replay.extract_json(changed)


def test_cache_file_never_contains_local_image_path_and_image_sha_is_keyed(tmp_path: Path) -> None:
    fake = _FakeProvider()
    provider = CachedProvider(
        delegate=fake,
        mode="live",
        cache_dir=tmp_path,
        strategy="current-v6",
        provider_identity={"provider": "fake", "model": "fake-model"},
    )
    private_path = "D:/customer/private/supplier.jpg"
    request = {
        "task": "x",
        "grounded_sources": [
            {
                "source_id": "image_1",
                "source_type": "candidate",
                "kind": "image",
                "image_path": private_path,
                "origin": private_path,
                "sha256": "deadbeef",
                "source_index": 1,
            }
        ],
    }

    provider.extract_json(request)

    cache_files = sorted(tmp_path.rglob("*.json"))
    assert len(cache_files) == 1
    cache_text = cache_files[0].read_text(encoding="utf-8")
    assert private_path not in cache_text
    assert "deadbeef" not in cache_text
    assert "request_fingerprint" in cache_text

    changed_image = json.loads(json.dumps(request))
    changed_image["grounded_sources"][0]["sha256"] = "feedface"
    provider.extract_json(changed_image)

    assert fake.calls == 2
    assert len(list(tmp_path.rglob("*.json"))) == 2


def test_import_case_never_promotes_previous_ai_prediction_to_ground_truth(tmp_path: Path) -> None:
    source_image = tmp_path / "candidate.jpg"
    source_image.write_bytes(b"image")
    selection = tmp_path / "listing-image-selection.json"
    selection.write_text(
        json.dumps(
            {
                "target_product_identity": {"product_type": "target"},
                "selected_ids": ["image_01"],
                "candidates": [
                    {
                        "image_id": "image_01",
                        "path": str(source_image),
                        "ownership": {"classification": "EXACT_TARGET"},
                        "gallery": {"selected": True},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    case_json = import_case_from_selection(
        selection_json=selection,
        cases_root=tmp_path / "cases",
        case_id="real_failure_001",
    )
    payload = json.loads(case_json.read_text(encoding="utf-8"))

    assert payload["label_status"] == "needs_review"
    assert payload["candidates"][0]["ground_truth"] is None
    assert payload["candidates"][0]["previous_prediction"]["selected"] is True
    with pytest.raises(ImageSelectionLabError, match="not labelled"):
        load_case(case_json)


def test_current_v6_lab_reuses_full_production_blind_pipeline_symbols() -> None:
    source = Path("tools/image_selection_lab/core.py").read_text(encoding="utf-8")
    assert "build_listing_image_visual_facts_request" in source
    assert "_run_visual_facts_request" in source
    assert "build_listing_image_ownership_request" in source
    assert "build_listing_image_ranking_request" in source
    assert "_run_ownership_request" in source
    assert "_run_ranking_request" in source
    assert "qwen-vl-rerank-v1 is intentionally reserved but not implemented yet" in source
