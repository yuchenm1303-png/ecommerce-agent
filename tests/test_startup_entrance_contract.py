from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "gui" / "startup_entrance.py").read_text(encoding="utf-8")
STABILITY = (ROOT / "gui" / "startup_entrance_stability.py").read_text(encoding="utf-8")
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_startup_entrance_is_scene_graph_owned() -> None:
    assert "QQmlComponent" in SOURCE
    assert "QQuickItem" in SOURCE
    assert "setParentItem(self.quick.contentItem())" in SOURCE
    assert 'z: 40000' in SOURCE
    assert "QPainter" not in SOURCE
    assert "QPixmap" not in SOURCE
    assert "PreciseTimer" not in SOURCE
    assert "paintEvent" not in SOURCE


def test_startup_visual_timing_is_preserved_in_qml() -> None:
    for token in (
        "_UI_FADE_MS = 620",
        "_CURTAIN_DELAY_MS = 0",
        "_CURTAIN_MS = 500",
        "_BACKGROUND_DELAY_MS = 120",
        "_BACKGROUND_MS = 760",
        "_TOTAL_MS = 1000",
        "_BG_START_SCALE = 1.16",
        "_BG_START_DIM = 0.46",
        "_CURTAIN_FRACTION = 0.51",
        '_CURTAIN_COLOR = "#333333"',
        "easing.bezierCurve: [0.645, 0.045, 0.355, 1.0, 1.0, 1.0]",
        "easing.bezierCurve: [0.25, 0.46, 0.45, 0.94, 1.0, 1.0]",
        "easing.type: Easing.OutCubic",
    ):
        assert token in SOURCE


def test_startup_reveal_uses_preblurred_assets_without_runtime_blur() -> None:
    assert 'getattr(self.background, "_sharp_path", "")' in SOURCE
    assert 'getattr(self.background, "_blur_path", "")' in SOURCE
    assert "Image.PreserveAspectCrop" in SOURCE
    assert "MultiEffect" not in SOURCE
    assert "QGraphicsBlurEffect" not in SOURCE


def test_qml_loading_is_a_normal_async_state() -> None:
    assert "component.statusChanged.connect(self._on_component_status_changed)" in SOURCE
    assert "if status == QQmlComponent.Status.Loading:" in SOURCE
    loading = SOURCE.split("if status == QQmlComponent.Status.Loading:", 1)[1].split(
        "if status == QQmlComponent.Status.Error:", 1
    )[0]
    assert "return" in loading
    assert "if status != QQmlComponent.Status.Ready:" in SOURCE
    assert "self._create_item_from_ready_component()" in SOURCE
    assert "Startup entrance QML is not ready" not in SOURCE


def test_startup_requests_survive_component_loading() -> None:
    assert "self._show_requested = True" in SOURCE
    assert "self._reveal_requested = False" in SOURCE
    assert "self._reveal_requested = True" in SOURCE
    assert "def _apply_pending_requests" in SOURCE
    assert 'item.setProperty("revealStarted", True)' in SOURCE


def test_decorative_qml_failure_does_not_raise_out_of_loader() -> None:
    loader = SOURCE.split("def _handle_component_status", 1)[1].split(
        "def _create_item_from_ready_component", 1
    )[0]
    assert "QQmlComponent.Status.Error" in loader
    assert "self._fail(" in loader
    assert "raise RuntimeError" not in loader


def test_startup_gate_waits_for_overlay_resolution_before_handoff() -> None:
    start = STABILITY.split("def start(self)", 1)[1].split(
        "def _on_overlay_ready", 1
    )[0]
    assert "self._overlay_is_ready()" in start
    assert "self._overlay_is_failed()" in start
    assert "self._connect_overlay_wait()" in start
    assert "self.overlay.ready.connect(self._on_overlay_ready)" in STABILITY
    assert "self.overlay.failed.connect(self._on_overlay_failed)" in STABILITY


def test_startup_handoff_precedes_reveal_animation() -> None:
    assert "_QUICK_SETTLE_FRAMES = 2" in STABILITY
    assert "self._emit_handoff_once()" in STABILITY
    assert "quick.frameSwapped.connect(self._on_reveal_frame_swapped)" in STABILITY
    begin = STABILITY.split("def _begin_reveal_frame_barrier", 1)[1].split(
        "def _disconnect_reveal_frame_barrier", 1
    )[0]
    assert begin.index("self._emit_handoff_once()") < begin.rindex(
        "self._flush_native_background()"
    )
    assert "QTimer.singleShot(0, self._start_entrance)" in STABILITY


def test_startup_animation_has_no_widget_layout_watchers_while_running() -> None:
    begin = STABILITY.split("def _begin_reveal_frame_barrier", 1)[1].split(
        "def _disconnect_reveal_frame_barrier", 1
    )[0]
    assert "self._remove_live_surface_watch()" in begin
    assert "central.update()" in STABILITY
    assert "central.repaint()" not in STABILITY


def test_startup_does_not_resume_legacy_card_fx_in_quick_mode() -> None:
    resume = STABILITY.split("def _resume_card_fx", 1)[1].split(
        "def _resume_presentation", 1
    )[0]
    assert "self._quick_presentation_active()" in resume
    assert "resume_from_modal" in resume


def test_formal_launcher_uses_stability_gate_after_native_show() -> None:
    assert "entrance = install_startup_entrance(window, visual)" in RUN
    assert "entrance_stability = install_startup_entrance_stability(window, entrance)" in RUN
    assert "static_view = install_static_qml_view(window, visual, entrance_stability)" in RUN
    assert "shell.show()" in RUN
    assert "entrance_stability.start()" in RUN
    assert RUN.index("shell.show()") < RUN.index("entrance_stability.start()")


def test_startup_sources_compile_without_importing_pyside() -> None:
    compile(SOURCE, "gui/startup_entrance.py", "exec")
    compile(STABILITY, "gui/startup_entrance_stability.py", "exec")
