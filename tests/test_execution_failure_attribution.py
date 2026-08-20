from __future__ import annotations

from gui.task_failure_diagnostics import execution_report_failure_summary


def test_field_failure_outranks_later_successful_photo_phase() -> None:
    report = {
        "section_reports": [
            {
                "section": "Additional Description",
                "status": "partial_persisted",
                "results": [
                    {
                        "attribute_key": "other_functions",
                        "label": "Other Functions",
                        "execution_status": "validation_failed",
                        "verification": {
                            "status": "validation_failed",
                            "detail": "settled readback did not match approved answer",
                        },
                    }
                ],
            }
        ],
        "photo_upload": {
            "requested": 5,
            "attempted": 5,
            "persisted": 5,
            "status": "persisted_verified",
        },
        "completion": {
            "photos_persisted": True,
            "required_blocked": 0,
            "required_field_cards_persisted": True,
            "draft_persisted_complete": False,
        },
    }

    failure = execution_report_failure_summary(report)

    assert failure["stage"] == "Additional Description / Other Functions"
    assert failure["field"] == "Other Functions"
    assert failure["status"] == "validation_failed"
    assert failure["error_type"] == "FieldValidationFailure"


def test_photo_failure_is_reported_only_when_photo_acceptance_failed() -> None:
    report = {
        "section_reports": [
            {
                "section": "Additional Description",
                "status": "persisted_verified",
                "results": [
                    {
                        "label": "Other Functions",
                        "execution_status": "validated",
                    }
                ],
            }
        ],
        "photo_upload": {
            "requested": 5,
            "attempted": 5,
            "persisted": 4,
            "status": "persistence_failed",
            "detail": "only 4/5 photos persisted",
        },
        "completion": {
            "photos_persisted": False,
            "required_blocked": 0,
            "required_field_cards_persisted": True,
            "draft_persisted_complete": False,
        },
    }

    failure = execution_report_failure_summary(report)

    assert failure["stage"] == "Product Photos"
    assert failure["status"] == "persistence_failed"
    assert failure["error_type"] == "PhotoPersistenceFailure"
