from __future__ import annotations

from PySide6.QtCore import Property, QEasingCurve, QRectF, Qt, QPropertyAnimation
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QMainWindow, QVBoxLayout, QWidget


class WorkspaceModeToggle(QAbstractButton):
    """One compact SINGLE/BATCH switch with a lightweight sliding thumb."""

    _ANIMATION_MS = 180

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("workspaceModeToggle")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedSize(106, 34)
        self.setToolTip("切换 Single / Batch 工作区")
        self.setAccessibleName("Single / Batch mode")

        self._thumb_position = 0.0
        self._animation = QPropertyAnimation(self, b"thumbPosition", self)
        self._animation.setDuration(self._ANIMATION_MS)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate_to_state)

    def _get_thumb_position(self) -> float:
        return self._thumb_position

    def _set_thumb_position(self, value: float) -> None:
        value = max(0.0, min(1.0, float(value)))
        if abs(value - self._thumb_position) < 0.0001:
            return
        self._thumb_position = value
        self.update()

    thumbPosition = Property(float, _get_thumb_position, _set_thumb_position)

    def _animate_to_state(self, checked: bool) -> None:
        self._animation.stop()
        self._animation.setStartValue(self._thumb_position)
        self._animation.setEndValue(1.0 if checked else 0.0)
        self._animation.start()

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        hovered = self.underMouse()
        if self.isChecked():
            track = QColor(104, 72, 105, 176 if hovered else 152)
            border = QColor(255, 236, 247, 62 if hovered else 42)
        else:
            track = QColor(24, 34, 47, 162 if hovered else 138)
            border = QColor(255, 255, 255, 54 if hovered else 34)

        painter.setPen(QPen(border, 1.0))
        painter.setBrush(track)
        painter.drawRoundedRect(rect, rect.height() / 2.0, rect.height() / 2.0)

        margin = 4.0
        diameter = rect.height() - margin * 2.0
        travel = rect.width() - margin * 2.0 - diameter
        thumb_x = rect.left() + margin + travel * self._thumb_position
        thumb_rect = QRectF(thumb_x, rect.top() + margin, diameter, diameter)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(249, 250, 252, 246))
        painter.drawEllipse(thumb_rect)

        painter.setPen(QColor(255, 255, 255, 226))
        font = painter.font()
        font.setPointSizeF(8.2)
        font.setWeight(650)
        painter.setFont(font)

        if self._thumb_position < 0.5:
            text_rect = QRectF(
                thumb_rect.right() + 2.0,
                rect.top(),
                rect.right() - thumb_rect.right() - 5.0,
                rect.height(),
            )
            label = "SINGLE"
        else:
            text_rect = QRectF(
                rect.left() + 5.0,
                rect.top(),
                thumb_rect.left() - rect.left() - 7.0,
                rect.height(),
            )
            label = "BATCH"
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, label)
        painter.end()

    def enterEvent(self, event) -> None:  # type: ignore[override]
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        self.update()
        super().leaveEvent(event)


def install_compact_mode_toggle(window: QMainWindow) -> WorkspaceModeToggle:
    """Hide the full-width legacy switch row and mount one toggle in the header."""

    existing = getattr(window, "_compact_mode_toggle", None)
    if isinstance(existing, WorkspaceModeToggle):
        return existing

    mode_stack = getattr(window, "mode_stack", None)
    single_button = getattr(window, "single_mode_button", None)
    batch_button = getattr(window, "batch_mode_button", None)
    set_mode = getattr(window, "_set_workspace_mode", None)
    if mode_stack is None or single_button is None or batch_button is None or not callable(set_mode):
        raise RuntimeError("compact mode toggle requires installed Single/Batch workspace")

    legacy_card = single_button.parentWidget()
    if legacy_card is not None:
        # Remove it from both layout footprint and later glass-card discovery.
        legacy_card.setObjectName("")
        legacy_card.hide()

    root = window.centralWidget()
    outer = root.layout() if root is not None else None
    if root is None or not isinstance(outer, QVBoxLayout) or outer.count() < 1:
        raise RuntimeError("compact mode toggle expected the preserved root layout")

    header_item = outer.itemAt(0)
    header = header_item.layout() if header_item is not None else None
    if not isinstance(header, QHBoxLayout):
        raise RuntimeError("compact mode toggle expected the common header row")

    toggle = WorkspaceModeToggle(root)
    toggle.setChecked(int(mode_stack.currentIndex()) == 1)
    toggle.clicked.connect(lambda checked: set_mode(1 if checked else 0))

    def sync_from_stack(index: int) -> None:
        target = int(index) == 1
        if toggle.isChecked() != target:
            toggle.setChecked(target)

    mode_stack.currentChanged.connect(sync_from_stack)
    header.addWidget(toggle, 0, Qt.AlignmentFlag.AlignBottom)
    window._compact_mode_toggle = toggle  # type: ignore[attr-defined]
    return toggle
