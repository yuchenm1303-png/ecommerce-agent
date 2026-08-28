from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QLayout, QMainWindow, QStackedWidget, QWidget

from .page_scroll_layout import refresh_single_source_layout


_MAX_LAYOUT_PASSES = 3


def _activate_layout_tree(widget: QWidget) -> bool:
    """Settle one persistent page subtree without pumping the Qt event loop."""

    changed = False
    try:
        widget.ensurePolished()
        layout = widget.layout()
        if isinstance(layout, QLayout):
            layout.invalidate()
            changed = bool(layout.activate()) or changed
    except RuntimeError:
        return changed

    try:
        children = widget.findChildren(
            QWidget,
            "",
            Qt.FindChildOption.FindDirectChildrenOnly,
        )
    except (RuntimeError, TypeError):
        return changed

    for child in children:
        changed = _activate_layout_tree(child) or changed
    return changed


class WorkspaceLayoutCommitter(QObject):
    """Own geometry for both persistent Single/Batch pages outside mode switches.

    QStackedWidget keeps the inactive page hidden. Hidden subtrees can otherwise
    retain stale child geometry until their next Show/LayoutRequest cycle, exposing
    a provisional layout for one frame when the mode changes. This controller
    commits both pages only at legitimate global geometry boundaries: installation,
    stack Show, and stack Resize. It deliberately has no currentChanged/page-Show
    hook, so clicking the mode switch performs zero layout activation.
    """

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        self.stack = getattr(window, "mode_stack", None)
        if not isinstance(self.stack, QStackedWidget):
            raise RuntimeError("workspace layout owner requires installed modeStack")
        self._committing = False
        self.stack.installEventFilter(self)
        window.destroyed.connect(self.cleanup)
        self.prime_all()

    def _commit_batch_responsive(self) -> None:
        workspace = getattr(self.window, "batch_workspace", None)
        responsive = getattr(workspace, "_batch_card_responsive", None)
        commit = getattr(responsive, "commit_now", None)
        if callable(commit):
            try:
                commit()
            except RuntimeError:
                pass

    def _commit_page(self, index: int) -> None:
        page = self.stack.widget(int(index))
        if not isinstance(page, QWidget):
            return

        target_rect = self.stack.contentsRect()
        if target_rect.width() > 0 and target_rect.height() > 0 and page.geometry() != target_rect:
            page.setGeometry(target_rect)

        if int(index) == 0:
            refresh_single_source_layout(self.window)

        for _pass in range(_MAX_LAYOUT_PASSES):
            changed = _activate_layout_tree(page)
            if int(index) == 1:
                self._commit_batch_responsive()
                changed = _activate_layout_tree(page) or changed
            if not changed:
                break

    def prepare_page(self, index: int) -> None:
        """Explicit precommit hook for startup/global resize code, never transition code."""

        if self._committing:
            return
        if int(index) < 0 or int(index) >= self.stack.count():
            return
        self._committing = True
        try:
            self._commit_page(int(index))
        finally:
            self._committing = False

    def prime_all(self) -> None:
        if self._committing:
            return
        self._committing = True
        try:
            for index in range(self.stack.count()):
                self._commit_page(index)
        finally:
            self._committing = False

    def commit_current(self, _index: int | None = None) -> None:
        self.prepare_page(int(self.stack.currentIndex()))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self.stack and event.type() in {
            QEvent.Type.Resize,
            QEvent.Type.Show,
        }:
            self.prime_all()
        return False

    def cleanup(self) -> None:
        try:
            self.stack.removeEventFilter(self)
        except RuntimeError:
            pass


def install_workspace_layout_commit(window: QMainWindow) -> WorkspaceLayoutCommitter:
    existing = getattr(window, "_workspace_layout_commit", None)
    if isinstance(existing, WorkspaceLayoutCommitter):
        return existing
    controller = WorkspaceLayoutCommitter(window)
    window._workspace_layout_commit = controller  # type: ignore[attr-defined]
    return controller


__all__ = ["WorkspaceLayoutCommitter", "install_workspace_layout_commit"]
