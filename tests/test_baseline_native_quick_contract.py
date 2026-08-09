from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CARD_FX = (ROOT / "gui" / "nekro_card_fx.py").read_text(encoding="utf-8")
BASE_VISUAL = (ROOT / "gui" / "visual_style.py").read_text(encoding="utf-8")
ADAPTER = (ROOT / "gui" / "native_visual_style.py").read_text(encoding="utf-8")
NATIVE = (ROOT / "gui" / "native_background.py").read_text(encoding="utf-8")
SHELL = (ROOT / "gui" / "native_window_shell.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_baseline_card_animation_contract_is_unchanged() -> None:
    assert "_NORMAL_ALPHA = 64.0" in CARD_FX
    assert "_HOVER_ALPHA = 82.0" in CARD_FX
    assert "_ACTIVE_ALPHA = 96.0" in CARD_FX
    assert "_HOVER_SECONDS = 0.12" in CARD_FX
    assert "_PRESS_SECONDS = 0.08" in CARD_FX
    assert "_RELEASE_SECONDS = 0.12" in CARD_FX
    assert "CSS default ease cubic-bezier(.25,.1,.25,1)" in CARD_FX
    assert "scale=1.0" in CARD_FX
    assert "install_nekro_card_fx(window, visual)" in RUNNER


def test_native_adapter_reuses_baseline_style_and_does_not_fork_interactions() -> None:
    assert "from .visual_style import NEKRO_STYLE" in ADAPTER
    assert "window.setStyleSheet(window.styleSheet() + \"\\n\" + NEKRO_STYLE)" in ADAPTER
    assert "MouseButtonPress" not in ADAPTER
    assert "MouseButtonRelease" not in ADAPTER
    assert "_HOVER_SECONDS" not in ADAPTER
    assert "_PRESS_SECONDS" not in ADAPTER
    assert "_RELEASE_SECONDS" not in ADAPTER
    assert "QGraphicsBlurEffect" not in ADAPTER
    assert "drawPixmap" not in ADAPTER


def test_renderer_moves_only_wallpaper_and_blur_to_quick() -> None:
    assert "QQuickWindow" in NATIVE
    assert "FrameAnimation" in NATIVE
    assert "import QtQuick.Effects" in NATIVE
    assert "MultiEffect" in NATIVE
    assert "maskEnabled: true" in NATIVE
    assert "maskSource: maskImg" in NATIVE
    assert "def _blur_wallpaper" in NATIVE
    assert "setInterval(16)" not in NATIVE
    assert "QQuickWidget" not in NATIVE + ADAPTER
    assert "QOpenGLWidget" not in NATIVE + ADAPTER


def test_baseline_glass_alpha_api_is_preserved() -> None:
    assert "def set_interaction(self, *, scale: float, overlay_alpha: float)" in BASE_VISUAL
    assert "def set_interaction(self, *, scale: float, overlay_alpha: float)" in ADAPTER
    assert "painter.fillRect(target, QColor(0, 0, 0, int(round(self._overlay_alpha))))" in ADAPTER
    assert "painter.setClipPath(path)" in ADAPTER


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


def test_runner_keeps_baseline_business_and_effect_controllers() -> None:
    assert "from gui.console_window import MainWindow" in RUNNER
    assert "install_buffered_logs(window)" in RUNNER
    assert "install_nekro_card_fx(window, visual)" in RUNNER
    assert "install_nekro_effects(window, sakura_count=3)" in RUNNER
    assert 'os.environ.setdefault("QSG_RENDER_LOOP", "threaded")' in RUNNER
