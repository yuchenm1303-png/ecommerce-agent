from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = (ROOT / "gui" / "static_modal_interaction.py").read_text(encoding="utf-8")
DETAILS = (ROOT / "gui" / "card_details_fast.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def _body(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_runtime_uses_widget_modal_and_never_installs_second_quick_window() -> None:
    assert "from gui.static_modal_interaction import install_static_modal_interaction" in RUNNER
    assert "install_static_modal_interaction(window, details)" in RUNNER
    assert "from gui.modal_interaction import install_modal_interaction" not in RUNNER
    assert "install_modal_interaction(window, details)" not in RUNNER
    assert "modal_overlay_zorder" not in RUNNER
    assert "QQuickWindow(" not in STATIC
    assert "QQmlComponent" not in STATIC
    assert "QQuickItem" not in STATIC


def test_panel_motion_is_snapshot_only_and_mouse_transparent() -> None:
    assert "class _PanelTransitionWidget(QWidget)" in STATIC
    assert "WA_TransparentForMouseEvents" in STATIC
    assert "QElapsedTimer" in STATIC
    assert "Qt.TimerType.PreciseTimer" in STATIC
    assert "_FRAME_MS = 16" in STATIC
    assert "QPainter.RenderHint.SmoothPixmapTransform" in STATIC
    assert "painter.setOpacity" in STATIC
    assert "painter.drawPixmap" in STATIC
    assert "QPropertyAnimation" not in STATIC
    assert "QParallelAnimationGroup" not in STATIC
    assert "QGraphicsOpacityEffect" not in STATIC


def test_motion_is_short_subtle_scale_fade_and_y_only() -> None:
    assert "_OPEN_MS = 210" in STATIC
    assert "_CLOSE_MS = 165" in STATIC
    assert "_OPEN_RISE_PX = 14.0" in STATIC
    assert "_CLOSE_DROP_PX = 10.0" in STATIC
    assert "_OPEN_SCALE = 0.985" in STATIC
    assert "_CLOSE_SCALE = 0.990" in STATIC
    paint = _body(STATIC, "def paintEvent", "class StaticModalInteractionController")
    assert "opacity = self._out_cubic(progress)" in paint
    assert "scale = _OPEN_SCALE" in paint
    assert "offset_y = _OPEN_RISE_PX" in paint
    assert "opacity = 1.0 - motion" in paint
    assert "scale = 1.0 - (1.0 - _CLOSE_SCALE)" in paint
    assert "offset_y = _CLOSE_DROP_PX" in paint


def test_real_drawer_never_moves_per_frame() -> None:
    tick = _body(STATIC, "def _tick", "def paintEvent")
    paint = _body(STATIC, "def paintEvent", "class StaticModalInteractionController")
    assert "details.drawer" not in tick
    assert "details.drawer" not in paint
    assert "setGeometry" not in tick
    assert "setGeometry" not in paint
    assert "self.root.update" not in tick
    assert "self.root.repaint" not in tick


def test_open_prepares_static_blur_once_then_animates_panel_snapshot() -> None:
    prepare = _body(STATIC, "def _prepare_open_frame", "def _show_with_animation")
    assert "self.details._capture_backdrop()" in prepare
    assert "self.details.drawer.grab()" in prepare
    assert "self.details.backdrop.show()" in prepare
    assert "self.details.scrim.show()" in prepare
    assert "self.details.drawer.hide()" in prepare
    assert "self.root.repaint()" in prepare

    show = _body(STATIC, "def _show_with_animation", "def _finish_open")
    assert "opening=True" in show
    assert "duration_ms=_OPEN_MS" in show
    assert "finished=self._finish_open" in show


def test_open_and_close_handoffs_keep_snapshot_until_destination_is_painted() -> None:
    tick = _body(STATIC, "def _tick", "def paintEvent")
    assert tick.index("callback()") < tick.index("self.hide()")

    finish_open = _body(STATIC, "def _finish_open", "def request_close")
    assert finish_open.index("self.details.drawer.show()") < finish_open.index(
        "self.root.repaint()"
    )

    finish_close = _body(STATIC, "def _finish_close", "def cleanup")
    assert finish_close.index("self._original_close()") < finish_close.index(
        "self.root.repaint()"
    )


def test_close_paths_are_rewired_to_animation_but_keep_real_controller_fallback() -> None:
    assert "self._original_close = self.details.close" in STATIC
    assert "self.details.close = self.request_close" in STATIC
    assert "self.details.close_button.clicked.connect(self.request_close)" in STATIC
    assert "self.details.scrim.clicked.connect(self.request_close)" in STATIC
    assert "self._original_close()" in STATIC
    assert "event.key() == Qt.Key.Key_Escape" in DETAILS
    assert "self.close()" in DETAILS


def test_fail_soft_resets_partial_scrim_before_static_fallback() -> None:
    show = _body(STATIC, "def _show_with_animation", "def _finish_open")
    fallback = show.split("except Exception:", 1)[1]
    assert fallback.index("self._original_close()") < fallback.index(
        "self._original_show_prepared_modal(ratio=ratio)"
    )


def test_real_modal_stays_in_existing_qwidget_tree() -> None:
    assert "self.backdrop = QLabel(self.root)" in DETAILS
    assert "self.scrim.setGeometry(self.root.rect())" in DETAILS
    assert "self.drawer.setGeometry(self._drawer_rect())" in DETAILS
    assert "self.drawer.show()" in DETAILS
    assert "self.drawer.hide()" in DETAILS
    assert "self.window.hide()" not in DETAILS
    assert "self.window.show()" not in DETAILS


def test_whole_card_body_remains_clickable() -> None:
    assert "def _label_is_passive" in STATIC
    assert "Qt.TextInteractionFlag.TextSelectableByMouse" in STATIC
    assert "Qt.TextInteractionFlag.LinksAccessibleByMouse" in STATIC
    assert "WA_TransparentForMouseEvents" in STATIC
    assert "card.setCursor(Qt.CursorShape.PointingHandCursor)" in STATIC


def test_widget_animation_source_compiles_without_importing_pyside() -> None:
    compile(STATIC, str(ROOT / "gui" / "static_modal_interaction.py"), "exec")
