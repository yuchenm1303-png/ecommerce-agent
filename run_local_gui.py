from __future__ import annotations

import os
import sys
from pathlib import Path


# The only performance-specific process setting. It must be set before Qt is
# imported so QQuickWindow FrameAnimation follows the threaded scene graph.
os.environ.setdefault("QSG_RENDER_LOOP", "threaded")


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

    # Keep the baseline import contract for visual tests, then layer the current
    # business workflow over that preserved QWidget shell.
    from gui.card_details_fast import install_card_details
    from gui.console_window import MainWindow
    from gui.workflow_console_window import WorkflowMainWindow
    from gui.log_presenter import install_buffered_logs
    from gui.native_visual_style import install_native_visual_style
    from gui.native_window_shell import install_native_window_shell
    from gui.nekro_card_fx import install_nekro_card_fx
    from gui.nekro_effects import install_nekro_effects
    from gui.ui_polish import install_ui_polish

    MainWindow = WorkflowMainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("ecommerce-agent Current Workflow")
    app.setOrganizationName("ecommerce-agent")

    # Preserve the baseline global smooth-scroll behavior unchanged.
    from gui.smooth_scroll import SmoothWheelFilter

    app.installEventFilter(SmoothWheelFilter(app))

    window = MainWindow(Path(__file__).resolve().parent)
    visual = install_native_visual_style(window)

    # Final presentation pass only. This runs after NEKRO/native compatibility
    # styling so table density, spacing and control styling are deterministic,
    # while all business widgets/controllers remain the original instances.
    install_ui_polish(window)

    # Card details are created after the responsive layout is finalized but
    # before the native shell/focus and card-hover controllers enumerate the
    # widget tree. The fast controller keeps the drawer at fixed size and runs
    # only short-lived 8 ms move/reveal motion during open/close.
    install_card_details(window)

    quick_window = visual.background.quick_window
    if quick_window is None:
        raise RuntimeError("Native Quick renderer was not created")
    shell = install_native_window_shell(window, quick_window)

    # These are the baseline controllers, unchanged. In particular the card
    # hover/press timing and easing remain exactly the d89cbcc implementation.
    install_nekro_card_fx(window, visual)
    install_buffered_logs(window)
    effects = install_nekro_effects(window, sakura_count=3)

    shell.show()
    effects.raise_()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
