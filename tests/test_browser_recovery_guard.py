from __future__ import annotations

from app.browser_recovery_guard import BrowserRecoveryGuard


def test_transient_endpoint_miss_does_not_allow_launch() -> None:
    guard = BrowserRecoveryGuard(offline_threshold=2)

    guard.observe_endpoint(False)

    assert guard.offline_observations == 1
    assert guard.offline_confirmed is False
    assert guard.claim_launch(require_offline_confirmation=True) is False


def test_only_one_launch_is_allowed_while_endpoint_stays_offline() -> None:
    guard = BrowserRecoveryGuard(offline_threshold=2)

    guard.observe_endpoint(False)
    guard.observe_endpoint(False)

    assert guard.offline_confirmed is True
    assert guard.claim_launch(require_offline_confirmation=True) is True
    assert guard.claim_launch(require_offline_confirmation=True) is False

    for _ in range(5):
        guard.observe_endpoint(False)

    assert guard.claim_launch(require_offline_confirmation=True) is False


def test_endpoint_visibility_alone_does_not_rearm_failed_launch() -> None:
    guard = BrowserRecoveryGuard(offline_threshold=1)

    guard.observe_endpoint(False)
    assert guard.claim_launch(require_offline_confirmation=True) is True

    guard.observe_endpoint(True)

    assert guard.launch_attempted is True
    assert guard.claim_launch(require_offline_confirmation=False) is False

    guard.record_automation_ready()

    assert guard.launch_attempted is False
    assert guard.claim_launch(require_offline_confirmation=False) is True


def test_new_outage_after_attempted_generation_disappears_can_recover_once() -> None:
    guard = BrowserRecoveryGuard(offline_threshold=2)

    guard.observe_endpoint(False)
    guard.observe_endpoint(False)
    assert guard.claim_launch(require_offline_confirmation=True) is True

    # The attempted browser generation became visible, then was explicitly gone.
    guard.observe_endpoint(True)
    guard.observe_endpoint(False)

    assert guard.launch_attempted is False
    assert guard.offline_observations == 1
    assert guard.claim_launch(require_offline_confirmation=True) is False

    guard.observe_endpoint(False)

    assert guard.claim_launch(require_offline_confirmation=True) is True
