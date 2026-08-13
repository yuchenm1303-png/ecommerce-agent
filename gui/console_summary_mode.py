from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QWidget,
)


_SUMMARY_MIN = 218
_SUMMARY_MAX = 242
_SUMMARY_TARGET = 230
_WORKSPACE_MIN = 260
_CONSOLE_TABS_MIN = 88
_CONSOLE_TABS_MAX = 112
_SIDE_MIN = 360
_SIDE_MAX = 480
_SIDE_RATIO = 0.29
_COALESCE_MS = 40


class ConsoleSummaryMode(QObject):
    """Keep a compact but complete console on the fixed Single page.

    Phase summary and a shallow live tab viewport remain visible. The existing
    detail modal still provides the full-height console when deeper inspection is
    needed, without resizing the main Single workspace.
    """

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        self.root = window.centralWidget()
        self.console = getattr(window, "console", None)
        self.toggle = getattr(window, "console_detail_toggle", None)
        self.body = getattr(window, "_ui_polish_body_splitter", None)
        self.details = getattr(window, "_card_details", None)
        self._mature_apply = None

        if not isinstance(self.root, QWidget):
            raise RuntimeError("console summary mode requires a central widget")
        if not isinstance(self.console, QFrame):
            raise RuntimeError("console summary mode requires the acceptance console")
        if not isinstance(self.toggle, QPushButton):
            raise RuntimeError("console summary mode requires console_detail_toggle")
        if not isinstance(self.body, QSplitter):
            raise RuntimeError("console summary mode requires the fixed bodySplitter")
        if self.details is None or not hasattr(self.details, "open_console_details"):
            raise RuntimeError("console summary mode requires the shared glass detail controller")

        # The old button changed splitter height. In the fixed Single layout it is
        # a plain action: detailed diagnostics open in the existing modal and the
        # main workspace never reflows.
        try:
            self.toggle.toggled.disconnect()
        except (RuntimeError, TypeError):
            pass
        try:
            self.toggle.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.toggle.setCheckable(False)
        self.toggle.setEnabled(True)
        self.toggle.show()
        self.toggle.setText("展开详情")
        self.toggle.clicked.connect(self._open_detail)

        for unit in getattr(self.console, "phase_units", {}).values():
            if isinstance(unit, QWidget):
                unit.show()

        # Keep the lower console surface visible instead of hiding it. Its shallow
        # viewport is enough for live context; the modal remains the full reader.
        tabs = getattr(self.console, "tabs", None)
        if isinstance(tabs, QTabWidget):
            tabs.show()
            tabs.setMinimumHeight(_CONSOLE_TABS_MIN)
            tabs.setMaximumHeight(_CONSOLE_TABS_MAX)
            tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        mature = getattr(window, "_mature_ui", None)
        timer = getattr(mature, "_timer", None)
        apply = getattr(mature, "apply", None)
        if isinstance(timer, QTimer) and callable(apply):
            self._mature_apply = apply
            try:
                timer.timeout.disconnect()
            except (RuntimeError, TypeError):
                pass
            timer.timeout.connect(self._apply_after_mature)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_COALESCE_MS)
        self._timer.timeout.connect(self.apply)
        self.root.installEventFilter(self)
        window.destroyed.connect(self.cleanup)

        QTimer.singleShot(0, self.apply)

    @staticmethod
    def _set_sizes_if_needed(splitter: QSplitter, target: list[int]) -> None:
        current = splitter.sizes()
        if len(current) != len(target) or any(abs(a - b) > 3 for a, b in zip(current, target)):
            splitter.setSizes(target)

    def _apply_workspace_width(self) -> None:
        splitter = self.root.findChild(QSplitter, "workspaceSplitter")
        if not isinstance(splitter, QSplitter) or splitter.count() < 2:
            return
        total = max(1, splitter.width() - splitter.handleWidth())
        side_target = min(
            _SIDE_MAX,
            max(_SIDE_MIN, round(total * _SIDE_RATIO)),
        )
        side = splitter.widget(1)
        if isinstance(side, QWidget):
            side.setMinimumWidth(_SIDE_MIN)
            side.setMaximumWidth(_SIDE_MAX)
        self._set_sizes_if_needed(
            splitter,
            [max(620, total - side_target), side_target],
        )

    def apply(self) -> None:
        if not isinstance(self.body, QSplitter):
            return

        self._apply_workspace_width()

        available = max(1, self.body.height() - self.body.handleWidth())
        target = min(_SUMMARY_MAX, max(_SUMMARY_MIN, _SUMMARY_TARGET))
        self.console.setMinimumHeight(_SUMMARY_MIN)
        self.console.setMaximumHeight(_SUMMARY_MAX)

        tabs = getattr(self.console, "tabs", None)
        if isinstance(tabs, QTabWidget):
            tabs.show()
            tabs.setMinimumHeight(_CONSOLE_TABS_MIN)
            tabs.setMaximumHeight(_CONSOLE_TABS_MAX)

        self._set_sizes_if_needed(
            self.body,
            [max(_WORKSPACE_MIN, available - target), target],
        )

    def _apply_after_mature(self) -> None:
        if callable(self._mature_apply):
            self._mature_apply()
        # ui_maturity still carries the old 330 px side-width and collapsed
        # console heuristics. Re-assert the fixed Single budgets after it runs.
        self.apply()

    def _open_detail(self, *_args: object) -> None:
        self.toggle.setText("展开详情")
        self.details.open_console_details()

    def schedule(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self.root and event.type() in {
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.LayoutRequest,
        }:
            self.schedule()
        return False

    def cleanup(self) -> None:
        self._timer.stop()
        try:
            self.root.removeEventFilter(self)
        except RuntimeError:
            pass


def install_console_summary_mode(window: QMainWindow) -> ConsoleSummaryMode:
    controller = ConsoleSummaryMode(window)
    window._console_summary_mode = controller  # type: ignore[attr-defined]
    return controller
