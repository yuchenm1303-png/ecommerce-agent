from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GUI_SOURCES = [
    PROJECT_ROOT / "run_local_gui.py",
    PROJECT_ROOT / "gui" / "quick_bridge.py",
    PROJECT_ROOT / "gui" / "readonly_runner.py",
    PROJECT_ROOT / "gui" / "real_execution.py",
    PROJECT_ROOT / "gui" / "result_loader.py",
]


def test_gui_python_sources_compile_without_importing_pyside() -> None:
    for path in GUI_SOURCES:
        source = path.read_text(encoding="utf-8")
        compile(source, str(path), "exec")
