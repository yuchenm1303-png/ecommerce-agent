from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODAL = (ROOT / "gui" / "modal_interaction.py").read_text(encoding="utf-8")
CARD_FX = (ROOT / "gui" / "nekro_card_fx.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_refined_modal_interaction_is_installed_for_shared_details() -> None:
    assert "from gui.modal_interaction import install_modal_interaction" in RUNNER
    assert "details = install_card_details(window)" in RUNNER
    assert "install_console_summary_mode(window)" in RUNNER
    assert "install_modal_interaction(window, details)" in RUNNER
    assert RUNNER.index("install_console_summary_mode(window)") < RUNNER.index(
        "install_modal_interaction(window, details)"
    )
    assert RUNNER.index("install_modal_interaction(window, details)") < RUNNER.index("shell.show()")


def test_modal_animation_only_moves_cheap_presentation_layers() -> None:
    assert "QParallelAnimationGroup" in MODAL
    assert "QPropertyAnimation" in MODAL
    assert "self.backdrop_effect" in MODAL
    assert "self.scrim_effect" in MODAL
    assert 'b"opacity"' in MODAL
    assert 'b"pos"' in MODAL
    assert "_PANEL_RISE_PX = 14" in MODAL
    assert "_PANEL_CLOSE_DROP_PX = 10" in MODAL
    assert 'b"geometry"' not in MODAL
    assert 'b"minimumHeight"' not in MODAL
    assert 'b"maximumHeight"' not in MODAL
    assert "setSizes(" not in MODAL
    assert "resize(" not in MODAL


def test_large_modal_panel_is_not_opacity_composited_each_frame() -> None:
    assert "self.details.drawer.setGraphicsEffect(None)" in MODAL
    assert "QGraphicsOpacityEffect(self.details.backdrop)" in MODAL
    assert "QGraphicsOpacityEffect(self.details.scrim)" in MODAL
    assert "QGraphicsOpacityEffect(self.details.drawer)" not in MODAL


def test_open_and_close_have_short_layered_timings() -> None:
    assert "_OPEN_BACKDROP_MS = 170" in MODAL
    assert "_OPEN_SCRIM_MS = 180" in MODAL
    assert "_OPEN_PANEL_MS = 210" in MODAL
    assert "_CLOSE_BACKDROP_MS = 145" in MODAL
    assert "_CLOSE_SCRIM_MS = 150" in MODAL
    assert "_CLOSE_PANEL_MS = 160" in MODAL
    assert "QEasingCurve.Type.OutCubic" in MODAL
    assert "QEasingCurve.Type.OutQuart" in MODAL
    assert "QEasingCurve.Type.InCubic" in MODAL


def test_passive_card_text_forwards_click_but_selectable_text_does_not() -> None:
    assert "def _label_is_passive" in MODAL
    assert "Qt.TextInteractionFlag.TextSelectableByMouse" in MODAL
    assert "Qt.TextInteractionFlag.LinksAccessibleByMouse" in MODAL
    assert "label.installEventFilter(self)" in MODAL
    assert "self._label_cards[label] = card" in MODAL
    assert "self.details.open(card)" in MODAL
    # Do not go back to filtering every QWidget in the application tree.
    assert "widget.installEventFilter(self)" not in MODAL
    assert "QApplication.instance().installEventFilter" not in MODAL


def test_card_hover_and_press_start_within_one_high_refresh_frame() -> None:
    assert "_POINTER_SAMPLE_MS = 8" in CARD_FX
    assert "_ANIMATION_FRAME_MS = 8" in CARD_FX
    assert "self._sample_timer.setTimerType(Qt.TimerType.PreciseTimer)" in CARD_FX
    assert "_HOVER_SECONDS = 0.07" in CARD_FX
    assert "_PRESS_SECONDS = 0.045" in CARD_FX
    assert "_RELEASE_SECONDS = 0.09" in CARD_FX
    assert "QTimer.singleShot(0, self._start_sampling_if_visible)" in CARD_FX


def test_modal_interaction_source_compiles_without_importing_pyside() -> None:
    compile(MODAL, str(ROOT / "gui" / "modal_interaction.py"), "exec")
