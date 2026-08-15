from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from app.app_branding import apply_qt_application_icon
from app.runtime_paths import is_frozen, is_installed_distribution, runtime_root

os.environ.setdefault("QSG_RENDER_LOOP", "threaded")
_UPDATE_E2E_MARKER_ENV = "ECOMMERCE_AGENT_UPDATE_E2E_MARKER"


def _complete_update_e2e_probe() -> bool:
    """Let release CI prove the newly installed real GUI executable boots.

    This mode is reachable only through an explicit environment variable used by
    the Windows release gate.  It runs after Qt has initialized, records the
    actual installed EcommerceAgent.exe/version, then exits before account UI.
    """

    marker_text = str(os.getenv(_UPDATE_E2E_MARKER_ENV, "") or "").strip()
    if not marker_text:
        return False
    if not is_frozen():
        raise RuntimeError("update E2E probe requires a frozen application")

    executable = Path(sys.executable).resolve()
    version_file = executable.parent / "_internal" / "packaging" / "VERSION"
    version = version_file.read_text(encoding="utf-8").strip().lstrip("v")
    marker = Path(marker_text).expanduser().resolve()
    marker.parent.mkdir(parents=True, exist_ok=True)
    temp = marker.with_name(f".{marker.name}.{os.getpid()}.tmp")
    try:
        temp.write_text(
            json.dumps(
                {
                    "started": True,
                    "frozen": True,
                    "executable": str(executable),
                    "version": version,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        os.replace(temp, marker)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
    return True


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
    from gui.app_access import ensure_application_access, install_application_access
    from gui.resilient_app_updater import install_application_updater
    from gui.batch_card_responsive import install_batch_card_responsive
    from gui.batch_product_files import install_batch_product_files
    from gui.cooperative_pause import (
        install_cooperative_batch_job_controls,
        install_cooperative_pause,
    )
    from gui.batch_sku_spec_ui import install_batch_sku_spec_ui
    from gui.batch_url_editor import install_batch_url_editor
    from gui.batch_workspace_density import install_batch_workspace_density
    from gui.browser_session_manager import install_managed_makro_browser
    from gui.card_details_fast import install_card_details
    from gui.console_summary_mode import install_console_summary_mode
    from gui.field_table_transfer import install_field_table_transfer
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
    from gui.presentation_clock import install_presentation_clock
    from gui.product_copy import install_product_copy
    from gui.product_input_window import ProductInputWorkflowMainWindow
    from gui.premium_copy import install_premium_copy
    from gui.required_input_support import install_required_input_support
    from gui.restore_snapshot import install_restore_snapshot
    from gui.runtime_assistant import install_runtime_assistant
    from gui.single_ai_guidance import install_single_ai_guidance
    from gui.single_top_compact import install_single_top_compact
    from gui.smooth_scroll import SmoothWheelFilter
    from gui.startup_entrance import install_startup_entrance
    from gui.startup_entrance_stability import install_startup_entrance_stability
    from gui.static_modal_interaction import install_static_modal_interaction
    from gui.ui_data_optimizations import install_ui_data_optimizations
    from gui.ui_maturity import install_mature_ui
    from gui.ui_polish import install_ui_polish
    from gui.update_runtime import install_update_runtime
    from gui.wallpaper_cache import install_preblur_cache
    from gui.workspace_transition import install_workspace_transition
    from gui.workspace_transition_tuning import apply_workspace_transition_tuning

    MainWindow = ProductInputWorkflowMainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("ecommerce-agent Listing Studio")
    app.setOrganizationName("ecommerce-agent")
    apply_qt_application_icon(app)

    if _complete_update_e2e_probe():
        return 0

    access_session = ensure_application_access(app)
    if access_session is None:
        return 0

    QQuickWindow.setDefaultAlphaBuffer(True)
    install_preblur_cache()

    window = MainWindow(runtime_root())
    access_controller = install_application_access(window, access_session)
    visual = install_native_visual_style(window)

    install_ui_polish(window)

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
    install_page_scroll_layout(window, visual)
    install_console_summary_mode(window)
    install_static_modal_interaction(window, details)
    install_field_table_transfer(window)

    window.install_mode_workspace()
    install_batch_url_editor(window.batch_workspace)
    install_batch_workspace_density(window.batch_workspace)
    install_cooperative_batch_job_controls(window.batch_workspace)
    install_batch_card_responsive(window.batch_workspace)
    install_workspace_mode_switch(window)
    install_frozen_process_router(window)
    install_managed_makro_browser(window)
    install_required_input_support(window)
    install_listing_offer_support(window)
    install_single_ai_guidance(window)
    install_batch_sku_spec_ui(window)
    install_batch_product_files(window)
    install_listing_offer_hardening(window)
    install_single_top_compact(window)
    install_cooperative_pause(window)
    install_activity_presence(window)
    install_detailed_preparation_progress(window)
    visual.refresh_glass_frames()

    # Low-frequency table/log work is optimized separately from rendering. It no
    # longer monkey-patches the Quick background or owns presentation timers.
    install_ui_data_optimizations(window)

    smooth_wheel = SmoothWheelFilter(window)
    smooth_wheel.install(window)
    window._smooth_wheel_filter = smooth_wheel  # type: ignore[attr-defined]
    window.destroyed.connect(smooth_wheel.cleanup)

    quick_window = visual.background.quick_window
    if quick_window is None:
        raise RuntimeError("Native Quick renderer was not created")
    install_restore_snapshot(window, quick_window)
    shell = install_native_window_shell(window, quick_window)

    # One shared 8 ms clock samples QCursor exactly once. Background parallax,
    # card interaction and the lightweight sakura/cursor surface consume that
    # same sample and apply their own cadence budgets.
    card_fx = install_nekro_card_fx(window, visual)
    install_buffered_logs(window)
    effects = install_nekro_effects(window, sakura_count=3)
    install_presentation_clock(
        window,
        background=visual.background,
        card_fx=card_fx,
        effects=effects,
    )

    apply_workspace_transition_tuning()
    install_workspace_transition(window, visual)

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

    # Source builds retain the updater controls for development. Frozen portable
    # archives deliberately do not self-update because Inno installs to a
    # separate managed tree; only the Inno-owned distribution may auto-update.
    if not is_frozen() or is_installed_distribution():
        install_update_runtime(app, window)
        install_application_updater(window, access_controller=access_controller)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
