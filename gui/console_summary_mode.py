from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtWidgets import QFrame, QMainWindow, QPushButton, QScrollArea, QSplitter, QTabWidget, QWidget


_SUMMARY_MIN = 300
_SUMMARY_MAX = 460
_PAGE_SUMMARY_MIN = 420
_PAGE_SUMMARY_MAX = 560
_COALESCE_MS = 40


class ConsoleSummaryMode(QObject):
    """Keep the rich console summary permanent and open detail as a modal.

    Legacy layouts may still own the console through ``bodySplitter``. The formal
    Single UI now uses one outer page scroll instead; in that mode the console owns
    its natural reading height and this controller never tries to resize a hidden
    splitter.
    """

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        self.root = window.centralWidget()
        self.console = getattr(window, "console", None)
        self.toggle = getattr(window, "console_detail_toggle", None)
        self.body = getattr(window, "_ui_polish_body_splitter", None)
        self.page_scroll = getattr(window, "_single_page_scroll", None)
        self.details = getattr(window, "_card_details", None)
        self._mature_apply = None

        if not isinstance(self.root, QWidget):
            raise RuntimeError("console summary mode requires a central widget")
        if not isinstance(self.console, QFrame):
            raise RuntimeError("console summary mode requires the acceptance console")
        if not isinstance(self.toggle, QPushButton):
            raise RuntimeError("console summary mode requires console_detail_toggle")
        if not isinstance(self.body, QSplitter) and not isinstance(self.page_scroll, QScrollArea):
            raise RuntimeError("console summary mode requires bodySplitter or singlePageScroll")
        if self.details is None or not hasattr(self.details, "open_console_details"):
            raise RuntimeError("console summary mode requires the shared glass detail controller")

        # Remove ui_polish/ui_maturity's old expand/collapse state machine. Keep
        # the logical checked bit true; the action now opens the shared detail
        # modal and never resizes the visible workspace.
        try:
            self.toggle.toggled.disconnect()
        except (RuntimeError, TypeError):
            pass
        try:
            self.toggle.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.toggle.setCheckable(True)
        self.toggle.setChecked(True)
        self.toggle.setEnabled(True)
        self.toggle.show()
        self.toggle.setText("展开详情")
        self.toggle.clicked.connect(self._open_detail)

        for unit in getattr(self.console, "phase_units", {}).values():
            if isinstance(unit, QWidget):
                unit.show()
        tabs = getattr(self.console, "tabs", None)
        if isinstance(tabs, QTabWidget):
            tabs.show()

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

    @staticmethod
    def _summary_target(available: int) -> int:
        target = min(440, max(340, available - 300))
        return min(target, max(_SUMMARY_MIN, available - 260))

    def apply(self) -> None:
        if isinstance(self.page_scroll, QScrollArea):
            self.console.setMinimumHeight(_PAGE_SUMMARY_MIN)
            self.console.setMaximumHeight(_PAGE_SUMMARY_MAX)
            return

        if not isinstance(self.body, QSplitter):
            return
        available = max(1, self.body.height() - self.body.handleWidth())
        self.console.setMinimumHeight(_SUMMARY_MIN)
        self.console.setMaximumHeight(_SUMMARY_MAX)
        target = self._summary_target(available)
        self._set_sizes_if_needed(
            self.body,
            [max(260, available - target), target],
        )

    def _apply_after_mature(self) -> None:
        if callable(self._mature_apply):
            self._mature_apply()
        self.apply()

    def _open_detail(self, *_args: object) -> None:
        if not self.toggle.isChecked():
            self.toggle.setChecked(True)
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
