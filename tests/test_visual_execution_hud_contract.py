from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HUD = ROOT / "app" / "makro" / "visual_execution_hud.py"
EXECUTOR = ROOT / "makro_execute_listing.py"


def test_visual_execution_hud_source_compiles() -> None:
    for path in (HUD, EXECUTOR):
        source = path.read_text(encoding="utf-8")
        compile(source, str(path), "exec")


def test_hud_ports_existing_visual_language_without_owning_input() -> None:
    source = HUD.read_text(encoding="utf-8")
    for token in (
        "visual_agent_hud_runtime.html",
        "visual_agent_hud_base.css",
        "visual_agent_hud_cursor.css",
        "edge-aurora",
        "mouseCursorShape",
        "info-bubble",
        "bottom-timeline",
        "pointer-events:none",
        "Playwright + Live DOM",
    ):
        assert token in source

    for event in ("focusin", "pointerdown", "click", "input", "change"):
        assert f"listen(document,'{event}'" in source

    assert "getBoundingClientRect()" in source
    assert "window.innerWidth" in source
    assert "window.innerHeight" in source
    assert "position:fixed" in source
    assert "100vw" in source and "100vh" in source
    assert "pyautogui" not in source
    assert "Send to QC" not in source


def test_real_executor_owns_hud_lifecycle_and_keeps_final_screenshot_clean() -> None:
    source = EXECUTOR.read_text(encoding="utf-8")
    assert "install_visual_execution_hud(page)" in source
    assert "set_visual_execution_hud_capture_safe(page, True)" in source
    assert "set_visual_execution_hud_capture_safe(page, False)" in source
    assert "finish_visual_execution_hud(page, success=visual_success)" in source
    assert "destroy_visual_execution_hud(page)" in source
    assert '"visual_execution_hud": visual_hud_installed' in source
    assert '"send_to_qc_clicked": False' in source


def test_hud_starts_only_after_prewrite_reconcile() -> None:
    source = EXECUTOR.read_text(encoding="utf-8")
    checkpoint = source.index('"step3_prewrite"')
    install = source.index("install_visual_execution_hud(page)")
    first_fill = source.index("_fill_one_section(", install)
    assert checkpoint < install < first_fill
