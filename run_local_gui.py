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

    from gui.main_window import MainWindow
    from gui.nekro_card_fx import install_nekro_card_fx
    from gui.nekro_overlay60 import install_nekro_overlay60
    from gui.nekro_visual_fx import NekroOverlay, install_nekro_visual_fx
    from gui.pastel_background import install_pastel_background
    from gui.performance_tuning import install_gui_performance_tuning

    app = QApplication(sys.argv)
    app.setApplicationName("ecommerce-agent Read-only Lab")
    app.setOrganizationName("ecommerce-agent")
    window = MainWindow(Path(__file__).resolve().parent)
    window.show()

    # nekro_visual_fx still owns the static glass/background/native white-dot
    # cursor, but its old animated overlay is disabled immediately below.
    NekroOverlay.PETAL_COUNT = 0
    visual_fx = install_nekro_visual_fx(window)

    # Stop/hide the legacy full-window animation layer and batch dense log UI.
    install_gui_performance_tuning(window, visual_fx)

    # Local non-black wallpaper; glass cards still sample its cached 10px blur.
    install_pastel_background(visual_fx.background)

    # Original source card hover/active states, idle timer sleeps when finished.
    install_nekro_card_fx(window)

    # One presentation surface only: original sakura sprite/motion + original
    # follower-circle semantics at a 60-fps target. No second transparent layer.
    overlay = install_nekro_overlay60(window, sakura_count=12)
    overlay.raise_()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
