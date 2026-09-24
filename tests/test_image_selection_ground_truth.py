from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.image_selection_lab.core import ImageSelectionLabError, load_case
from tools.image_selection_lab.ground_truth import apply_ground_truth_labels


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _make_case(tmp_path: Path) -> Path:
    case_dir = tmp_path / "cases" / "case_001"
    images = case_dir / "images"
    images.mkdir(parents=True)
    for name in ("img_001.jpg", "img_002.jpg", "img_003.jpg"):
        (images / name).write_bytes(b"not-decoded-in-label-test")
    case_path = case_dir / "case.json"
    _write_json(
        case_path,
        {
            "schema_version": 1,
            "case_id": "case_001",
            "suite": ["test"],
            "label_status": "needs_review",
            "target_product": {
                "name": "Target Product",
                "brand": "Brand",
                "model": "Model-1",
            },
            "candidates": [
                {
                    "image_id": "img_001",
                    "file": "images/img_001.jpg",
                    "ground_truth": None,
                    "capture": {"sha256": "a" * 64},
                },
                {
                    "image_id": "img_002",
                    "file": "images/img_002.jpg",
                    "ground_truth": None,
                    "capture": {"sha256": "b" * 64},
                },
                {
                    "image_id": "img_003",
                    "file": "images/img_003.jpg",
                    "ground_truth": None,
                    "capture": {"sha256": "c" * 64},
                },
            ],
        },
    )
    return case_path


def _labels_payload() -> dict:
    return {
        "schema_version": 1,
        "case_id": "case_001",
        "review_basis": "human-reviewed contact sheet",
        "expected_candidate_count": 3,
        "expected_target": {"brand": "Brand", "model": "Model-1"},
        "labels": {
            "img_001": {
                "ownership": "EXACT_TARGET",
                "relevance_grade": 3,
                "main_image_allowed": True,
            },
            "img_002": {
                "ownership": "TARGET_PACKAGING_OR_DETAIL",
                "relevance_grade": 2,
                "main_image_allowed": False,
            },
            "img_003": {
                "ownership": "OTHER_PRODUCT",
                "relevance_grade": 0,
                "main_image_allowed": False,
            },
        },
    }


def test_apply_ground_truth_derives_upload_permission_and_freezes_capture(tmp_path: Path) -> None:
    case_path = _make_case(tmp_path)
    labels_path = tmp_path / "labels.json"
    _write_json(labels_path, _labels_payload())

    result = apply_ground_truth_labels(case_json=case_path, labels_json=labels_path)

    assert result == case_path.resolve()
    payload = json.loads(case_path.read_text(encoding="utf-8"))
    assert payload["label_status"] == "human_verified"
    assert payload["candidates"][0]["ground_truth"]["auto_upload_allowed"] is True
    assert payload["candidates"][1]["ground_truth"]["auto_upload_allowed"] is True
    assert payload["candidates"][2]["ground_truth"]["auto_upload_allowed"] is False
    manifest = payload["ground_truth_manifest"]
    assert manifest["reviewed_candidate_count"] == 3
    assert len(manifest["capture_fingerprint"]) == 64
    assert len(manifest["label_set_sha256"]) == 64


def test_applied_ground_truth_is_accepted_by_eval_case_loader(tmp_path: Path, monkeypatch) -> None:
    case_path = _make_case(tmp_path)
    labels_path = tmp_path / "labels.json"
    _write_json(labels_path, _labels_payload())
    apply_ground_truth_labels(case_json=case_path, labels_json=labels_path)

    monkeypatch.setattr(
        "tools.image_selection_lab.core.has_decodable_image_pixels",
        lambda _path: True,
    )
    case = load_case(case_path)
    assert case.case_id == "case_001"
    assert [candidate.truth.ownership for candidate in case.candidates] == [
        "EXACT_TARGET",
        "TARGET_PACKAGING_OR_DETAIL",
        "OTHER_PRODUCT",
    ]


def test_ground_truth_requires_exact_candidate_coverage(tmp_path: Path) -> None:
    case_path = _make_case(tmp_path)
    labels = _labels_payload()
    labels["labels"].pop("img_003")
    labels_path = tmp_path / "labels.json"
    _write_json(labels_path, labels)

    with pytest.raises(ImageSelectionLabError, match="coverage mismatch"):
        apply_ground_truth_labels(case_json=case_path, labels_json=labels_path)


def test_ground_truth_refuses_target_mismatch(tmp_path: Path) -> None:
    case_path = _make_case(tmp_path)
    labels = _labels_payload()
    labels["expected_target"]["model"] = "Wrong-Model"
    labels_path = tmp_path / "labels.json"
    _write_json(labels_path, labels)

    with pytest.raises(ImageSelectionLabError, match="target mismatch"):
        apply_ground_truth_labels(case_json=case_path, labels_json=labels_path)


def test_forbidden_candidate_cannot_be_main_image(tmp_path: Path) -> None:
    case_path = _make_case(tmp_path)
    labels = _labels_payload()
    labels["labels"]["img_003"]["main_image_allowed"] = True
    labels_path = tmp_path / "labels.json"
    _write_json(labels_path, labels)

    with pytest.raises(ImageSelectionLabError, match="forbids upload but allows main image"):
        apply_ground_truth_labels(case_json=case_path, labels_json=labels_path)


def test_b01_dyson_ground_truth_contract_is_complete() -> None:
    path = Path("evals/image_selection/ground_truth/B01_dyson_hs05_makro.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    labels = payload["labels"]

    assert payload["expected_candidate_count"] == 25
    assert set(labels) == {f"img_{index:03d}" for index in range(1, 26)}
    allowed = {
        image_id
        for image_id, truth in labels.items()
        if truth["ownership"] in {"EXACT_TARGET", "TARGET_PACKAGING_OR_DETAIL"}
    }
    assert allowed == {"img_001", "img_002", "img_003", "img_005"}
    assert labels["img_004"]["ownership"] == "PAGE_ASSET"
    assert all(
        labels[f"img_{index:03d}"]["ownership"] == "OTHER_PRODUCT"
        for index in range(6, 26)
    )
