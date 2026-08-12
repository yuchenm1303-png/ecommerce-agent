from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QSG_RENDER_LOOP", "threaded")


def main() -> int:
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtQuick import QQuickWindow
        from PySide6.QtWidgets import QApplication, QAbstractScrollArea, QSizePolicy
    except ImportError:
        print(
            "缺少开发 GUI 依赖 PySide6。\n"
            "请在 ecommerce-agent 当前 Python/venv 中执行：\n"
            "  python -m pip install -r requirements-gui.txt\n",
            file=sys.stderr,
        )
        return 2

    from gui.activity_presence import install_activity_presence
    from gui.browser_session_manager import install_managed_makro_browser
    from gui.card_details_fast import install_card_details
    from gui.console_summary_mode import install_console_summary_mode
    from gui.console_window import MainWindow
    from gui.workflow_console_window import WorkflowMainWindow
    from gui.log_presenter import install_buffered_logs
    from gui.mode_toggle import install_workspace_mode_switch
    from gui.native_visual_style import install_native_visual_style
    from gui.native_window_shell import install_native_window_shell
    from gui.nekro_card_fx import install_nekro_card_fx
    from gui.nekro_effects import install_nekro_effects
    from gui.page_scroll_layout import install_page_scroll_layout
    from gui.preparation_progress import install_detailed_preparation_progress
    from gui.required_input_support import install_required_input_support
    from gui.restore_snapshot import install_restore_snapshot
    from gui.runtime_assistant import install_runtime_assistant
    from gui.static_modal_interaction import install_static_modal_interaction
    from gui.ui_maturity import install_mature_ui
    from gui.ui_polish import install_ui_polish
    from gui.ui_runtime_optimizations import install_ui_runtime_optimizations
    from gui.workspace_transition import install_workspace_transition
    from gui.workspace_transition_tuning import apply_workspace_transition_tuning
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

    # Keep the right-side Telemetry/Web/Safety card at one stable non-scrolling
    # height. It must not grow with the outer side viewport or drift toward the
    # lower console as the window becomes taller.
    side_tabs = getattr(window, "side_detail_tabs", None)
    if side_tabs is not None:
        side_tabs.setFixedHeight(300)
        side_tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        side_host = side_tabs.parentWidget()
        side_layout = side_host.layout() if side_host is not None else None
        if side_layout is not None:
            side_layout.setStretchFactor(side_tabs, 0)
            side_layout.setAlignment(side_tabs, Qt.AlignmentFlag.AlignTop)
            side_layout.addStretch(1)

        ancestor = side_host
        while ancestor is not None:
            if isinstance(ancestor, QAbstractScrollArea):
                ancestor.verticalScrollBar().setValue(0)
                ancestor.horizontalScrollBar().setValue(0)
                ancestor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                ancestor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                break
            ancestor = ancestor.parentWidget()

    # The Single body is content-driven instead of viewport-driven: Product Source,
    # status cards, field workspace and the full console live on one scrollable
    # page. The common application header remains fixed above it.
    install_page_scroll_layout(window, visual)

    details = install_card_details(window)
    mature = install_mature_ui(window)
    details.attach_mature(mature)
    install_console_summary_mode(window)
    install_static_modal_interaction(window, details)

    window.install_mode_workspace()
    install_workspace_mode_switch(window)
    # Formal GUI owns one dedicated Makro Edge/Profile. 9222 remains an internal
    # transport detail; Single and Batch share one login session and Batch keeps
    # per-job isolation through owned tabs/target ids.
    install_managed_makro_browser(window)
    install_required_input_support(window)
    install_activity_presence(window)
    install_detailed_preparation_progress(window)
    visual.refresh_glass_frames()

    # Presentation-only hot-path optimizations are installed after both Single
    # and Batch widgets exist, but still before the first event-loop paint.
    install_ui_runtime_optimizations(window, visual)

    smooth_wheel = SmoothWheelFilter(window)
    smooth_wheel.install(window)
    window._smooth_wheel_filter = smooth_wheel  # type: ignore[attr-defined]
    window.destroyed.connect(smooth_wheel.cleanup)

    quick_window = visual.background.quick_window
    if quick_window is None:
        raise RuntimeError("Native Quick renderer was not created")

    # Windows can discard the QWidget backing-store pixels while the native Quick
    # owner is minimized even though every widget object remains alive.
    install_restore_snapshot(window, quick_window)

    shell = install_native_window_shell(window, quick_window)

    # Card performance budgeting is now native to the controller/effect hot path:
    # one live interactive card, one frozen outgoing card, 90 Hz maximum motion
    # clock, and sub-pixel QWidget raster publication. No runtime monkey-patching.
    install_nekro_card_fx(window, visual)
    install_buffered_logs(window)
    effects = install_nekro_effects(window, sakura_count=3)

    # Keep the stabilized workspace transition implementation unchanged. Only its
    # presentation timing tokens are tuned here.
    apply_workspace_transition_tuning()
    install_workspace_transition(window, visual)

    shell.show()
    effects.raise_()
    # Runtime Assistant remains Phase 1 Shadow Mode: observe + explain only.
    assistant = install_runtime_assistant(window)
    assistant.raise_()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
