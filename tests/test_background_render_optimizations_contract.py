from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OPT = (ROOT / "gui" / "background_render_optimizations.py").read_text(encoding="utf-8")
NATIVE = (ROOT / "gui" / "native_background.py").read_text(encoding="utf-8")
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def _body(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_preblur_cache_preserves_the_existing_visual_pipeline() -> None:
    assert '_CACHE_MAGIC = b"ECBGRAW1"' in OPT
    assert "QImage.Format.Format_ARGB32_Premultiplied" in OPT
    assert "payload = bytes(memoryview(image.constBits())[:payload_size])" in OPT
    assert "result = original(source, radius)" in OPT
    assert "_native._blur_wallpaper = cached_blur" in OPT
    assert "blurred.save(str(self._blur_path), \"JPG\", 92)" in NATIVE
    assert "layer.enabled: true" in NATIVE
    assert "source: blurSource" in NATIVE
    assert "maskEnabled: true" in NATIVE
    assert "maskSource: maskImg" in NATIVE


def test_pointer_hotpath_keeps_exact_parallax_contract_with_one_cursor_read() -> None:
    body = _body(OPT, "    def sample(self) -> None:", "\n\ndef install_background_pointer_hotpath")
    assert body.count("QCursor.pos()") == 1
    assert "quick.mapFromGlobal(global_pos)" in body
    assert 'getattr(_native, "_POINTER_EPSILON", 0.0015)' in body
    assert 'quick.setProperty("pointerX", nx)' in body
    assert 'quick.setProperty("pointerY", ny)' in body
    assert 'quick.setProperty("animationRunning", True)' in body
    assert "_POINTER_SAMPLE_MS = 8" in NATIVE
    assert "Math.pow(0.88, dt * 60.0)" in NATIVE
    assert "< 0.02" in NATIVE


def test_pointer_geometry_is_event_cached_instead_of_polled_each_tick() -> None:
    init = _body(OPT, "    def __init__(self, window: QMainWindow, visual: Any) -> None:", "    def _read_geometry")
    sample = _body(OPT, "    def sample(self) -> None:", "\n\ndef install_background_pointer_hotpath")
    for signal_name in ("xChanged", "yChanged", "widthChanged", "heightChanged"):
        assert signal_name in init
    assert "geometry = self._geometry" in sample
    assert "quick.x()" not in sample
    assert "quick.y()" not in sample
    assert "quick.width()" not in sample
    assert "quick.height()" not in sample


def test_optimizations_install_before_native_background_and_after_runtime_wrapper() -> None:
    assert "install_preblur_cache" in RUN
    assert "install_background_pointer_hotpath" in RUN
    assert RUN.index("install_preblur_cache()") < RUN.index("install_native_visual_style(window)")
    assert RUN.index("install_ui_runtime_optimizations(window, visual)") < RUN.index(
        "install_background_pointer_hotpath(window, visual)"
    )
    assert RUN.index("install_background_pointer_hotpath(window, visual)") < RUN.index("shell.show()")


def test_optimization_source_compiles_without_importing_pyside() -> None:
    compile(OPT, str(ROOT / "gui" / "background_render_optimizations.py"), "exec")
