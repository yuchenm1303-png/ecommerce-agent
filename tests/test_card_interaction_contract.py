from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CARD = (ROOT / "gui" / "nekro_card_fx.py").read_text(encoding="utf-8")
VISUAL = (ROOT / "gui" / "native_visual_style.py").read_text(encoding="utf-8")
NATIVE = (ROOT / "gui" / "native_background.py").read_text(encoding="utf-8")
CLOCK = (ROOT / "gui" / "presentation_clock.py").read_text(encoding="utf-8")


def _body(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_reference_card_visual_states_are_preserved() -> None:
    for token in (
        "_NORMAL_SCALE = 1.00",
        "_HOVER_SCALE = 1.02",
        "_ACTIVE_SCALE = 1.00",
        "_NORMAL_ALPHA = 64.0",
        "_HOVER_ALPHA = 102.0",
        "_ACTIVE_ALPHA = 102.0",
        "_TRANSITION_MS = 300",
        "QPointF(0.25, 0.10)",
        "QPointF(0.25, 1.00)",
    ):
        assert token in CARD


def test_card_input_is_driven_by_shared_clock_not_its_own_sampler() -> None:
    assert "def presentation_tick" in CARD
    assert "self.card_fx.presentation_tick(" in CLOCK
    assert "QCursor" not in CARD
    assert "QApplication.mouseButtons" not in CARD
    assert "_pointer_timer" not in CARD
    assert "_motion_timer" not in CARD
    assert "self.window.childAt(local)" in CARD
    assert "widget.installEventFilter(self)" not in CARD


def test_motion_reverses_from_current_interpolated_state() -> None:
    animate = _body(CARD, "def _animate_to", "def _normal")
    assert "self._advance_state(state, now_s)" in animate
    assert "state.from_scale = state.current_scale" in animate
    assert "state.from_alpha = state.current_alpha" in animate
    assert "state.target_scale = scale" in animate
    assert "state.target_alpha = alpha" in animate
    assert "_MIN_PRESSED_MS" not in CARD


def test_motion_cost_is_bounded_to_active_cards() -> None:
    assert "self._moving_frames: set[QFrame] = set()" in CARD
    assert "_MAX_CONCURRENT_MOTIONS = 2" in CARD
    advance = _body(CARD, "def _advance_motions", "def _animate_to")
    assert "for frame in tuple(self._moving_frames):" in advance
    assert "for state in self.states.values():" not in advance
    assert "self._moving_frames.discard(frame)" in advance


def test_motion_uses_one_frozen_widget_composite_then_thaws_at_endpoint() -> None:
    recapture = _body(CARD, "def _recapture_for_motion", "def _retire_stale_motions")
    assert "self._set_content_frozen(state, False)" in recapture
    assert "self._set_content_frozen(state, True)" in recapture

    advance = _body(CARD, "def _advance_state", "def _advance_motions")
    assert "if not state.moving:" in advance
    assert "self._set_content_frozen(state, False)" in advance

    effect = _body(VISUAL, "class _CardScaleEffect", "class NativeGlassProxy")
    assert "self.sourcePixmap(" in effect
    assert "return self._frozen_source, self._frozen_offset" in effect
    assert "self._freeze_requested = False" in effect


def test_hover_clearance_cache_follows_geometry_revision() -> None:
    key = _body(CARD, "def _geometry_cache_key", "def _available_edge_growth")
    assert 'getattr(background, "_geometry_revision", -1)' in key
    assert "int(self.window.width())" in key
    assert "int(self.window.height())" in key
    assert "len(self.states)" in key

    rebuild = _body(CARD, "def _rebuild_hover_scale_cache", "def _hover_scale_for")
    assert "self._available_edge_growth(frame, reference_growth, rects)" in rebuild
    assert "self._hover_scale_cache = cache" in rebuild


def test_clearance_math_keeps_reference_growth_and_neighbor_protection() -> None:
    for token in (
        "_REFERENCE_CARD_SPAN_PX = 300.0",
        "_REFERENCE_EDGE_GROWTH_PX",
        "_MIN_NEIGHBOR_GAP_PX = 1.0",
        "_WINDOW_EDGE_GAP_PX = 1.0",
    ):
        assert token in CARD
    clearance = _body(CARD, "def _available_edge_growth", "def _rebuild_hover_scale_cache")
    assert "frame.isAncestorOf(other)" in clearance
    assert "other.isAncestorOf(frame)" in clearance
    assert "horizontal_overlap" in clearance
    assert "vertical_overlap" in clearance


def test_quick_shell_and_widget_content_share_one_scale_value() -> None:
    assert "SCALE_ROLE = _ROLE_BASE + 11" in NATIVE
    assert 'SCALE_ROLE: "cardScale"' in NATIVE
    assert "scale: cardScale" in NATIVE
    assert "transformOrigin: Item.Center" in NATIVE

    proxy = _body(VISUAL, "class NativeGlassProxy", "class NativeVisualStyleController")
    assert "self.background.set_card_presentation(" in proxy
    assert "self._scale_effect.set_scale(scale)" in proxy


def test_modal_suspend_resets_card_state_without_timer_lifecycle() -> None:
    suspend = _body(CARD, "def suspend_for_modal", "def resume_from_modal")
    resume = _body(CARD, "def resume_from_modal", "def _cleanup")
    assert "self._moving_frames.clear()" in suspend
    assert "state.snap(_NORMAL_SCALE, _NORMAL_ALPHA)" in suspend
    assert "self._hover_scale_cache.clear()" in resume
    assert "self._hover_scale_cache_key = None" in resume
    assert "state.snap(_NORMAL_SCALE, _NORMAL_ALPHA)" in resume
    assert "timer" not in suspend.lower()
    assert "timer" not in resume.lower()


def test_sources_compile_without_importing_pyside() -> None:
    for relative in (
        "gui/nekro_card_fx.py",
        "gui/native_visual_style.py",
        "gui/native_background.py",
        "gui/presentation_clock.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        compile(source, relative, "exec")
