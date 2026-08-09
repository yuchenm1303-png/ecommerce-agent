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
    from gui.nekro_visual_fx import install_nekro_visual_fx

    app = QApplication(sys.argv)
    app.setApplicationName("ecommerce-agent Read-only Lab")
    app.setOrganizationName("ecommerce-agent")
    window = MainWindow(Path(__file__).resolve().parent)
    window.show()
    install_nekro_visual_fx(window)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
