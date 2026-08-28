from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC_VIEW = (ROOT / "gui" / "static_qml_view.py").read_text(encoding="utf-8")
STABILITY = (ROOT / "gui" / "startup_entrance_stability.py").read_text(encoding="utf-8")


def _body(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def _without_comments(source: str) -> str:
    return "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )


def test_native_handoff_runs_after_quick_frame_callback() -> None:
    assert "handoffFrameReady = Signal()" in STATIC_VIEW
    assert "Qt.ConnectionType.QueuedConnection" in STATIC_VIEW
    assert "self.quick.frameSwapped.connect(self._on_handoff_frame_swapped)" in STATIC_VIEW
    assert "self.quick.frameSwapped.disconnect(self._on_handoff_frame_swapped)" in STATIC_VIEW

    frame_callback = _without_comments(
        _body(
            STATIC_VIEW,
            "def _on_handoff_frame_swapped(self) -> None:",
            "def _disconnect_handoff",
        )
    )
    for forbidden in (
        "set_overlay_presented",
        "requestUpdate",
        "setProperty",
        "repaint",
        "update()",
    ):
        assert forbidden not in frame_callback
    assert "self._post_handoff_commit()" in frame_callback

    commit = _body(STATIC_VIEW, "def _commit_handoff(self) -> None:", "def _activate_after_startup")
    assert "self.shell.set_overlay_presented(False)" in commit


def test_reveal_frame_callback_is_observation_only() -> None:
    assert "revealFrameReady = Signal()" in STABILITY
    assert "Qt.ConnectionType.QueuedConnection" in STABILITY
    assert "quick.frameSwapped.connect(self._on_reveal_frame_swapped)" in STABILITY

    frame_callback = _without_comments(
        _body(
            STABILITY,
            "def _on_reveal_frame_swapped(self) -> None:",
            "def _consume_reveal_frame",
        )
    )
    for forbidden in (
        "_flush_native_background",
        "setProperty",
        "requestUpdate",
        "repaint",
        "update()",
    ):
        assert forbidden not in frame_callback
    assert "self.revealFrameReady.emit()" in frame_callback

    consume = _body(STABILITY, "def _consume_reveal_frame(self) -> None:", "def _start_entrance")
    assert "self._flush_native_background()" in consume


def test_frame_boundary_sources_compile() -> None:
    compile(STATIC_VIEW, "gui/static_qml_view.py", "exec")
    compile(STABILITY, "gui/startup_entrance_stability.py", "exec")
