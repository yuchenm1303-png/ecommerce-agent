from __future__ import annotations

import app.makro.portal_interruptions as interruptions
import app.makro.step3_transition as transition


def test_reconciler_drains_stacked_safe_interruptions(monkeypatch) -> None:
    state = ["joyride", "tutorial", "cookie"]

    def consume(kind: str) -> bool:
        if state and state[0] == kind:
            state.pop(0)
            return True
        return False

    monkeypatch.setattr(interruptions, "_dismiss_joyride", lambda _page: consume("joyride"))
    monkeypatch.setattr(
        interruptions,
        "_dismiss_presentation_dialog",
        lambda _page: consume("tutorial"),
    )
    monkeypatch.setattr(
        interruptions,
        "_dismiss_cookie_notice",
        lambda _page: consume("cookie"),
    )

    assert interruptions.reconcile_portal_interruptions(object()) == 3
    assert state == []


def test_reconciler_leaves_unknown_modal_untouched(monkeypatch) -> None:
    monkeypatch.setattr(interruptions, "_dismiss_joyride", lambda _page: False)
    monkeypatch.setattr(interruptions, "_dismiss_presentation_dialog", lambda _page: False)
    monkeypatch.setattr(interruptions, "_dismiss_cookie_notice", lambda _page: False)

    assert interruptions.reconcile_portal_interruptions(object()) == 0


def test_tour_dismiss_actions_do_not_advance_tour() -> None:
    assert "Skip" in interruptions._TOUR_DISMISS_LABELS
    assert "Done" in interruptions._TOUR_DISMISS_LABELS
    assert "Next" not in interruptions._TOUR_DISMISS_LABELS


def test_legacy_joyride_entry_uses_unified_reconciler(monkeypatch) -> None:
    monkeypatch.setattr(transition, "reconcile_portal_interruptions", lambda _page: 3)
    assert transition.dismiss_joyride_overlay(object()) is True
