from __future__ import annotations

import os
import sys
from pathlib import Path

from app.app_branding import apply_qt_application_icon
from app.runtime_paths import runtime_root

os.environ.setdefault("QSG_RENDER_LOOP", "threaded")


def main() -> int:
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtQuick import QQuickWindow
        from PySide6.QtWidgets import QApplication, QAbstractScrollArea, QLabel, QSizePolicy
    except ImportError:
        print(
            "缺少开发 GUI 依赖 PySide6。\n"
            "请在 ecommerce-agent 当前 Python/venv 中执行：\n"
            "  python -m pip install -r requirements-gui.txt\n",
            file=sys.stderr,
        )
        return 2

    from gui.activity_presence import install_activity_presence
    from gui.background_render_optimizations import (
        install_background_pointer_hotpath,
        install_preblur_cache,
    )
    from gui.batch_card_responsive import install_batch_card_responsive
    from gui.batch_job_controls import install_batch_job_controls
    from gui.batch_url_editor import install_batch_url_editor
    from gui.batch_workspace_density import install_batch_workspace_density
    from gui.browser_session_manager import install_managed_makro_browser
    from gui.card_details_fast import install_card_details
    from gui.console_summary_mode import install_console_summary_mode
    from gui.console_window import MainWindow
    from gui.workflow_console_window import WorkflowMainWindow
    from gui.frozen_process_router import install_frozen_process_router
    from gui.listing_offer_hardening import install_listing_offer_hardening
    from gui.listing_offer_support import install_listing_offer_support
    from gui.log_presenter import install_buffered_logs
    from gui.mode_toggle import install_workspace_mode_switch
    from gui.native_visual_style import install_native_visual_style
    from gui.native_window_shell import install_native_window_shell
    from gui.nekro_card_fx import install_nekro_card_fx
    from gui.nekro_effects import install_nekro_effects
    from gui.page_scroll_layout import install_page_scroll_layout
    from gui.preparation_progress import install_detailed_preparation_progress
    from gui.product_copy import install_product_copy
    from gui.premium_copy import install_premium_copy
    from gui.required_input_support import install_required_input_support
    from gui.restore_snapshot import install_restore_snapshot
    from gui.runtime_assistant import install_runtime_assistant
    from gui.single_top_compact import install_single_top_compact
    from gui.startup_entrance import install_startup_entrance
    from gui.startup_entrance_stability import install_startup_entrance_stability
    from gui.static_modal_interaction import install_static_modal_interaction
    from gui.ui_maturity import install_mature_ui
    from gui.ui_polish import install_ui_polish
    from gui.ui_runtime_optimizations import install_ui_runtime_optimizations
    from gui.workspace_transition import install_workspace_transition
    from gui.workspace_transition_tuning import apply_workspace_transition_tuning
    from gui.smooth_scroll import SmoothWheelFilter

    MainWindow = WorkflowMainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("ecommerce-agent Listing Studio")
    app.setOrganizationName("ecommerce-agent")
    apply_qt_application_icon(app)

    # Keep the native Fuji renderer compatible with the translucent QWidget
    # child surface. Modal presentation itself now stays entirely in QWidget.
    QQuickWindow.setDefaultAlphaBuffer(True)

    # Preserve the exact established blur pixels while avoiding repeated
    # QGraphicsBlurEffect work on later launches. The native renderer still writes
    # the same JPG92 texture and keeps the same QML glass/mask pipeline.
    install_preblur_cache()

    window = MainWindow(runtime_root())
    visual = install_native_visual_style(window)

    # Finish the proven Single workspace presentation before wrapping it in the
    # final page scroll. Existing business widgets/signals are preserved and only
    # their presentation ownership changes.
    install_ui_polish(window)

    # Keep the right-side Telemetry/Web/Safety card at one stable non-scrolling
    # height. It must not grow with the old side viewport.
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

    details = install_card_details(window)
    mature = install_mature_ui(window)
    details.attach_mature(mature)

    # ui_maturity gets the last compact/responsive pass first. Then the final
    # Single geometry owner replaces the viewport-competing body splitter with one
    # content-driven vertical page: Product Source → statuses → fields → console.
    # The common application header remains fixed above this scroll area.
    install_page_scroll_layout(window, visual)

    install_console_summary_mode(window)
    install_static_modal_interaction(window, details)

    window.install_mode_workspace()
    # Batch input is a thin rail by default; detailed per-link editing opens only
    # on demand so the owned-job workspace keeps the majority of vertical space.
    install_batch_url_editor(window.batch_workspace)
    install_batch_workspace_density(window.batch_workspace)
    # Each owned product card gets its own scheduler controls. The control layer
    # only selects existing canonical queues/executor paths; it never duplicates
    # Resolver, Makro execution or Product Photos business logic.
    install_batch_job_controls(window.batch_workspace)
    # Long supplier URLs/logs are display data, not minimum-width constraints.
    # Keep every owned-job card capped to the real Batch viewport so right-side
    # actions and per-job controls remain visible without horizontal scrolling.
    install_batch_card_responsive(window.batch_workspace)
    install_workspace_mode_switch(window)
    # Source runs keep their existing Python subprocess behavior. Frozen builds
    # switch those exact canonical helpers to the packaged console worker before
    # browser/session wrappers capture any execution entry points.
    install_frozen_process_router(window)
    # Formal GUI owns one dedicated Makro Edge/Profile. 9222 remains an internal
    # transport detail; Single and Batch share one login session and Batch keeps
    # per-job isolation through owned tabs/target ids.
    install_managed_makro_browser(window)
    install_required_input_support(window)
    # Offer intent is layered after required-input support so high-risk required
    # fields (title/package/identifier/compliance) can opt out of generic N/A/1
    # fallback without replacing the ordinary required-field mechanism.
    install_listing_offer_support(window)
    # Freeze offer ownership after the main support layer exists: duplicate
    # supplier URLs may represent different sold bundles in Batch, while Single
    # execution is invalidated if the offer is edited after preparation.
    install_listing_offer_hardening(window)
    install_single_top_compact(window)
    install_activity_presence(window)
    install_detailed_preparation_progress(window)
    visual.refresh_glass_frames()

    # Presentation-only hot-path optimizations are installed after both Single
    # and Batch widgets exist, but still before the first event-loop paint.
    install_ui_runtime_optimizations(window, visual)
    # Keep the established 8 ms parallax semantics but replace the temporary
    # runtime wrapper with a one-cursor-read, geometry-signal-cached bridge.
    install_background_pointer_hotpath(window, visual)

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

    # Card performance budgeting is native to the controller/effect hot path:
    # one live interactive card, one frozen outgoing card, 90 Hz maximum motion
    # clock, and sub-pixel QWidget raster publication.
    install_nekro_card_fx(window, visual)
    install_buffered_logs(window)
    effects = install_nekro_effects(window, sakura_count=3)

    # Keep the stabilized workspace transition implementation unchanged. Only its
    # presentation timing tokens are tuned here.
    apply_workspace_transition_tuning()
    install_workspace_transition(window, visual)

    # Product copy owns user-facing wording. The premium layer keeps concise
    # Chinese actions while restoring English hierarchy/status labels.
    product_copy = install_product_copy(window)
    legacy_headings = {
        "BATCH LISTING · MULTI PRODUCT QUEUE": "BATCH QUEUE",
        "JOB CONTROL · OWNED TAB ISOLATION · LIVE TELEMETRY": "LISTING TASK",
    }
    for label in window.findChildren(QLabel):
        replacement = legacy_headings.get(label.text())
        if replacement is not None:
            label.setText(replacement)
    premium_copy = install_premium_copy(window)

    # Reference-site startup choreography is a one-shot presentation surface. It
    # freezes pointer/card effects while visible and keeps the original visual
    # timeline. A separate handoff gate waits for the maximized QWidget geometry
    # to settle before capture, then restores runtime presentation over separate
    # frames so the final snapshot/live swap cannot jump or stall.
    entrance = install_startup_entrance(window, visual)
    entrance_stability = install_startup_entrance_stability(window, entrance)

    shell.show()
    effects.raise_()
    assistant = install_runtime_assistant(window)
    product_copy.attach_runtime_assistant(assistant)
    premium_copy.attach_runtime_assistant(assistant)
    assistant.raise_()
    entrance.raise_overlay()
    entrance_stability.start()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
