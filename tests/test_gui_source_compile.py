from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GUI_SOURCES = [
    PROJECT_ROOT / "run_local_gui.py",
    PROJECT_ROOT / "makro_gui_workflow.py",
    PROJECT_ROOT / "gui" / "main_window.py",
    PROJECT_ROOT / "gui" / "console_window.py",
    PROJECT_ROOT / "gui" / "workflow_console_window.py",
    PROJECT_ROOT / "gui" / "acceptance_console.py",
    PROJECT_ROOT / "gui" / "readonly_runner.py",
    PROJECT_ROOT / "gui" / "real_execution.py",
    PROJECT_ROOT / "gui" / "result_loader.py",
    PROJECT_ROOT / "gui" / "visual_style.py",
    PROJECT_ROOT / "gui" / "nekro_card_fx.py",
    PROJECT_ROOT / "gui" / "nekro_effects.py",
    PROJECT_ROOT / "gui" / "log_presenter.py",
]


def test_gui_python_sources_compile_without_importing_pyside() -> None:
    """Catch syntax regressions even though core CI intentionally omits PySide6."""

    for path in GUI_SOURCES:
        source = path.read_text(encoding="utf-8")
        compile(source, str(path), "exec")
