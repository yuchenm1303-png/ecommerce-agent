from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print(
            "缺少开发 GUI 依赖 PySide6。\n"
            "请在 ecommerce-agent 当前 Python/venv 中执行：\n"
            "  python -m pip install -r requirements-gui.txt\n",
            file=sys.stderr,
        )
        return 2

    from gui.console_window import MainWindow
    from gui.log_presenter import install_buffered_logs
    from gui.nekro_card_fx import install_nekro_card_fx
    from gui.nekro_effects import install_nekro_effects
    from gui.visual_perf import VisualPerfRecorder
    from gui.visual_perf_hooks import install_visual_perf_hooks
    from gui.visual_style import install_visual_style

    app = QApplication(sys.argv)
    app.setApplicationName("ecommerce-agent Read-only Lab")
    app.setOrganizationName("ecommerce-agent")

    # One global filter gives every scrollable surface continuous per-pixel
    # wheel scrolling (see gui/smooth_scroll.py).
    from gui.smooth_scroll import SmoothWheelFilter

    app.installEventFilter(SmoothWheelFilter(app))

    project_root = Path(__file__).resolve().parent
    window = MainWindow(project_root)
    visual = install_visual_style(window)
    card_fx = install_nekro_card_fx(window, visual)
    buffered_logs = install_buffered_logs(window)
    effects = install_nekro_effects(window, sakura_count=3)

    # Diagnostics are dormant unless Ctrl+Alt+P starts a capture. While idle the
    # wrappers only take a single boolean branch and perform no sampling or IO.
    recorder = VisualPerfRecorder(project_root)
    install_visual_perf_hooks(
        window,
        visual,
        effects,
        card_fx,
        buffered_logs,
        recorder,
    )

    window.show()
    effects.raise_()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
