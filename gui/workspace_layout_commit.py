from __future__ import annotations

from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QLayout, QMainWindow, QStackedWidget, QWidget

from .page_scroll_layout import refresh_single_source_layout


_MAX_LAYOUT_PASSES = 3


def _activate_layout_tree(widget: QWidget) -> bool:
    """Synchronously settle one QWidget subtree during startup only.

    The operation deliberately does not pump application events. It is used while
    the startup overlay owns the screen, never as part of Single/Batch mode
    switching. Once both workspaces are live, their geometry is left persistent
    and QStackedWidget only changes which page is visible.
    """

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
    """Startup-only geometry primer for the initially visible workspace.

    Single and Batch are persistent sibling pages. A mode change must never
    invalidate or reactivate their layout trees because doing so exposes a
    transient reflow to the native glass layer. This helper therefore has no
    ``currentChanged`` connection; the startup entrance calls it explicitly while
    its opaque overlay still covers the live QWidget tree.
    """

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        self.stack = getattr(window, "mode_stack", None)
        if not isinstance(self.stack, QStackedWidget):
            raise RuntimeError("workspace layout commit requires installed modeStack")
        self._committing = False

    def _commit_batch_responsive(self) -> None:
        workspace = getattr(self.window, "batch_workspace", None)
        responsive = getattr(workspace, "_batch_card_responsive", None)
        commit = getattr(responsive, "commit_now", None)
        if callable(commit):
            commit()

    def commit_current(self, _index: int | None = None) -> None:
        if self._committing:
            return
        page = self.stack.currentWidget()
        if not isinstance(page, QWidget):
            return

        self._committing = True
        try:
            stack_layout = self.stack.layout()
            if isinstance(stack_layout, QLayout):
                stack_layout.invalidate()
                stack_layout.activate()

            if int(self.stack.currentIndex()) == 0:
                refresh_single_source_layout(self.window)
            else:
                self._commit_batch_responsive()

            for _pass in range(_MAX_LAYOUT_PASSES):
                changed = _activate_layout_tree(page)
                if int(self.stack.currentIndex()) == 1:
                    self._commit_batch_responsive()
                if not changed:
                    break
        finally:
            self._committing = False


def install_workspace_layout_commit(window: QMainWindow) -> WorkspaceLayoutCommitter:
    existing = getattr(window, "_workspace_layout_commit", None)
    if isinstance(existing, WorkspaceLayoutCommitter):
        return existing
    controller = WorkspaceLayoutCommitter(window)
    window._workspace_layout_commit = controller  # type: ignore[attr-defined]
    return controller


__all__ = ["WorkspaceLayoutCommitter", "install_workspace_layout_commit"]
