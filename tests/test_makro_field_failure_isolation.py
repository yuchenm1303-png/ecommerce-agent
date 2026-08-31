from __future__ import annotations

from makro_execute_listing import _execution_outcome


def _completion(*, complete: bool) -> dict[str, bool]:
    return {
        "draft_persisted_complete": complete,
        "autofill_safe_complete": complete,
    }


def test_one_field_failure_is_partial_success_when_siblings_persisted():
    reports = [
        {
            "section": "Additional Description",
            "status": "persisted_partial",
            "candidate_count": 33,
            "persisted_verified": 32,
            "validation_failed": 1,
            "fill_error": 0,
            "persisted_validation_failed": 0,
            "optional_prewrite_skipped": 0,
        }
    ]

    outcome = _execution_outcome(reports, None, _completion(complete=False))

    assert outcome["status"] == "partial_success"
    assert outcome["process_success"] is True
    assert outcome["persisted_fields"] == 32
    assert outcome["isolated_field_issues"] == 1


def test_structural_section_failure_remains_process_failure():
    reports = [
        {
            "section": "Product Description",
            "status": "save_failed",
            "candidate_count": 11,
            "persisted_verified": 0,
            "validation_failed": 0,
            "fill_error": 0,
            "persisted_validation_failed": 0,
            "optional_prewrite_skipped": 0,
        }
    ]

    outcome = _execution_outcome(reports, None, _completion(complete=False))

    assert outcome["status"] == "failed"
    assert outcome["process_success"] is False
    assert outcome["reason"] == "structural_execution_failure"


def test_all_field_failures_with_no_persisted_success_remain_failed():
    reports = [
        {
            "section": "Additional Description",
            "status": "no_validated_writes",
            "candidate_count": 2,
            "persisted_verified": 0,
            "validation_failed": 2,
            "fill_error": 0,
            "persisted_validation_failed": 0,
            "optional_prewrite_skipped": 0,
        }
    ]

    outcome = _execution_outcome(reports, None, _completion(complete=False))

    assert outcome["status"] == "failed"
    assert outcome["reason"] == "no_persisted_success"


def test_strict_complete_run_without_field_issues_is_success():
    reports = [
        {
            "section": "Product Description",
            "status": "persisted_verified",
            "candidate_count": 11,
            "persisted_verified": 11,
            "validation_failed": 0,
            "fill_error": 0,
            "persisted_validation_failed": 0,
            "optional_prewrite_skipped": 0,
        }
    ]

    outcome = _execution_outcome(reports, None, _completion(complete=True))

    assert outcome["status"] == "success"
    assert outcome["process_success"] is True
