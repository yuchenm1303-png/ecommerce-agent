from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODAL = (ROOT / "gui" / "modal_interaction.py").read_text(encoding="utf-8")
CARD_FX = (ROOT / "gui" / "nekro_card_fx.py").read_text(encoding="utf-8")
NATIVE_BACKGROUND = (ROOT / "gui" / "native_background.py").read_text(encoding="utf-8")
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
    assert "QQuickWindow" in MODAL
    assert "OpacityAnimator" in MODAL
    assert "YAnimator" in MODAL
    assert "ScaleAnimator" in MODAL
    assert "Easing.OutQuart" in MODAL
    assert "transitionFinished" in MODAL
    assert "QGraphicsOpacityEffect" not in MODAL
    assert "QPropertyAnimation" not in MODAL
    assert "QParallelAnimationGroup" not in MODAL
    assert "QEasingCurve" not in MODAL
    assert "QQuickWidget" not in MODAL


def test_qml_transition_is_startup_safe_and_has_no_optional_effects_import() -> None:
    assert "import QtQuick.Effects" not in MODAL
    assert "MultiEffect" not in MODAL
    assert "property url blurUrl" in MODAL
    assert "id: blurImage" in MODAL
    assert "self.transition_window: QQuickWindow | None = None" in MODAL
    assert "self.transition_item: QQuickItem | None = None" in MODAL
    init_body = MODAL.split("def __init__", 1)[1].split("def _error_text", 1)[0]
    assert "QQuickWindow(self.quick_window)" not in init_body
    assert "QQmlComponent(self.engine" not in init_body
    assert "component.status()" in MODAL
    assert "QQmlComponent.Status.Ready" in MODAL
    assert "return False" in MODAL


def test_transition_uses_dedicated_native_quick_child_without_hiding_widget_tree() -> None:
    assert "surface = QQuickWindow(self.quick_window)" in MODAL
    assert 'surface.setObjectName("glassModalTransitionWindow")' in MODAL
    assert "Qt.WindowType.WindowDoesNotAcceptFocus" in MODAL
    assert "surface.setColor(QColor(0, 0, 0, 0))" in MODAL
    assert "surface.show()" in MODAL
    assert "surface.raise_()" in MODAL
    assert "surface.hide()" in MODAL
    assert "self.window.hide()" not in MODAL
    assert "self.window.show()" not in MODAL
    assert "self.details.drawer.grab()" in MODAL
    assert "setSizes(" not in MODAL
    assert 'b"geometry"' not in MODAL
    assert 'b"minimumHeight"' not in MODAL
    assert 'b"maximumHeight"' not in MODAL


def test_modal_transition_cannot_invalidate_native_glass_mask_via_parent_visibility() -> None:
    assert "QEvent.Type.Show" in NATIVE_BACKGROUND
    assert "QEvent.Type.Hide" in NATIVE_BACKGROUND
    assert "self.schedule_mask_update()" in NATIVE_BACKGROUND
    assert "self.window.hide()" not in MODAL
    assert "self.window.show()" not in MODAL


def test_opening_is_prepared_hidden_and_revealed_only_under_final_quick_frame() -> None:
    assert "self._original_show_prepared_modal = self.details._show_prepared_modal" in MODAL
    assert "self.details._show_prepared_modal = self._show_modal_with_transition" in MODAL
    assert "def _prepare_hidden_modal" in MODAL
    assert "self.details.backdrop.hide()" in MODAL
    assert "self.details.scrim.hide()" in MODAL
    assert "self.details.drawer.hide()" in MODAL
    assert "def _reveal_prepared_modal" in MODAL

    open_driver = MODAL.split("def _show_modal_with_transition", 1)[1].split(
        "def request_close", 1
    )[0]
    assert "base = self._prepare_hidden_modal(ratio=ratio)" in open_driver
    assert "self.details.drawer.grab()" in open_driver
    assert "self._state = _STATE_OPENING" in open_driver
    assert "self._issue_transition(closing=False)" in open_driver
    assert "self.details.drawer.show()" not in open_driver

    finish = MODAL.split("def _on_transition_finished", 1)[1].split("def eventFilter", 1)[0]
    open_branch = finish.split("if opened:", 1)[1].split(
        "if self._state != _STATE_CLOSING:", 1
    )[0]
    assert open_branch.index("self._state = _STATE_OPEN") < open_branch.index(
        "self._reveal_prepared_modal()"
    )
    assert open_branch.index("self._reveal_prepared_modal()") < open_branch.index(
        "self._hide_transition_surface()"
    )


