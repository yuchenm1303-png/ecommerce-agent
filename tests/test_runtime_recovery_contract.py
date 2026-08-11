from __future__ import annotations

from app.makro.interruption_monitor import classify_failure_text
from app.makro.runtime_contract import (
    RecoveryAction,
    RecoveryPermission,
    RuntimeEvent,
    RuntimeState,
    permission_for,
)


def test_recovery_permissions_keep_risky_navigation_out_of_auto_safe() -> None:
    assert permission_for(RecoveryAction.WAIT) is RecoveryPermission.AUTO_SAFE
    assert permission_for(RecoveryAction.DISMISS_OVERLAY) is RecoveryPermission.AUTO_SAFE
    assert permission_for(RecoveryAction.CLOSE_POPUP) is RecoveryPermission.AUTO_VERIFY
    assert permission_for(RecoveryAction.RELOAD_PAGE) is RecoveryPermission.USER_CONFIRM
    assert permission_for(RecoveryAction.ASK_HUMAN_VERIFICATION) is RecoveryPermission.HUMAN_ONLY


def test_runtime_event_normalizes_progress_and_human_permission() -> None:
    event = RuntimeEvent(
        RuntimeState.WAITING_FOR_USER,
        "captcha",
        progress=130,
        action=RecoveryAction.ASK_HUMAN_VERIFICATION,
    )
    assert event.progress == 100
    assert event.requires_user is True
    assert event.permission is RecoveryPermission.HUMAN_ONLY


def test_known_joyride_failure_is_visible_but_not_business_action() -> None:
    event = classify_failure_text(
        '<div class="joyride-overlay"> intercepts pointer events',
        phase="Step 2",
        progress=31,
    )
    assert event.action is RecoveryAction.DISMISS_OVERLAY
    assert event.permission is RecoveryPermission.AUTO_SAFE
    assert event.progress == 31


def test_unknown_failure_stays_shadow_only() -> None:
    event = classify_failure_text("some completely new portal failure")
    assert event.state is RuntimeState.FAILED
    assert event.action is RecoveryAction.NONE
    assert event.advisor == "shadow"
