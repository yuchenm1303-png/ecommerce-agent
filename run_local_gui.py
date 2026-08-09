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
    from gui.nekro_sakura import install_nekro_sakura
    from gui.nekro_visual_fx import NekroOverlay, install_nekro_visual_fx
    from gui.pastel_background import install_pastel_background

    app = QApplication(sys.argv)
    app.setApplicationName("ecommerce-agent Read-only Lab")
    app.setOrganizationName("ecommerce-agent")
    window = MainWindow(Path(__file__).resolve().parent)
    window.show()

    # Disable the earlier approximation. Sakura now comes from the dedicated
    # port of nekro.top's production canvas_sakura motion model below.
    NekroOverlay.PETAL_COUNT = 0
    visual_fx = install_nekro_visual_fx(window)

    # Use our own non-black wallpaper while preserving the original nekro/imsyy
    # glass sampling, cursor and card interaction behavior.
    install_pastel_background(visual_fx.background)

    # Source .cards behavior: scale(1) -> hover scale(1.01) -> active scale(.98),
    # with the original 0.3s CSS-style transition. Layout remains our test GUI.
    install_nekro_card_fx(window)

    # Original canvas_sakura motion model with a deliberately reduced particle
    # count for this development tool (source site uses 50).
    install_nekro_sakura(window, count=12)

    # Keep the original cursor follower above the sakura canvas, matching the
    # browser presentation where the cursor remains visually readable.
    visual_fx.overlay.raise_()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
