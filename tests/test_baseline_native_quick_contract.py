from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CARD_FX = (ROOT / "gui" / "nekro_card_fx.py").read_text(encoding="utf-8")
BASE_VISUAL = (ROOT / "gui" / "visual_style.py").read_text(encoding="utf-8")
ADAPTER = (ROOT / "gui" / "native_visual_style.py").read_text(encoding="utf-8")
NATIVE = (ROOT / "gui" / "native_background.py").read_text(encoding="utf-8")
RUNTIME = (ROOT / "gui" / "ui_runtime_optimizations.py").read_text(encoding="utf-8")
EFFECTS = (ROOT / "gui" / "nekro_effects.py").read_text(encoding="utf-8")
LOGS = (ROOT / "gui" / "log_presenter.py").read_text(encoding="utf-8")
SHELL = (ROOT / "gui" / "native_window_shell.py").read_text(encoding="utf-8")
SCROLL = (ROOT / "gui" / "smooth_scroll.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_reference_website_list_card_interaction_is_locked() -> None:
    assert "_NORMAL_SCALE = 1.00" in CARD_FX
    assert "_HOVER_SCALE = 1.02" in CARD_FX
    assert "_ACTIVE_SCALE = 1.00" in CARD_FX
    assert "_NORMAL_ALPHA = 64.0" in CARD_FX
    assert "_HOVER_ALPHA = 102.0" in CARD_FX
    assert "_ACTIVE_ALPHA = 102.0" in CARD_FX
    assert "_TRANSITION_MS = 300" in CARD_FX
    assert "cubic-bezier(.25, .1, .25, 1)" in CARD_FX
    assert "_MIN_PRESSED_MS" not in CARD_FX
    assert "QElapsedTimer" not in CARD_FX
    assert "install_nekro_card_fx(window, visual)" in RUNNER


def test_card_motion_is_continuous_reversible_and_refresh_aware() -> None:
    assert "time.perf_counter()" in CARD_FX
    assert "self._motion_timer = QTimer(self)" in CARD_FX
    assert "Qt.TimerType.PreciseTimer" in CARD_FX
    assert "screen.refreshRate()" in CARD_FX
    assert "self._motion_easing" not in CARD_FX
    assert "self._ease.valueForProgress(linear)" in CARD_FX
    assert "state.from_scale = state.current_scale" in CARD_FX
    assert "state.from_alpha = state.current_alpha" in CARD_FX
    assert "self._active(frame)" in CARD_FX
    assert "self._hover(previous)" in CARD_FX


def test_card_hit_test_uses_one_local_sampler_not_per_widget_filters() -> None:
    assert "_POINTER_SAMPLE_MS = 8" in CARD_FX
    assert "QCursor.pos()" in CARD_FX
    assert "self.window.childAt(local)" in CARD_FX
    assert "widget.installEventFilter(self)" not in CARD_FX
    assert "QApplication.widgetAt" not in CARD_FX
    assert "QMouseEvent" not in CARD_FX
    assert "app.installEventFilter(self)" not in CARD_FX
    assert "_inline_card_motion_active" not in CARD_FX


def test_all_registered_big_and_small_glass_cards_share_the_same_interaction() -> None:
    assert '_GLASS_NAMES = {"glassCard", "heroCard", "statusCard", "microCard"}' in CARD_FX
    assert '_GLASS_NAMES = {"glassCard", "heroCard", "statusCard", "microCard"}' in ADAPTER
    assert "for frame in window.findChildren(QFrame):" in CARD_FX
    assert "if frame.objectName() not in _GLASS_NAMES:" in CARD_FX


def test_quick_glass_shell_and_widget_content_share_the_same_transform() -> None:
    assert "class NativeGlassProxy(QObject)" in ADAPTER
    assert "class _CardScaleEffect(QGraphicsEffect)" in ADAPTER
    assert "frame.setGraphicsEffect(self._scale_effect)" in ADAPTER
    assert "painter.scale(scale, scale)" in ADAPTER
    assert "self.drawSource(painter)" in ADAPTER
    assert "self.background.set_card_presentation(" in ADAPTER
    assert "self._scale_effect.set_scale(scale)" in ADAPTER
    assert "class _CardInteractionTint" not in ADAPTER
    assert "_interaction_overlay_alpha" not in ADAPTER

    assert "class GlassCardModel(QAbstractListModel)" in NATIVE
    assert "SCALE_ROLE = _ROLE_BASE + 11" in NATIVE
    assert 'SCALE_ROLE: "cardScale"' in NATIVE
    assert 'self.SCALE_ROLE: QByteArray(b"cardScale")' in NATIVE
    assert '"cardScale": 1.0' in NATIVE
    assert "scale: cardScale" in NATIVE
    assert "transformOrigin: Item.Center" in NATIVE
    assert "def set_presentation" in NATIVE
    assert "def set_card_presentation" in NATIVE
    assert "cardAlpha / 255.0" in NATIVE

    # Hover stays cheap: the full-window blur mask remains geometry-only while
    # Quick's GPU rectangle supplies the visible scaled glass shell.
    render_mask = NATIVE.split("def render_mask", 1)[1].split("def _qml_source", 1)[0]
    assert 'state.get("cardScale"' not in render_mask
    presentation = NATIVE.split("def set_card_presentation", 1)[1].split("def _sample_pointer", 1)[0]
    assert "schedule_mask_update" not in presentation


def test_runtime_glass_mask_avoids_png_round_trip_without_changing_mask_pixels() -> None:
    assert "class _GlassMaskImageProvider(QQuickImageProvider)" in RUNTIME
    assert "engine.addImageProvider(_MASK_PROVIDER_ID, provider)" in RUNTIME
    assert "bg.card_model.render_mask(quick.width(), quick.height())" in RUNTIME
    assert 'QUrl(f"image://{_MASK_PROVIDER_ID}/{bg._mask_revision}")' in RUNTIME
    assert "image.save(" not in RUNTIME
    assert "self._original_mask_update()" in RUNTIME
    assert "install_ui_runtime_optimizations(window, visual)" in RUNNER


def test_batch_cards_join_the_existing_native_glass_model_after_workspace_install() -> None:
    assert "def refresh_glass_frames(self) -> int:" in ADAPTER
    assert "model.beginInsertRows" in ADAPTER
    assert "model.cards.append(frame)" in ADAPTER
    assert '"cardScale": 1.0' in ADAPTER
    assert "self.background._geometry_watch.add(current)" in ADAPTER
    assert "mode_stack.currentChanged.connect" in ADAPTER
    assert "window.install_mode_workspace()" in RUNNER
    assert "visual.refresh_glass_frames()" in RUNNER
    assert RUNNER.index("window.install_mode_workspace()") < RUNNER.index("visual.refresh_glass_frames()")
    assert RUNNER.index("visual.refresh_glass_frames()") < RUNNER.index(
        "install_ui_runtime_optimizations(window, visual)"
    )
    assert RUNNER.index("install_ui_runtime_optimizations(window, visual)") < RUNNER.index(
        "install_nekro_card_fx(window, visual)"
    )


def test_renderer_samples_pointer_without_global_event_filter() -> None:
    assert "QQuickWindow" in NATIVE
    assert "FrameAnimation" in NATIVE
    assert "import QtQuick.Effects" in NATIVE
    assert "MultiEffect" in NATIVE
    assert "maskEnabled: true" in NATIVE
    assert "def _blur_wallpaper" in NATIVE
    assert "_POINTER_SAMPLE_MS = 8" in NATIVE
    assert "QCursor.pos()" in NATIVE
    assert "watched.installEventFilter(self)" in NATIVE
    assert "app.installEventFilter(self)" not in NATIVE
    assert "QQuickWidget" not in NATIVE + ADAPTER
    assert "QOpenGLWidget" not in NATIVE + ADAPTER


def test_effect_overlay_polls_cursor_inside_its_existing_frame() -> None:
    assert "self._sample_pointer()" in EFFECTS
    assert "QCursor.pos()" in EFFECTS
    assert "QApplication.mouseButtons()" in EFFECTS
    assert "window.installEventFilter(self)" in EFFECTS
    assert "app.installEventFilter(self)" not in EFFECTS
    assert "QMouseEvent" not in EFFECTS


def test_hidden_console_defers_text_document_work() -> None:
    assert "if not self.view.isVisible():" in LOGS
    assert "_MAX_HIDDEN_PENDING = 8000" in LOGS
    assert "_MAX_CATCHUP_LINES = 800" in LOGS
    assert "QEvent.Type.Show" in LOGS


def test_runtime_tables_reuse_items_instead_of_rebuilding_every_update() -> None:
    assert "item = table.item(row, column)" in RUNTIME
    assert "if item is None:" in RUNTIME
    assert "controller.jobs_changed.disconnect(workspace._apply_jobs)" in RUNTIME
    assert "controller.jobs_changed.connect(self._apply_batch_jobs)" in RUNTIME
    assert "if old_rows != new_rows:" in RUNTIME
    assert "table.resizeRowsToContents()" in RUNTIME


def test_baseline_style_and_public_glass_api_are_preserved() -> None:
    assert "from .visual_style import NEKRO_STYLE" in ADAPTER
    assert 'window.setStyleSheet(window.styleSheet() + "\\n" + NEKRO_STYLE)' in ADAPTER
    assert "def set_interaction(self, *, scale: float, overlay_alpha: float)" in BASE_VISUAL
    assert "def set_interaction(self, *, scale: float, overlay_alpha: float)" in ADAPTER
    assert "self.central.installEventFilter(self)" in ADAPTER
    assert "app.installEventFilter(self)" not in ADAPTER


def test_native_shell_is_single_window_tree_and_native_pixel_sized() -> None:
    assert "_WS_CHILD" in SHELL
    assert "_WS_EX_LAYERED" in SHELL
    assert "SetParent" in SHELL
    assert "GetClientRect" in SHELL
    assert "SetWindowPos" in SHELL
    assert "overlay.setGeometry" not in SHELL
    assert "handle.resize" not in SHELL
    assert "handle.setPosition" not in SHELL
    assert "createWindowContainer" not in SHELL + NATIVE


def test_native_shell_focus_bridge_is_limited_to_keyboard_controls() -> None:
    assert "def _focus_native_child" in SHELL
    assert "user32.SetFocus" in SHELL
    assert "user32.GetFocus" in SHELL
    assert "QEvent.Type.WindowActivate" in SHELL
    assert "QEvent.Type.FocusIn" in SHELL
    assert "app.focusChanged.connect(self._on_focus_changed)" in SHELL
    assert "self._last_focus_widget" in SHELL
    assert "target.setFocus(Qt.FocusReason.ActiveWindowFocusReason)" in SHELL
    assert "_KEYBOARD_WIDGET_TYPES" in SHELL
    assert "QLineEdit" in SHELL
    assert "QAbstractSpinBox" in SHELL
    assert "QComboBox" in SHELL
    assert "QPlainTextEdit" in SHELL
    assert "QAbstractItemView" in SHELL
    assert "focusPolicy()" not in SHELL
    assert "_keyboard_focus_watch" in SHELL
    assert "QEvent.Type.MouseButtonPress" in SHELL


def test_smooth_wheel_filter_is_scoped_bounded_and_frame_rate_independent() -> None:
    assert "def install(self, root: QWidget)" in SCROLL
    assert "root.findChildren(QAbstractScrollArea)" in SCROLL
    assert "watched.installEventFilter(self)" in SCROLL
    assert "from PySide6.QtWidgets import QApplication" not in SCROLL
    assert "QApplication.instance()" not in SCROLL
    assert "app.installEventFilter" not in RUNNER
    assert "smooth_wheel.install(window)" in RUNNER
    assert "_clamp_target" in SCROLL
    assert "if step == 0:" in SCROLL
    assert "if bar.value() == current:" in SCROLL
    assert "math.pow(1.0 - self._EASE, dt / self._REFERENCE_DT_S)" in SCROLL
    assert "self._scroller._animations.clear()" in SCROLL


def test_runner_keeps_baseline_business_and_effect_controllers() -> None:
    assert "from gui.console_window import MainWindow" in RUNNER
    assert "install_buffered_logs(window)" in RUNNER
    assert "install_nekro_card_fx(window, visual)" in RUNNER
    assert "install_nekro_effects(window, sakura_count=3)" in RUNNER
    assert 'os.environ.setdefault("QSG_RENDER_LOOP", "threaded")' in RUNNER
