from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "scripts" / "parallax_benchmark.py").read_text(encoding="utf-8")


def test_benchmark_source_compiles() -> None:
    compile(SOURCE, "scripts/parallax_benchmark.py", "exec")


def test_benchmark_keeps_four_isolated_renderer_modes() -> None:
    for mode in ("widget60", "widget165", "gl", "quick"):
        assert f'"{mode}"' in SOURCE
    assert 'modes = ["widget60", "widget165", "gl", "quick"]' in SOURCE


def test_native_gl_path_is_vsync_driven_and_qpainter_free() -> None:
    gl = SOURCE.split("def run_gl", 1)[1].split("def run_quick", 1)[0]
    assert "QOpenGLWindow" in gl
    assert "QOpenGLTextureBlitter" in gl
    assert "fmt.setSwapInterval(1)" in gl
    assert "self.frameSwapped.connect(self._frame_swapped)" in gl
    assert "self.update()" in gl
    assert "QPainter" not in gl


def test_quick_path_is_threaded_scene_graph_and_frame_animation() -> None:
    quick = SOURCE.split("def run_quick", 1)[1].split("def run_child", 1)[0]
    assert 'QSG_RENDER_LOOP", "threaded"' in quick
    assert "FrameAnimation" in quick
    assert "frameSwapped.connect" in quick
