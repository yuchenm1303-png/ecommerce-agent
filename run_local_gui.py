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
    from gui.nekro_sakura import install_nekro_sakura
    from gui.nekro_visual_fx import NekroOverlay, install_nekro_visual_fx

    app = QApplication(sys.argv)
    app.setApplicationName("ecommerce-agent Read-only Lab")
    app.setOrganizationName("ecommerce-agent")
    window = MainWindow(Path(__file__).resolve().parent)
    window.show()

    # Disable the earlier approximation. Sakura now comes from the dedicated
    # port of nekro.top's production canvas_sakura motion model below.
    NekroOverlay.PETAL_COUNT = 0
    visual_fx = install_nekro_visual_fx(window)
    install_nekro_sakura(window, count=12)

    # Keep the original cursor follower above the sakura canvas, matching the
    # browser presentation where the cursor remains visually readable.
    visual_fx.overlay.raise_()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
