from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CARD_FX = (ROOT / "gui" / "nekro_card_fx.py").read_text(encoding="utf-8")
BASE_VISUAL = (ROOT / "gui" / "visual_style.py").read_text(encoding="utf-8")
ADAPTER = (ROOT / "gui" / "native_visual_style.py").read_text(encoding="utf-8")
NATIVE = (ROOT / "gui" / "native_background.py").read_text(encoding="utf-8")
SHELL = (ROOT / "gui" / "native_window_shell.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_baseline_card_animation_curve_is_preserved() -> None:
    assert "_NORMAL_ALPHA = 64.0" in CARD_FX
    assert "_HOVER_ALPHA = 82.0" in CARD_FX
    assert "_ACTIVE_ALPHA = 96.0" in CARD_FX
    assert "_HOVER_SECONDS = 0.12" in CARD_FX
    assert "_PRESS_SECONDS = 0.08" in CARD_FX
    assert "_RELEASE_SECONDS = 0.12" in CARD_FX
    assert "CSS default ease cubic-bezier(.25,.1,.25,1)" in CARD_FX
    assert "scale=1.0" in CARD_FX
    assert "install_nekro_card_fx(window, visual)" in RUNNER


def test_native_child_hit_test_does_not_change_animation_values() -> None:
    assert "self.window.childAt(local)" in CARD_FX
    assert "self.window.mapFromGlobal(point.toPoint())" in CARD_FX
    assert "QApplication.widgetAt(point.toPoint())" in CARD_FX
    assert "event_type == QEvent.Enter" in CARD_FX


def test_glass_pixels_are_composed_once_in_quick() -> None:
    assert "class NativeGlassProxy(QObject)" in ADAPTER
    assert "background.set_card_alpha(self.frame, overlay_alpha)" in ADAPTER
    assert "paintEvent" not in ADAPTER
    assert "QPainterPath" not in ADAPTER
    assert "QColor" not in ADAPTER
    assert "QGraphicsBlurEffect" not in ADAPTER

    assert "class GlassCardModel(QAbstractListModel)" in NATIVE
    assert 'setContextProperty("glassCardModel", self.card_model)' in NATIVE
    assert "id: glassMaskSource" in NATIVE
    assert "model: glassCardModel" in NATIVE
    assert "cardAlpha / 255.0" in NATIVE
    assert "ShaderEffectSource" in NATIVE
    assert "id: glassMaskTexture" in NATIVE
    assert "sourceItem: glassMaskSource" in NATIVE
    assert "hideSource: true" in NATIVE
    assert "maskSource: glassMaskTexture" in NATIVE
    assert "maskUrl" not in NATIVE
    assert "glass_mask_" not in NATIVE


def test_renderer_moves_wallpaper_blur_and_tint_to_quick() -> None:
    assert "QQuickWindow" in NATIVE
    assert "FrameAnimation" in NATIVE
    assert "import QtQuick.Effects" in NATIVE
    assert "MultiEffect" in NATIVE
    assert "maskEnabled: true" in NATIVE
    assert "def _blur_wallpaper" in NATIVE
    assert "setInterval(16)" not in NATIVE
    assert "QQuickWidget" not in NATIVE + ADAPTER
    assert "QOpenGLWidget" not in NATIVE + ADAPTER


def test_baseline_style_and_public_glass_api_are_preserved() -> None:
    assert "from .visual_style import NEKRO_STYLE" in ADAPTER
    assert 'window.setStyleSheet(window.styleSheet() + "\\n" + NEKRO_STYLE)' in ADAPTER
    assert "def set_interaction(self, *, scale: float, overlay_alpha: float)" in BASE_VISUAL
    assert "def set_interaction(self, *, scale: float, overlay_alpha: float)" in ADAPTER


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


def test_native_shell_bridges_keyboard_focus_deterministically() -> None:
    assert "def _focus_native_child" in SHELL
    assert "user32.SetFocus" in SHELL
    assert "user32.GetFocus" in SHELL
    assert "QEvent.Type.WindowActivate" in SHELL
    assert "QEvent.Type.FocusIn" in SHELL
    assert "QEvent.Type.MouseButtonPress" in SHELL
    assert "self._last_focus_widget" in SHELL
    assert "target.setFocus(Qt.FocusReason.ActiveWindowFocusReason)" in SHELL


def test_runner_keeps_baseline_business_and_effect_controllers() -> None:
    assert "from gui.console_window import MainWindow" in RUNNER
    assert "install_buffered_logs(window)" in RUNNER
    assert "install_nekro_card_fx(window, visual)" in RUNNER
    assert "install_nekro_effects(window, sakura_count=3)" in RUNNER
    assert 'os.environ.setdefault("QSG_RENDER_LOOP", "threaded")' in RUNNER
