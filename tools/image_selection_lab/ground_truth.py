from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .core import AUTO_ALLOWED_CLASSES, ImageSelectionLabError, OWNERSHIP_CLASSES


GROUND_TRUTH_LABEL_SCHEMA_VERSION = 1


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ImageSelectionLabError(f"failed to read JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ImageSelectionLabError(f"JSON root must be an object: {path}")
    return dict(payload)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _capture_fingerprint(candidates: list[dict[str, Any]]) -> str:
    identity: list[dict[str, str]] = []
    for candidate in candidates:
        image_id = str(candidate.get("image_id") or "").strip()
        capture = candidate.get("capture")
        digest = str(capture.get("sha256") or "").strip().lower() if isinstance(capture, dict) else ""
        if not image_id or not digest:
            raise ImageSelectionLabError(
                "apply-ground-truth requires capture-case candidates with image_id and capture.sha256"
            )
        identity.append({"image_id": image_id, "sha256": digest})
    return _canonical_sha256(identity)


def _validate_expected_target(
    *,
    case_id: str,
    target_product: dict[str, Any],
    expected_target: Any,
) -> None:
    if expected_target in (None, {}):
        return
    if not isinstance(expected_target, dict):
        raise ImageSelectionLabError("ground-truth expected_target must be an object")
    for key, expected_raw in expected_target.items():
        expected = str(expected_raw or "").strip()
        if not expected:
            continue
        actual = str(target_product.get(str(key)) or "").strip()
        if actual.casefold() != expected.casefold():
            raise ImageSelectionLabError(
                f"ground-truth target mismatch for case {case_id}: "
                f"{key} expected={expected!r} actual={actual!r}"
            )


def _normalized_truth(*, case_id: str, image_id: str, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ImageSelectionLabError(
            f"ground-truth label for {case_id}/{image_id} must be an object"
        )
    ownership = str(raw.get("ownership") or "").strip().upper()
    if ownership not in OWNERSHIP_CLASSES:
        raise ImageSelectionLabError(
            f"ground-truth label for {case_id}/{image_id} has invalid ownership={ownership!r}"
        )
    try:
        grade = int(raw.get("relevance_grade"))
    except (TypeError, ValueError) as exc:
        raise ImageSelectionLabError(
            f"ground-truth label for {case_id}/{image_id} relevance_grade must be 0..3"
        ) from exc
    if grade not in {0, 1, 2, 3}:
        raise ImageSelectionLabError(
            f"ground-truth label for {case_id}/{image_id} relevance_grade must be 0..3"
        )
    main_allowed = raw.get("main_image_allowed")
    if type(main_allowed) is not bool:
        raise ImageSelectionLabError(
            f"ground-truth label for {case_id}/{image_id} main_image_allowed must be boolean"
        )

    auto_allowed = ownership in AUTO_ALLOWED_CLASSES
    if main_allowed and not auto_allowed:
        raise ImageSelectionLabError(
            f"ground-truth label for {case_id}/{image_id} forbids upload but allows main image"
        )
    if auto_allowed and grade == 0:
        raise ImageSelectionLabError(
            f"ground-truth label for {case_id}/{image_id} is uploadable but relevance_grade is 0"
        )
    if not auto_allowed and grade != 0:
        raise ImageSelectionLabError(
            f"ground-truth label for {case_id}/{image_id} is forbidden and must use relevance_grade 0"
        )

    return {
        "ownership": ownership,
        "auto_upload_allowed": auto_allowed,
        "relevance_grade": grade,
        "main_image_allowed": main_allowed,
    }


def apply_ground_truth_labels(
    *,
    case_json: str | Path,
    labels_json: str | Path,
) -> Path:
    """Apply a reviewed label set to one exact local capture-case.

    The label set owns only human semantic truth. The case retains the captured image
    files and SHA-256 evidence. Candidate coverage must match exactly so no image can
    silently remain unlabelled or inherit another image's answer.
    """

    case_path = Path(case_json).expanduser().resolve()
    labels_path = Path(labels_json).expanduser().resolve()
    if not case_path.is_file():
        raise ImageSelectionLabError(f"case JSON does not exist: {case_path}")
    if not labels_path.is_file():
        raise ImageSelectionLabError(f"ground-truth labels JSON does not exist: {labels_path}")

    case_payload = _read_json(case_path)
    labels_payload = _read_json(labels_path)
    if int(labels_payload.get("schema_version") or 0) != GROUND_TRUTH_LABEL_SCHEMA_VERSION:
        raise ImageSelectionLabError(
            f"unsupported ground-truth schema_version={labels_payload.get('schema_version')!r}"
        )

    case_id = str(case_payload.get("case_id") or "").strip()
    labels_case_id = str(labels_payload.get("case_id") or "").strip()
    if not case_id or labels_case_id != case_id:
        raise ImageSelectionLabError(
            f"ground-truth case_id mismatch: case={case_id!r} labels={labels_case_id!r}"
        )

    target_product = case_payload.get("target_product")
    if not isinstance(target_product, dict) or not target_product:
        raise ImageSelectionLabError(f"case {case_id} target_product is missing")
    _validate_expected_target(
        case_id=case_id,
        target_product=target_product,
        expected_target=labels_payload.get("expected_target"),
    )

    candidates_raw = case_payload.get("candidates")
    if not isinstance(candidates_raw, list) or not candidates_raw:
        raise ImageSelectionLabError(f"case {case_id} candidates are missing")
    candidates = [dict(item) for item in candidates_raw if isinstance(item, dict)]
    if len(candidates) != len(candidates_raw):
        raise ImageSelectionLabError(f"case {case_id} candidate entries must all be objects")

    expected_count = labels_payload.get("expected_candidate_count")
    if expected_count is not None and int(expected_count) != len(candidates):
        raise ImageSelectionLabError(
            f"ground-truth candidate count mismatch for {case_id}: "
            f"expected={expected_count} actual={len(candidates)}"
        )

    raw_labels = labels_payload.get("labels")
    if not isinstance(raw_labels, dict):
        raise ImageSelectionLabError("ground-truth labels must be an object keyed by image_id")

    candidate_ids = [str(candidate.get("image_id") or "").strip() for candidate in candidates]
    if not all(candidate_ids) or len(set(candidate_ids)) != len(candidate_ids):
        raise ImageSelectionLabError(f"case {case_id} has missing or duplicate image_id")
    label_ids = {str(image_id).strip() for image_id in raw_labels}
    candidate_id_set = set(candidate_ids)
    if label_ids != candidate_id_set:
        missing = sorted(candidate_id_set - label_ids)
        extra = sorted(label_ids - candidate_id_set)
        raise ImageSelectionLabError(
            f"ground-truth coverage mismatch for {case_id}: missing={missing} extra={extra}"
        )

    labelled_candidates: list[dict[str, Any]] = []
    for candidate in candidates:
        image_id = str(candidate.get("image_id") or "").strip()
        updated = dict(candidate)
        updated["ground_truth"] = _normalized_truth(
            case_id=case_id,
            image_id=image_id,
            raw=raw_labels.get(image_id),
        )
        labelled_candidates.append(updated)

    capture_fingerprint = _capture_fingerprint(labelled_candidates)
    label_set_sha256 = _canonical_sha256(labels_payload)
    case_payload["candidates"] = labelled_candidates
    case_payload["label_status"] = "human_verified"
    case_payload["ground_truth_manifest"] = {
        "schema_version": GROUND_TRUTH_LABEL_SCHEMA_VERSION,
        "label_set": labels_path.name,
        "label_set_sha256": label_set_sha256,
        "capture_fingerprint": capture_fingerprint,
        "review_basis": str(labels_payload.get("review_basis") or "").strip(),
        "reviewed_candidate_count": len(labelled_candidates),
    }
    _write_json(case_path, case_payload)
    return case_path


__all__ = [
    "GROUND_TRUTH_LABEL_SCHEMA_VERSION",
    "apply_ground_truth_labels",
]
