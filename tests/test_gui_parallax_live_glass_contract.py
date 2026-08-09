from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")
SCENE = (ROOT / "gui" / "qml" / "SceneBackground.qml").read_text(encoding="utf-8")
GLASS = (ROOT / "gui" / "qml" / "GlassCard.qml").read_text(encoding="utf-8")
EFFECTS = (ROOT / "gui" / "qml" / "SceneEffects.qml").read_text(encoding="utf-8")
MAIN = (ROOT / "gui" / "qml" / "Main.qml").read_text(encoding="utf-8")


def test_runtime_uses_one_qquickwindow_scene_graph_not_widget_composition() -> None:
    assert "QQmlApplicationEngine" in LAUNCHER
    assert 'QSG_RENDER_LOOP", "threaded"' in LAUNCHER
    assert "QQuickWindow.setGraphicsApi" in LAUNCHER
    assert "QQuickWidget" not in LAUNCHER
    assert "QOpenGLWidget" not in LAUNCHER
    assert "console_window" not in LAUNCHER
    assert "visual_style" not in LAUNCHER
    assert "nekro_card_fx" not in LAUNCHER


def test_parallax_is_fractional_frame_synchronized_and_timer_free() -> None:
    assert "FrameAnimation" in SCENE
    assert "frameTime" in SCENE
    assert "Math.exp" in SCENE
    assert "root.offsetX +=" in SCENE
    assert "root.offsetY +=" in SCENE
    assert "Timer {" not in SCENE
    assert "Math.round(root.offset" not in SCENE


def test_live_glass_is_one_global_preblur_and_mask_pass() -> None:
    assert 'source: "image://wallpaper/blur"' in SCENE
    assert "id: glassMaskLayer" in SCENE
    assert "layer.enabled: true" in SCENE
    assert "MultiEffect" in SCENE
    assert "source: blurSource" in SCENE
    assert "maskSource: glassMaskLayer" in SCENE
    assert "ShaderEffectSource" not in GLASS
    assert "property Item maskLayer" in GLASS
    assert "parent: root.maskLayer" in GLASS
    assert "maskLayer: scene.glassMaskLayer" in MAIN


def test_sakura_and_cursor_share_the_same_scene_graph_overlay() -> None:
    assert "SceneEffects" in MAIN
    assert "z: 1000" in MAIN
    assert "FrameAnimation" in EFFECTS
    assert "for (var i = 0; i < 3; ++i)" in EFFECTS
    assert 'source: "image://wallpaper/sakura"' in EFFECTS
    assert "pointerPress.active ? 9 : 18" in EFFECTS
    assert "Timer {" not in EFFECTS
