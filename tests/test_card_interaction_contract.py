from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CARD_FX = (ROOT / "gui" / "nekro_card_fx.py").read_text(encoding="utf-8")


def _body(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_card_interaction_watches_the_entire_widget_subtree() -> None:
    assert "self._watched_to_card: dict[QObject, QFrame]" in CARD_FX
    assert "def _nearest_card" in CARD_FX
    assert "def _watch_widget" in CARD_FX
    assert "def _register_widget_tree" in CARD_FX
    assert "root.findChildren(QWidget)" in CARD_FX
    assert "widget.setMouseTracking(True)" in CARD_FX
    assert "widget.installEventFilter(self)" in CARD_FX


def test_mouse_move_and_enter_both_confirm_hover_without_polling() -> None:
    event_filter = _body(CARD_FX, "def eventFilter", "def _cleanup")
    assert "QEvent.Type.Enter" in event_filter
    assert "QEvent.Type.MouseMove" in event_filter
    assert "self._enter(frame)" in event_filter
    assert "_sample_timer" not in CARD_FX
    assert "QApplication.instance().installEventFilter" not in CARD_FX


def test_child_leave_does_not_cancel_hover_until_pointer_leaves_whole_card() -> None:
    event_filter = _body(CARD_FX, "def eventFilter", "def _cleanup")
    assert "QEvent.Type.Leave" in event_filter
    assert "if not self._cursor_inside_card(frame):" in event_filter
    assert "self._leave(frame)" in event_filter
    assert "frame.mapFromGlobal(QCursor.pos())" in CARD_FX


def test_every_left_press_commits_active_feedback_synchronously() -> None:
    press = _body(CARD_FX, "def _press", "def _release")
    assert "self._snap(frame, _ACTIVE_ALPHA)" in press
    assert "self._animate(frame, _ACTIVE_ALPHA" not in press

    snap = _body(CARD_FX, "def _snap", "def _enter")
    assert "state.snap(alpha)" in snap

    state_snap = _body(CARD_FX, "def snap(self, alpha", "def freeze")
    assert "self.surface.set_interaction" in state_snap
    assert "self.animating = False" in state_snap


def test_release_uses_existing_hover_or_normal_easing() -> None:
    release = _body(CARD_FX, "def _release", "def _reset_card")
    assert "target = _HOVER_ALPHA" in release
    assert "target = _NORMAL_ALPHA" in release
    assert "self._animate(frame, target, _RELEASE_SECONDS)" in release
    assert "_HOVER_ALPHA = 90.0" in CARD_FX
    assert "_ACTIVE_ALPHA = 110.0" in CARD_FX
    assert "_RELEASE_SECONDS = 0.085" in CARD_FX


def test_dynamic_card_children_are_registered_without_consuming_events() -> None:
    event_filter = _body(CARD_FX, "def eventFilter", "def _cleanup")
    assert "QEvent.Type.ChildAdded" in event_filter
    assert "self._register_widget_tree(child)" in event_filter
    assert event_filter.rstrip().endswith("return False")


def test_modal_suspend_contract_is_preserved() -> None:
    assert "def suspend_for_modal" in CARD_FX
    assert "def resume_from_modal" in CARD_FX
    assert "self._animation_timer.stop()" in CARD_FX
    assert "state.freeze()" in CARD_FX


def test_card_interaction_source_compiles_without_importing_pyside() -> None:
    compile(CARD_FX, str(ROOT / "gui" / "nekro_card_fx.py"), "exec")
