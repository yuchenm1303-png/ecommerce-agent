from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CARD_FX = (ROOT / "gui" / "nekro_card_fx.py").read_text(encoding="utf-8")
BASE_VISUAL = (ROOT / "gui" / "visual_style.py").read_text(encoding="utf-8")
ADAPTER = (ROOT / "gui" / "native_visual_style.py").read_text(encoding="utf-8")
NATIVE = (ROOT / "gui" / "native_background.py").read_text(encoding="utf-8")
EFFECTS = (ROOT / "gui" / "nekro_effects.py").read_text(encoding="utf-8")
LOGS = (ROOT / "gui" / "log_presenter.py").read_text(encoding="utf-8")
SHELL = (ROOT / "gui" / "native_window_shell.py").read_text(encoding="utf-8")
SCROLL = (ROOT / "gui" / "smooth_scroll.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_baseline_card_animation_curve_is_preserved() -> None:
    assert "_NORMAL_ALPHA = 64.0" in CARD_FX
    assert "_HOVER_ALPHA = 82.0" in CARD_FX
    assert "_ACTIVE_ALPHA = 96.0" in CARD_FX
    assert "_HOVER_SECONDS = 0.12" in CARD_FX
    assert "_PRESS_SECONDS = 0.08" in CARD_FX
    assert "_RELEASE_SECONDS = 0.12" in CARD_FX
    assert "scale=1.0" in CARD_FX
    assert "install_nekro_card_fx(window, visual)" in RUNNER


def test_card_hit_test_uses_one_pointer_sampler_not_per_widget_filters() -> None:
    assert "_POINTER_SAMPLE_MS = 24" in CARD_FX
    assert "QCursor.pos()" in CARD_FX
    assert "self.window.childAt(local)" in CARD_FX
    assert "window.installEventFilter(self)" in CARD_FX
    assert "widget.installEventFilter(self)" not in CARD_FX
    assert "QApplication.widgetAt" not in CARD_FX
    assert "app.installEventFilter(self)" not in CARD_FX
    assert "_inline_card_motion_active" in CARD_FX


def test_glass_blur_mask_is_static_until_geometry_changes() -> None:
    assert "class NativeGlassProxy(QObject)" in ADAPTER
    assert "background.set_card_alpha(self.frame, overlay_alpha)" in ADAPTER
    assert "paintEvent" not in ADAPTER
    assert "QGraphicsBlurEffect" not in ADAPTER

    assert "class GlassCardModel(QAbstractListModel)" in NATIVE
    assert 'setContextProperty("glassCardModel", self.card_model)' in NATIVE
    assert "def render_mask" in NATIVE
    assert "property url maskUrl" in NATIVE
    assert "id: maskImg" in NATIVE
    assert "maskSource: maskImg" in NATIVE
    assert "glass_mask_" in NATIVE
    assert "ShaderEffectSource" not in NATIVE
    assert "live: true" not in NATIVE
    assert "cardAlpha / 255.0" in NATIVE
    assert "_GEOMETRY_SYNC_MS = 24" in NATIVE


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


def test_smooth_wheel_filter_is_scoped_to_scroll_areas() -> None:
    assert "def install(self, root: QWidget)" in SCROLL
    assert "root.findChildren(QAbstractScrollArea)" in SCROLL
    assert "watched.installEventFilter(self)" in SCROLL
    assert "from PySide6.QtWidgets import QApplication" not in SCROLL
    assert "QApplication.instance()" not in SCROLL
    assert "app.installEventFilter" not in RUNNER
    assert "smooth_wheel.install(window)" in RUNNER


def test_runner_keeps_baseline_business_and_effect_controllers() -> None:
    assert "from gui.console_window import MainWindow" in RUNNER
    assert "install_buffered_logs(window)" in RUNNER
    assert "install_nekro_card_fx(window, visual)" in RUNNER
    assert "install_nekro_effects(window, sakura_count=3)" in RUNNER
    assert 'os.environ.setdefault("QSG_RENDER_LOOP", "threaded")' in RUNNER
