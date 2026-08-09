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
    from gui.performance_tuning import install_gui_performance_tuning

    app = QApplication(sys.argv)
    app.setApplicationName("ecommerce-agent Read-only Lab")
    app.setOrganizationName("ecommerce-agent")
    window = MainWindow(Path(__file__).resolve().parent)
    window.show()

    # Disable the old approximate sakura particles in nekro_visual_fx. The
    # dedicated source-sprite implementation below is the only sakura layer.
    NekroOverlay.PETAL_COUNT = 0
    visual_fx = install_nekro_visual_fx(window)

    # Stop the legacy permanent 60-fps full-window follower repaint, replace it
    # with a dirty-region/on-demand cursor scheduler, and batch live log UI work.
    performance = install_gui_performance_tuning(window, visual_fx)

    # Local non-black wallpaper; original card glass sampling stays intact.
    install_pastel_background(visual_fx.background)

    # Original .cards states, now with an animation timer that sleeps while idle.
    install_nekro_card_fx(window)

    # Original nekro.top sakura sprite/motion, 12 particles. The Qt renderer
    # paints at 30 fps with time-scaled motion and dirty-region invalidation.
    install_nekro_sakura(window, count=12)

    # Cursor follower should remain visually above the transparent sakura canvas.
    performance.raise_cursor()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
