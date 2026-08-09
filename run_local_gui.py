from __future__ import annotations

import os
import sys
from pathlib import Path


# The framed native QQuickWindow owns wallpaper presentation. Set the render
# loop before importing Qt so FrameAnimation follows the scene-graph cadence.
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

    from gui.console_window import MainWindow
    from gui.log_presenter import install_buffered_logs
    from gui.native_window_shell import install_native_window_shell
    from gui.nekro_card_fx import install_nekro_card_fx
    from gui.nekro_effects import install_nekro_effects
    from gui.visual_style import install_visual_style
    from gui.window_diagnostics import install_window_diagnostics

    app = QApplication(sys.argv)
    app.setApplicationName("ecommerce-agent Read-only Lab")
    app.setOrganizationName("ecommerce-agent")

    from gui.smooth_scroll import SmoothWheelFilter

    app.installEventFilter(SmoothWheelFilter(app))

    project_root = Path(__file__).resolve().parent
    window = MainWindow(project_root)
    visual = install_visual_style(window)
    quick_window = visual.background.quick_window
    if quick_window is None:
        raise RuntimeError("Native Quick application window was not created")
    shell = install_native_window_shell(window, quick_window)

    install_nekro_card_fx(window, visual)
    install_buffered_logs(window)
    effects = install_nekro_effects(window, sakura_count=3)

    shell.show()
    effects.raise_()

    # Disabled by default. Set ECOM_GUI_DIAGNOSTICS=1 for one-run geometry,
    # Win32 ownership/Z-order, DPI and Quick frame-cadence telemetry.
    install_window_diagnostics(window, visual.background, shell, project_root)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