def test_closing_hides_real_modal_before_transition_surface_is_removed() -> None:
    finish = MODAL.split("def _on_transition_finished", 1)[1].split("def eventFilter", 1)[0]
    close_branch = finish.split("if self._state != _STATE_CLOSING:", 1)[1]
    assert close_branch.index("self._state = _STATE_CLOSED") < close_branch.index(
        "self.details.close()"
    )
    assert close_branch.index("self.details.close()") < close_branch.index(
        "self._hide_transition_surface()"
    )


def test_preblurred_snapshot_replaces_runtime_qtquick_effect() -> None:
    assert "self._original_capture_backdrop = self.details._capture_backdrop" in MODAL
    assert "self.details._capture_backdrop = self._capture_raw_backdrop" in MODAL
    assert "blurred = self.details._blur_pixmap(base)" in MODAL
    assert 'item.setProperty("blurUrl", self._blur_url)' in MODAL
    assert 'suffix = ".png" if alpha else ".bmp"' in MODAL
    assert 'panel_url = self._publish_pixmap("modal_panel", panel, alpha=True)' in MODAL


def test_quick_failure_falls_back_to_atomic_modal_instead_of_crashing() -> None:
    assert "if not self._ensure_transition_surface():" in MODAL
    assert "self._fallback_open()" in MODAL
    assert "self._fallback_closed()" in MODAL
    assert "self._reveal_prepared_modal()" in MODAL
    assert "raise RuntimeError(\"Glass modal transition" not in MODAL


def test_any_transition_preparation_exception_cannot_trap_modal_state() -> None:
    assert "def _fallback_open" in MODAL
    assert "def _fallback_closed" in MODAL
    assert "def _error_text" in MODAL

    ensure_body = MODAL.split("def _ensure_transition_surface", 1)[1].split(
        "def _rewire_close_inputs", 1
    )[0]
    assert "except Exception as exc:" in ensure_body
    assert "self._transition_error = self._error_text(exc)" in ensure_body
    assert "return False" in ensure_body

    open_body = MODAL.split("def _show_modal_with_transition", 1)[1].split(
        "def request_close", 1
    )[0]
    assert "except Exception as exc:" in open_body
    assert "self._fallback_open(exc)" in open_body

    close_body = MODAL.split("def request_close", 1)[1].split(
        "def _on_transition_finished", 1
    )[0]
    assert "except Exception as exc:" in close_body
    assert "self._fallback_closed(exc)" in close_body

    fallback_open = MODAL.split("def _fallback_open", 1)[1].split("def _fallback_closed", 1)[0]
    assert "self._state = _STATE_OPEN" in fallback_open
    assert "self._transitioning = False" in fallback_open
    assert "self._reveal_prepared_modal()" in fallback_open
    assert "self._hide_transition_surface()" in fallback_open

    fallback_closed = MODAL.split("def _fallback_closed", 1)[1].split(
        "def _ensure_transition_surface", 1
    )[0]
    assert "self._state = _STATE_CLOSED" in fallback_closed
    assert "self.details.close()" in fallback_closed
    assert "self._hide_transition_surface()" in fallback_closed


def test_modal_lifecycle_no_longer_depends_on_drawer_show_reentry() -> None:
    for state in (
        "_STATE_CLOSED",
        "_STATE_OPENING",
        "_STATE_OPEN",
        "_STATE_CLOSING",
    ):
        assert state in MODAL
    assert "_STATE_OPENING_PENDING" not in MODAL
    assert "self.details.drawer.installEventFilter(self)" not in MODAL
    filter_body = MODAL.split("def eventFilter", 1)[1].split("def cleanup", 1)[0]
    assert "self.details.drawer" not in filter_body
    assert "QEvent.Type.Show" not in filter_body
    assert "QTimer.singleShot" not in MODAL


def test_open_close_motion_is_short_and_subtle() -> None:
    assert "_OPEN_MS = 235" in MODAL
    assert "_CLOSE_MS = 165" in MODAL
    assert "_PANEL_RISE_PX = 18" in MODAL
    assert "_PANEL_CLOSE_DROP_PX = 12" in MODAL
    assert "_PANEL_OPEN_SCALE = 0.985" in MODAL
    assert "_PANEL_CLOSE_SCALE = 0.990" in MODAL
    assert "function prepareOpen()" in MODAL
    assert "function prepareClose()" in MODAL
    assert "onActiveChanged:" in MODAL


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
