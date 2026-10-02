"""Boot the real Listing Studio GUI offscreen and verify the composed page layout.

Side effects are disabled for the probe process only: no window reaches the
desktop, the managed Makro Edge never launches, the Sakana helper process never
starts and no update check leaves the machine (source builds never contact the
Stable channel; the probe additionally sets ECOMMERCE_AGENT_DISABLE_UPDATE_CHECK).
Win32 child embedding is replaced by its Qt-level equivalent because offscreen
windows have no native HWNDs.

The probe walks Single and Batch, records the geometry of every business control
that must stay reachable, and reports layout problems as JSON:

    python tools/gui_layout_probe.py --json
    python tools/gui_layout_probe.py --shots artifacts/layout-shots

Exit status is 0 when no problem was found.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Qt splits platform options on ':' so the screen config must be a relative path.
os.chdir(ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen:configfile=tools/offscreen_1080p.json")
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")
os.environ.setdefault("QSG_RENDER_LOOP", "basic")
# Source builds never contact the Stable channel; keep the header controls but
# make the "no update traffic" guarantee explicit for this probe process.
os.environ["ECOMMERCE_AGENT_DISABLE_UPDATE_CHECK"] = "1"

import gui.browser_session_manager as browser_module  # noqa: E402
import gui.native_window_shell as shell_module  # noqa: E402
import gui.sakana_toy as sakana_module  # noqa: E402
import gui.update_runtime as update_runtime_module  # noqa: E402
import run_local_gui  # noqa: E402


def _fit_native_child_qt(self) -> None:
    if self._fit_timer.isActive():
        self._fit_timer.stop()
    if self._closing or not self._embedded:
        return
    self.overlay.setGeometry(0, 0, self.owner.width(), self.owner.height())
    self._last_fitted_owner_size = self._owner_size()


def _disable_side_effects() -> None:
    browser_module.ManagedMakroBrowser.ensure_async = lambda self, *a, **k: None
    browser_module.ManagedMakroBrowser._poll = lambda self, *a, **k: None
    sakana_module.SakanaToyController._start_process = lambda self, *a, **k: None
    update_runtime_module.install_update_runtime = lambda *a, **k: None
    shell_module._embed_native_child = lambda *a, **k: None
    shell_module._fit_child_to_owner_client = lambda *a, **k: None
    shell_module._set_native_child_presented = lambda *a, **k: None
    shell_module._focus_native_child = lambda *a, **k: False
    shell_module.NativeWindowShell._fit_native_child = _fit_native_child_qt


SINGLE_CONTROLS = (
    "url_input",
    "product_pack_button",
    "_listing_photo_ownership.single_photo_button",
    "listing_intent_input",
    "listing_intent_detail_button",
    "ai_guidance_input",
    "model_name_keywords_input",
    "vertical_input",
    "start_button",
    "stop_button",
    "_cooperative_pause.pause_button",
    "real_settings_toggle",
    "write_value",
    "save_value",
    "qc_value",
    "source_port",
    "current_page_check",
    "step1_button",
    "step2_button",
    "step3_button",
    "console_detail_toggle",
    "ready_card",
    "missing_card",
    "conflict_card",
    "blocked_card",
    "field_table",
    "side_detail_tabs",
    "console.tabs",
    "phase_badge",
    "_workspace_mode_switch",
    "_ai_settings_controller.button",
    "_channel_account_center.button",
    "_application_updater._version_label",
    "_application_updater._check_button",
)

BATCH_CONTROLS = (
    "batch_workspace.url_input",
    "batch_workspace.url_input.paste_button",
    "batch_workspace.url_input.add_button",
    "batch_workspace.source_port",
    "batch_workspace.worker_count",
    "batch_workspace.clear_button",
    "batch_workspace.prepare_button",
    "batch_workspace.save_check",
    "batch_workspace.images_check",
    "batch_workspace.qc_check",
    "batch_workspace.execute_button",
    "batch_workspace.stop_button",
    "batch_workspace.open_batch_button",
    "batch_workspace.account_context_name",
    "phase_badge",
    "_workspace_mode_switch",
)


def _resolve(window: Any, path: str) -> Any:
    value: Any = window
    for name in path.split("."):
        value = getattr(value, name, None)
        if value is None:
            return None
    return value


def _rect(widget: Any, window: Any) -> list[int]:
    from PySide6.QtCore import QPoint

    origin = widget.mapTo(window, QPoint(0, 0))
    return [int(origin.x()), int(origin.y()), int(widget.width()), int(widget.height())]


def _inside(rect: list[int], bounds: list[int]) -> bool:
    x, y, w, h = rect
    bx, by, bw, bh = bounds
    return x >= bx and y >= by and x + w <= bx + bw and y + h <= by + bh


def _overlap(a: list[int], b: list[int]) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def _layout_widgets(layout: Any) -> list[Any]:
    found: list[Any] = []
    if layout is None:
        return found
    for index in range(layout.count()):
        item = layout.itemAt(index)
        if item.widget() is not None:
            found.append(item.widget())
        if item.layout() is not None:
            found.extend(_layout_widgets(item.layout()))
    return found


def _card_content_problems(window: Any, mode: str, frame: Any, card_rect: list[int]) -> list[str]:
    """Every laid-out child must sit inside its card and never overlap a sibling."""

    problems: list[str] = []
    members = [
        widget
        for widget in _layout_widgets(frame.layout())
        if widget.isVisible() and widget.width() > 0 and widget.height() > 0
    ]
    rects = [(widget, _rect(widget, window)) for widget in members]
    label = frame.objectName()
    for index, (widget_a, rect_a) in enumerate(rects):
        name_a = f"{type(widget_a).__name__}#{widget_a.objectName() or '-'}"
        if not _inside(rect_a, card_rect):
            problems.append(f"{mode}: {name_a} {rect_a} spills out of {label} {card_rect}")
        for widget_b, rect_b in rects[index + 1 :]:
            if widget_a.isAncestorOf(widget_b) or widget_b.isAncestorOf(widget_a):
                continue
            if _overlap(rect_a, rect_b):
                name_b = f"{type(widget_b).__name__}#{widget_b.objectName() or '-'}"
                problems.append(f"{mode}: {name_a} {rect_a} overlaps {name_b} {rect_b} in {label}")
    return problems


def _measure(window: Any, mode: str, controls: tuple[str, ...]) -> dict[str, Any]:
    from PySide6.QtWidgets import QFrame, QWidget

    bounds = [0, 0, int(window.width()), int(window.height())]
    problems: list[str] = []
    geometry: dict[str, list[int]] = {}
    for path in controls:
        widget = _resolve(window, path)
        if not isinstance(widget, QWidget):
            problems.append(f"{mode}: {path} is missing")
            continue
        if not widget.isVisible():
            problems.append(f"{mode}: {path} is not visible")
            continue
        rect = _rect(widget, window)
        geometry[path] = rect
        if rect[2] <= 0 or rect[3] <= 0:
            problems.append(f"{mode}: {path} has empty geometry {rect}")
        elif not _inside(rect, bounds):
            problems.append(f"{mode}: {path} {rect} leaves the window {bounds}")

    glass = getattr(getattr(window, "_visual_style", None), "_glass", {})
    frames = [
        frame
        for frame in glass
        if isinstance(frame, QFrame) and frame.isVisible() and frame.width() > 0
    ]
    cards = [(frame.objectName(), _rect(frame, window)) for frame in frames]
    for frame, (_name, rect) in zip(frames, cards):
        problems.extend(_card_content_problems(window, mode, frame, rect))
    for index, (name_a, rect_a) in enumerate(cards):
        if not _inside(rect_a, bounds):
            problems.append(f"{mode}: glass card {name_a} {rect_a} leaves the window")
        for name_b, rect_b in cards[index + 1 :]:
            if _overlap(rect_a, rect_b):
                problems.append(f"{mode}: glass cards {name_a} {rect_a} and {name_b} {rect_b} overlap")
    return {"mode": mode, "controls": geometry, "cards": [rect for _, rect in cards], "problems": problems}


class _Probe:
    def __init__(self, shots: Path | None) -> None:
        self.shots = shots
        self.report: dict[str, Any] = {"pages": []}

    def window(self) -> Any:
        from PySide6.QtWidgets import QApplication

        from gui.product_input_window import ProductInputWorkflowMainWindow

        return next(
            w for w in QApplication.topLevelWidgets() if isinstance(w, ProductInputWorkflowMainWindow)
        )

    def _shot(self, name: str) -> None:
        if self.shots is None:
            return
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQuick import QQuickWindow

        self.shots.mkdir(parents=True, exist_ok=True)
        for win in QGuiApplication.topLevelWindows():
            if isinstance(win, QQuickWindow) and win.isVisible():
                win.grabWindow().save(str(self.shots / f"{name}.png"))
                return

    def single(self) -> None:
        window = self.window()
        self.report["pages"].append(_measure(window, "single", SINGLE_CONTROLS))
        self._shot("single")
        window._workspace_mode_switch.click()

    def batch(self) -> None:
        window = self.window()
        self.report["pages"].append(_measure(window, "batch", BATCH_CONTROLS))
        self._shot("batch")

    def finish(self) -> None:
        from PySide6.QtWidgets import QApplication

        problems = [p for page in self.report["pages"] for p in page["problems"]]
        self.report["problems"] = problems
        QApplication.instance().exit(1 if problems else 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print the full JSON report")
    parser.add_argument("--shots", type=Path, default=None, help="save Quick screenshots here")
    args = parser.parse_args()

    _disable_side_effects()
    probe = _Probe(args.shots)
    original_stage = run_local_gui.mark_startup_stage

    def stage(name: str) -> None:
        original_stage(name)
        if name != "running":
            return
        from PySide6.QtCore import QTimer

        QTimer.singleShot(3000, probe.single)
        QTimer.singleShot(6000, probe.batch)
        QTimer.singleShot(6200, probe.finish)

    run_local_gui.mark_startup_stage = stage
    sys.argv = sys.argv[:1]
    code = run_local_gui.main()
    report = probe.report
    if args.json:
        print("GUI_LAYOUT_PROBE " + json.dumps(report, ensure_ascii=False))
    else:
        for problem in report.get("problems", []):
            print(problem)
    return int(code)


if __name__ == "__main__":
    raise SystemExit(main())
