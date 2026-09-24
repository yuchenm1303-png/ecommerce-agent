from __future__ import annotations

from app.makro.page_observation import InteractiveElement, PageObservation
from app.makro.recovery_agent import build_recovery_request, parse_recovery_response
from app.makro.runtime_contract import RecoveryAction, RecoveryPermission


def _observation(*, human: bool = False, login: bool = False) -> PageObservation:
    return PageObservation(
        url="https://seller.makro.co.za/index.html#dashboard/addListings/single",
        title="Makro",
        detected_stage="brand",
        expected_stage="brand",
        interactive_elements=(
            InteractiveElement("E001", "button", "button", "Close"),
            InteractiveElement("E002", "button", "button", "Check Brand"),
        ),
        human_verification=human,
        login_required=login,
    )


def test_recovery_request_is_advisory_and_business_actions_are_forbidden() -> None:
    request = build_recovery_request(_observation(), last_successful_action="brand filled")
    assert request["task"] == "advise_makro_runtime_recovery"
    assert "SEND_TO_QC" in request["context"]["forbidden_business_actions"]
    assert "CHANGE_VERTICAL" in request["context"]["forbidden_business_actions"]
    assert "CLOSE_POPUP" in request["context"]["allowed_actions"]


def test_recovery_response_must_reference_current_observation_element() -> None:
    raw = {
        "observed_state": "STEP_2_BRAND",
        "interruption": "UNKNOWN_MODAL",
        "action": "CLOSE_POPUP",
        "target_element_id": "E001",
        "explanation": "dismiss modal",
        "expected_after": "STEP_2_BRAND",
        "confidence": 0.96,
        "requires_user": False,
    }
    proposal = parse_recovery_response(raw, _observation())
    assert proposal.action is RecoveryAction.CLOSE_POPUP
    assert proposal.permission is RecoveryPermission.AUTO_VERIFY


def test_human_verification_cannot_be_auto_recovered() -> None:
    raw = {
        "observed_state": "VERIFY",
        "interruption": "HUMAN_VERIFICATION",
        "action": "CLOSE_POPUP",
        "target_element_id": "E001",
        "explanation": "wrong",
        "expected_after": "STEP_2_BRAND",
        "confidence": 0.9,
        "requires_user": False,
    }
    try:
        parse_recovery_response(raw, _observation(human=True))
    except ValueError as exc:
        assert "Human verification" in str(exc)
    else:
        raise AssertionError("human verification must never become automatic")


def test_login_observation_requires_human_login_action() -> None:
    raw = {
        "observed_state": "LOGIN",
        "interruption": "LOGIN_REQUIRED",
        "action": "ASK_HUMAN_LOGIN",
        "target_element_id": "",
        "explanation": "login expired",
        "expected_after": "STEP_2_BRAND",
        "confidence": 0.99,
        "requires_user": True,
    }
    proposal = parse_recovery_response(raw, _observation(login=True))
    assert proposal.action is RecoveryAction.ASK_HUMAN_LOGIN
    assert proposal.requires_user is True
