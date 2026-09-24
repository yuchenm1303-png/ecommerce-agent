from app.makro.photo_acceptance import (
    harden_completion_result,
    harden_execution_outcome,
    photo_requirement_satisfied,
)


def test_zero_requested_zero_persisted_is_not_photo_complete():
    report = {
        "requested": 0,
        "final_count": 0,
        "initial_count": 0,
        "status": "skipped",
        "listing_photo_requirement_satisfied": False,
        "persistence": {"final_count": 0},
    }
    assert photo_requirement_satisfied(report) is False


def test_preexisting_persisted_photo_satisfies_requirement_without_new_upload():
    report = {
        "requested": 0,
        "final_count": 1,
        "initial_count": 1,
        "status": "persisted_verified",
        "listing_photo_requirement_satisfied": True,
        "persistence": {"final_count": 1},
    }
    assert photo_requirement_satisfied(report) is True


def test_legacy_report_falls_back_to_observed_final_count():
    report = {
        "requested": 0,
        "final_count": 2,
        "initial_count": 2,
        "persistence": {"final_count": 2},
    }
    assert photo_requirement_satisfied(report) is True


def test_completion_cannot_be_strict_when_product_photos_are_missing():
    report = {
        "requested": 0,
        "final_count": 0,
        "listing_photo_requirement_satisfied": False,
    }
    completion = {
        "draft_persisted_complete": True,
        "autofill_safe_complete": True,
        "photo_requirement_satisfied": True,
    }
    hardened = harden_completion_result(completion, report)
    assert hardened["photo_requirement_satisfied"] is False
    assert hardened["draft_persisted_complete"] is False
    assert hardened["autofill_safe_complete"] is False
    assert hardened["photo_acceptance_reason"] == "mandatory_product_photo_missing"


def test_old_success_outcome_is_downgraded_when_photos_are_missing():
    report = {
        "requested": 0,
        "final_count": 0,
        "listing_photo_requirement_satisfied": False,
    }
    outcome = {
        "status": "success",
        "reason": "strict_acceptance_complete",
        "process_success": True,
        "strict_acceptance_complete": True,
    }
    hardened = harden_execution_outcome(outcome, report)
    assert hardened["status"] == "partial_success"
    assert hardened["reason"] == "mandatory_product_photo_missing"
    assert hardened["strict_acceptance_complete"] is False
    assert hardened["photo_requirement_satisfied"] is False
