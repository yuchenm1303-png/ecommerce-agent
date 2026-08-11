from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QSG_RENDER_LOOP", "threaded")


def main() -> int:
    try:
        from PySide6.QtQuick import QQuickWindow
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print(
            "缺少开发 GUI 依赖 PySide6。\n"
            "请在 ecommerce-agent 当前 Python/venv 中执行：\n"
            "  python -m pip install -r requirements-gui.txt\n",
            file=sys.stderr,
        )
        return 2

    from gui.card_details_fast import install_card_details
    from gui.console_summary_mode import install_console_summary_mode
    from gui.console_window import MainWindow
    from gui.workflow_console_window import WorkflowMainWindow
    from gui.log_presenter import install_buffered_logs
    from gui.mode_toggle import install_compact_mode_toggle
    from gui.native_visual_style import install_native_visual_style
    from gui.native_window_shell import install_native_window_shell
    from gui.nekro_card_fx import install_nekro_card_fx
    from gui.nekro_effects import install_nekro_effects
    from gui.required_input_support import install_required_input_support
    from gui.static_modal_interaction import install_static_modal_interaction
    from gui.ui_maturity import install_mature_ui
    from gui.ui_polish import install_ui_polish
    from gui.smooth_scroll import SmoothWheelFilter

    MainWindow = WorkflowMainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("ecommerce-agent Current Workflow")
    app.setOrganizationName("ecommerce-agent")

    # Keep the native Fuji renderer compatible with the translucent QWidget
    # child surface. Modal presentation itself now stays entirely in QWidget.
    QQuickWindow.setDefaultAlphaBuffer(True)

    window = MainWindow(Path(__file__).resolve().parent)
    visual = install_native_visual_style(window)

    # Finish the proven Single workspace exactly as before. Batch wraps that
    # complete workspace afterwards, so old layout/card plugins never reinterpret
    # Batch controls as Single diagnostics.
    install_ui_polish(window)
    details = install_card_details(window)
    mature = install_mature_ui(window)
    details.attach_mature(mature)
    install_console_summary_mode(window)
    install_static_modal_interaction(window, details)

    window.install_mode_workspace()
    install_compact_mode_toggle(window)
    install_required_input_support(window)
    visual.refresh_glass_frames()

    smooth_wheel = SmoothWheelFilter(window)
    smooth_wheel.install(window)
    window._smooth_wheel_filter = smooth_wheel  # type: ignore[attr-defined]
    window.destroyed.connect(smooth_wheel.cleanup)

    quick_window = visual.background.quick_window
    if quick_window is None:
        raise RuntimeError("Native Quick renderer was not created")
    shell = install_native_window_shell(window, quick_window)

    install_nekro_card_fx(window, visual)
    install_buffered_logs(window)
    effects = install_nekro_effects(window, sakura_count=3)

    shell.show()
    effects.raise_()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
