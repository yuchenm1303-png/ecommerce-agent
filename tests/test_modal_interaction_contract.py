from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODAL = (ROOT / "gui" / "modal_interaction.py").read_text(encoding="utf-8")
CARD_FX = (ROOT / "gui" / "nekro_card_fx.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_gpu_modal_interaction_is_installed_for_shared_details() -> None:
    assert "from gui.modal_interaction import install_modal_interaction" in RUNNER
    assert "details = install_card_details(window)" in RUNNER
    assert "install_console_summary_mode(window)" in RUNNER
    assert "install_modal_interaction(window, details)" in RUNNER
    assert RUNNER.index("install_console_summary_mode(window)") < RUNNER.index(
        "install_modal_interaction(window, details)"
    )
    assert RUNNER.index("install_modal_interaction(window, details)") < RUNNER.index("shell.show()")


def test_modal_transition_uses_threaded_quick_animators_not_widget_animation() -> None:
    assert "QQmlComponent" in MODAL
    assert "QQuickItem" in MODAL
    assert "MultiEffect" in MODAL
    assert "OpacityAnimator" in MODAL
    assert "YAnimator" in MODAL
    assert "ScaleAnimator" in MODAL
    assert "Easing.OutQuart" in MODAL
    assert "transitionFinished" in MODAL
    assert "QGraphicsOpacityEffect" not in MODAL
    assert "QPropertyAnimation" not in MODAL
    assert "QParallelAnimationGroup" not in MODAL
    assert "QEasingCurve" not in MODAL


def test_real_widget_tree_is_removed_from_transition_hot_path() -> None:
    assert "self.window.hide()" in MODAL
    assert "self.window.show()" in MODAL
    assert "self.details.drawer.grab()" in MODAL
    assert 'pixmap.save(str(path), "BMP")' in MODAL
    assert 'self.transition_item.setProperty("active", True)' in MODAL
    assert "setSizes(" not in MODAL
    assert 'b"geometry"' not in MODAL
    assert 'b"minimumHeight"' not in MODAL
    assert 'b"maximumHeight"' not in MODAL


def test_cpu_backdrop_blur_is_deferred_until_quick_animation_started() -> None:
    assert "self._original_capture_backdrop = self.details._capture_backdrop" in MODAL
    assert "self.details._capture_backdrop = self._capture_raw_backdrop" in MODAL
    assert "QTimer.singleShot(24, self._prepare_static_blur)" in MODAL
    assert "self.details._blur_pixmap(self._base_snapshot)" in MODAL


def test_open_close_motion_is_short_and_subtle() -> None:
    assert "_OPEN_MS = 235" in MODAL
    assert "_CLOSE_MS = 165" in MODAL
    assert "_PANEL_RISE_PX = 18" in MODAL
    assert "_PANEL_CLOSE_DROP_PX = 12" in MODAL
    assert "_PANEL_OPEN_SCALE = 0.985" in MODAL
    assert "_PANEL_CLOSE_SCALE = 0.990" in MODAL


def test_passive_card_text_becomes_part_of_card_hit_surface() -> None:
    assert "def _label_is_passive" in MODAL
    assert "Qt.TextInteractionFlag.TextSelectableByMouse" in MODAL
    assert "Qt.TextInteractionFlag.LinksAccessibleByMouse" in MODAL
    assert "WA_TransparentForMouseEvents" in MODAL
    assert "label.installEventFilter(self)" not in MODAL
    assert "QApplication.instance().installEventFilter" not in MODAL


def test_card_hover_is_event_driven_not_pointer_polled() -> None:
    assert "_ANIMATION_FRAME_MS = 8" in CARD_FX
    assert "_POINTER_SAMPLE_MS" not in CARD_FX
    assert "_sample_timer" not in CARD_FX
    assert "QCursor" not in CARD_FX
    assert "QApplication" not in CARD_FX
    assert "frame.installEventFilter(self)" in CARD_FX
    assert "QEvent.Type.Enter" in CARD_FX
    assert "QEvent.Type.Leave" in CARD_FX
    assert "QEvent.Type.MouseButtonPress" in CARD_FX
    assert "QEvent.Type.MouseButtonRelease" in CARD_FX
    assert "_HOVER_SECONDS = 0.065" in CARD_FX
    assert "_PRESS_SECONDS = 0.040" in CARD_FX
    assert "_RELEASE_SECONDS = 0.085" in CARD_FX


def test_modal_interaction_source_compiles_without_importing_pyside() -> None:
    compile(MODAL, str(ROOT / "gui" / "modal_interaction.py"), "exec")
    compile(CARD_FX, str(ROOT / "gui" / "nekro_card_fx.py"), "exec")
