from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOGS = (ROOT / "gui" / "log_presenter.py").read_text(encoding="utf-8")
CARD = (ROOT / "gui" / "nekro_card_fx.py").read_text(encoding="utf-8")
VISUAL = (ROOT / "gui" / "native_visual_style.py").read_text(encoding="utf-8")
RUNTIME = (ROOT / "gui" / "ui_runtime_optimizations.py").read_text(encoding="utf-8")
ASSISTANT = (ROOT / "gui" / "runtime_assistant.py").read_text(encoding="utf-8")
WORKSPACE = (ROOT / "gui" / "workspace_transition.py").read_text(encoding="utf-8")
TUNING = (ROOT / "gui" / "workspace_transition_tuning.py").read_text(encoding="utf-8")
MODAL = (ROOT / "gui" / "static_modal_interaction.py").read_text(encoding="utf-8")
EFFECTS = (ROOT / "gui" / "nekro_effects.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def _body(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_hidden_console_queue_has_constant_time_head_removal() -> None:
    assert "from collections import deque" in LOGS
    assert "self.pending: deque[str] = deque()" in LOGS
    assert "self.pending.popleft()" in LOGS
    assert "del self.pending[:" not in LOGS
    assert "_LOG_FLUSH_MS = 70" in LOGS
    assert "_CATCHUP_FLUSH_MS = 16" in LOGS
    assert "_MAX_HIDDEN_PENDING = 8000" in LOGS


def test_card_motion_cost_tracks_active_cards_not_total_card_count() -> None:
    assert "self._moving_frames: set[QFrame] = set()" in CARD
    advance = _body(CARD, "def _advance_motions", "def _animate_to")
    assert "for frame in tuple(self._moving_frames):" in advance
    assert "for state in self.states.values():" not in advance
    assert "self._moving_frames.add(frame)" in CARD
    assert "def _hover_still_owns_global" in CARD
    assert "_POINTER_SAMPLE_MS = 8" in CARD
    assert "_TRANSITION_MS = 300" in CARD


def test_card_content_effect_does_not_invalidate_bounds_every_animation_frame() -> None:
    effect = _body(VISUAL, "class _CardScaleEffect", "class NativeGlassProxy")
    assert "_EFFECT_BOUND_SCALE = 1.04" in VISUAL
    assert effect.count("self.updateBoundingRect()") == 1
    assert "if self.isEnabled() != active:" in effect
    assert "self.update()" in effect
    assert "painter.scale(scale, scale)" in effect


def test_background_pointer_keeps_full_sampling_rate_with_static_idle_shortcut() -> None:
    assert "def _install_idle_pointer_fast_path" in RUNTIME
    assert "controller._last_pointer_global == point" in RUNTIME
    assert "controller._last_pointer_quick_geometry == geometry" in RUNTIME
    assert "original()" in RUNTIME
    # We optimize work per tick, not responsiveness by lowering the established sampler.
    NATIVE = (ROOT / "gui" / "native_background.py").read_text(encoding="utf-8")
    assert "_POINTER_SAMPLE_MS = 8" in NATIVE


def test_batch_table_skips_unchanged_rows_before_touching_qtablewidget_items() -> None:
    assert "self._batch_row_fingerprints" in RUNTIME
    batch = _body(RUNTIME, "def _apply_batch_jobs", "def _apply_batch_summary")
    assert "previous != new_fingerprints" in batch
    assert "previous[row] == new_fingerprints[row]" in batch
    assert batch.index("previous[row] == new_fingerprints[row]") < batch.index(
        "item = self._ensure_item(table, row, column, value)"
    )
    assert 'job.status == "READY"' in batch


def test_default_off_runtime_assistant_keeps_monitoring_but_defers_hidden_widget_work() -> None:
    present = _body(ASSISTANT, "def present(self, event: RuntimeEvent)", "def _compact_after_recovery")
    assert "self._last_event = event" in present
    assert "if not self._user_visible:" in present
    assert "return" in present
    assert "self._apply_event_to_widgets(event)" in present
    assert "self._recovered_compacted_while_hidden" in ASSISTANT
    assert "install_runtime_event_bridge(window)" in ASSISTANT
    assert "install_runtime_shadow_recovery(window)" in ASSISTANT


def test_approved_animation_architecture_and_timing_remain_outside_hot_path_changes() -> None:
    # The stabilized transparent QWidget render path remains intact; never regress
    # to QWidget.grab(), which previously introduced the large white-frame artifact.
    assert "page.render(" in WORKSPACE
    assert "page.grab()" not in WORKSPACE
    assert "_TOTAL_MS = 390" in WORKSPACE
    assert "_TOTAL_MS = 480" in TUNING
    assert "_OPEN_MS = 500" in MODAL
    assert "_CLOSE_MS = 300" in MODAL
    assert "_FRAME_MS = 16" in EFFECTS
    assert "install_nekro_effects(window, sakura_count=3)" in RUNNER


def test_all_hot_path_sources_compile_without_importing_qt() -> None:
    for path in (
        ROOT / "gui" / "log_presenter.py",
        ROOT / "gui" / "nekro_card_fx.py",
        ROOT / "gui" / "native_visual_style.py",
        ROOT / "gui" / "ui_runtime_optimizations.py",
        ROOT / "gui" / "runtime_assistant.py",
    ):
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
