from __future__ import annotations

import os
import sys
from pathlib import Path


# The native QQuickWindow background owns wallpaper presentation. Set the render
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

    app = QApplication(sys.argv)
    app.setApplicationName("ecommerce-agent Read-only Lab")
    app.setOrganizationName("ecommerce-agent")

    # One global filter gives every scrollable surface continuous per-pixel
    # wheel scrolling (see gui/smooth_scroll.py).
    from gui.smooth_scroll import SmoothWheelFilter

    app.installEventFilter(SmoothWheelFilter(app))

    window = MainWindow(Path(__file__).resolve().parent)
    visual = install_visual_style(window)
    quick_window = getattr(visual.background, "quick_window", None)
    install_native_window_shell(
        window,
        quick_window,
        legacy_frame=getattr(visual, "window_frame", None),
    )
    install_nekro_card_fx(window, visual)
    install_buffered_logs(window)
    effects = install_nekro_effects(window, sakura_count=3)

    window.show()
    effects.raise_()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
