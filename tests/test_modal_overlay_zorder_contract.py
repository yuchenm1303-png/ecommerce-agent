from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_legacy_quick_modal_runtime_is_deleted() -> None:
    assert not (ROOT / "gui" / "modal_interaction.py").exists()
    assert not (ROOT / "gui" / "modal_overlay_zorder.py").exists()
    assert "modal_overlay_zorder" not in RUNNER
    assert "install_modal_overlay_zorder" not in RUNNER
    assert "install_modal_interaction" not in RUNNER
    assert "static_modal_interaction" in RUNNER


def test_only_native_quick_runtime_is_the_background_owner() -> None:
    assert "install_native_visual_style(window)" in RUNNER
    assert "visual.background.quick_window" in RUNNER
    assert "install_native_window_shell(window, quick_window)" in RUNNER
