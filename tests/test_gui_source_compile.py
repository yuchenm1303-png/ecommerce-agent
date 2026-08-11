from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GUI_SOURCES = [
    PROJECT_ROOT / "run_local_gui.py",
    PROJECT_ROOT / "makro_gui_workflow.py",
    PROJECT_ROOT / "makro_batch_source.py",
    PROJECT_ROOT / "makro_batch_job.py",
    PROJECT_ROOT / "makro_execute_listing.py",
    PROJECT_ROOT / "app" / "browser_page_owner.py",
    PROJECT_ROOT / "app" / "makro" / "runtime_contract.py",
    PROJECT_ROOT / "app" / "makro" / "page_observation.py",
    PROJECT_ROOT / "app" / "makro" / "interruption_monitor.py",
    PROJECT_ROOT / "app" / "makro" / "recovery_agent.py",
    PROJECT_ROOT / "gui" / "main_window.py",
    PROJECT_ROOT / "gui" / "console_window.py",
    PROJECT_ROOT / "gui" / "workflow_console_window.py",
    PROJECT_ROOT / "gui" / "batch_model.py",
    PROJECT_ROOT / "gui" / "batch_runner.py",
    PROJECT_ROOT / "gui" / "batch_workspace.py",
    PROJECT_ROOT / "gui" / "acceptance_console.py",
    PROJECT_ROOT / "gui" / "activity_presence.py",
    PROJECT_ROOT / "gui" / "browser_session_manager.py",
    PROJECT_ROOT / "gui" / "preparation_progress.py",
    PROJECT_ROOT / "gui" / "runtime_event_bridge.py",
    PROJECT_ROOT / "gui" / "runtime_shadow_recovery.py",
    PROJECT_ROOT / "gui" / "runtime_assistant.py",
    PROJECT_ROOT / "gui" / "readonly_runner.py",
    PROJECT_ROOT / "gui" / "real_execution.py",
    PROJECT_ROOT / "gui" / "result_loader.py",
    PROJECT_ROOT / "gui" / "visual_style.py",
    PROJECT_ROOT / "gui" / "native_background.py",
    PROJECT_ROOT / "gui" / "native_visual_style.py",
    PROJECT_ROOT / "gui" / "native_window_shell.py",
    PROJECT_ROOT / "gui" / "ui_polish.py",
    PROJECT_ROOT / "gui" / "ui_maturity.py",
    PROJECT_ROOT / "gui" / "ui_runtime_optimizations.py",
    PROJECT_ROOT / "gui" / "restore_snapshot.py",
    PROJECT_ROOT / "gui" / "workspace_transition.py",
    PROJECT_ROOT / "gui" / "card_details.py",
    PROJECT_ROOT / "gui" / "card_details_fast.py",
    PROJECT_ROOT / "gui" / "modal_interaction.py",
    PROJECT_ROOT / "gui" / "console_summary_mode.py",
    PROJECT_ROOT / "gui" / "nekro_card_fx.py",
    PROJECT_ROOT / "gui" / "nekro_effects.py",
    PROJECT_ROOT / "gui" / "smooth_scroll.py",
    PROJECT_ROOT / "gui" / "log_presenter.py",
]


def test_gui_python_sources_compile_without_importing_pyside() -> None:
    for path in GUI_SOURCES:
        source = path.read_text(encoding="utf-8")
        compile(source, str(path), "exec")
