from __future__ import annotations

import pytest

from app.providers.input_rejection_fallback import _is_image_inspection_rejection


def test_data_inspection_marker_detection_is_case_insensitive_and_chain_aware() -> None:
    cause = RuntimeError(
        "InternalError.Algo.DataInspectionFailed: Input image data may contain inappropriate content."
    )
    wrapped = RuntimeError("OpenAI-compatible JSON task 调用失败")
    wrapped.__cause__ = cause

    assert _is_image_inspection_rejection(wrapped) is True


def test_unrelated_bad_request_is_not_classified_as_image_inspection() -> None:
    assert _is_image_inspection_rejection(RuntimeError("400 invalid_request_error")) is False
