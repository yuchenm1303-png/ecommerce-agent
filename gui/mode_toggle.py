from __future__ import annotations

from PySide6.QtCore import (
    QObject,
    Property,
    QEasingCurve,
    QPointF,
    QRectF,
    Qt,
    QPropertyAnimation,
    QTimer,
    QVariantAnimation,
)
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QAbstractButton,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QVBoxLayout,
    QWidget,
)

from .settings_modal_surface import install_ai_settings_modal
from .workspace_layout_commit import install_workspace_layout_commit


_CORE_WIDTH = 40.0
_CORE_HEIGHT = 20.0
_ACTION_SIZE = 16.0
_ACTION_LEFT_OFF = 1.0
_ACTION_LEFT_ON = 23.0
_TRANSITION_MS = 300
_HEADER_REFLOW_MS = 260
_TRACK = QColor(255, 255, 255, 0x30)
_WHITE = QColor(255, 255, 255, 255)


def _original_switch_easing() -> QEasingCurve:
    curve = QEasingCurve(QEasingCurve.Type.BezierSpline)
    curve.addCubicBezierSegment(
        QPointF(0.645, 0.045),
        QPointF(0.355, 1.0),
        QPointF(1.0, 1.0),
    )
    return curve


def _header_reflow_easing() -> QEasingCurve:
    """Smooth deceleration without an overshoot or a hard end-stop."""

    return QEasingCurve(QEasingCurve.Type.OutCubic)


class WorkspaceModeSwitch(QAbstractButton):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("workspaceModeSwitch")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedSize(int(_CORE_WIDTH), 32)
        self.setAccessibleName("Single / Batch mode")
        self._action_position = 0.0
        self._animation = QPropertyAnimation(self, b"actionPosition", self)
        self._animation.setDuration(_TRANSITION_MS)
        self._animation.setEasingCurve(_original_switch_easing())
        self.toggled.connect(self._animate_to_state)
        self.toggled.connect(self._sync_tooltip)
        self._sync_tooltip(False)

    def _get_action_position(self) -> float:
        return self._action_position

    def _set_action_position(self, value: float) -> None:
        value = max(0.0, min(1.0, float(value)))
        if abs(value - self._action_position) < 0.0001:
            return
        self._action_position = value
        self.update()

    actionPosition = Property(float, _get_action_position, _set_action_position)

    def set_checked_immediate(self, checked: bool) -> None:
        self._animation.stop()
        blocked = self.blockSignals(True)
        try:
            self.setChecked(bool(checked))
        finally:
            self.blockSignals(blocked)
        self._action_position = 1.0 if checked else 0.0
        self._sync_tooltip(bool(checked))
        self.update()

    def _animate_to_state(self, checked: bool) -> None:
        self._animation.stop()
        self._animation.setStartValue(self._action_position)
        self._animation.setEndValue(1.0 if checked else 0.0)
        self._animation.start()

    def _sync_tooltip(self, checked: bool) -> None:
        self.setToolTip("Batch 模式 · 点击切换 Single" if checked else "Single 模式 · 点击切换 Batch")

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        core = QRectF(0.0, (self.height() - _CORE_HEIGHT) / 2.0, _CORE_WIDTH, _CORE_HEIGHT)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_TRACK)
        painter.drawRoundedRect(core, 10.0, 10.0)
        icon_font = QFont(self.font())
        icon_font.setPixelSize(11)
        icon_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(icon_font)
        painter.setPen(_WHITE)
        on_rect = QRectF(core.left() + 2.0, core.top(), 18.0, core.height())
        off_rect = QRectF(core.left() + 20.0, core.top(), 18.0, core.height())
        if self._action_position > 0.001:
            painter.setOpacity(self._action_position)
            painter.drawText(on_rect, Qt.AlignmentFlag.AlignCenter, "✓")
        if self._action_position < 0.999:
            painter.setOpacity(1.0 - self._action_position)
            painter.drawText(off_rect, Qt.AlignmentFlag.AlignCenter, "×")
        painter.setOpacity(1.0)
        action_left = _ACTION_LEFT_OFF + (_ACTION_LEFT_ON - _ACTION_LEFT_OFF) * self._action_position
        action = QRectF(core.left() + action_left, core.top() + 2.0, _ACTION_SIZE, _ACTION_SIZE)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_WHITE)
        painter.drawEllipse(action)
        painter.end()


class _HeaderModeReflowAnimator(QObject):
    """Smooth the header layout delta created by SINGLE/BATCH badge text.

    The old implementation changed minimumWidth and maximumWidth separately on every
    animation tick, then restored both original constraints when the animation ended.
    That generated two layout invalidations per frame and a final extra reflow, which
    is exactly the visible stutter/snap at the endpoint.

    This controller uses one fixed-width commit per animation frame. At the endpoint
    it keeps the measured target as the badge's minimum width and only releases the
    upper bound. The live geometry therefore remains identical across the handoff,
    while later, genuinely longer runtime status text can still expand naturally.
    """

    def __init__(self, window: QMainWindow, mode_stack: QWidget, badge: QLabel) -> None:
        super().__init__(window)
        self.window = window
        self.mode_stack = mode_stack
        self.badge = badge
        self._base_min_width = int(badge.minimumWidth())
        self._base_max_width = int(badge.maximumWidth())
        self._start_width = max(1, int(badge.width()))
        self._target_width = self._start_width
        self._applied_width = self._start_width
        self._generation = 0
        self._width_cache: dict[str, int] = {}

        self._animation = QVariantAnimation(self)
        self._animation.setDuration(_HEADER_REFLOW_MS)
        self._animation.setEasingCurve(_header_reflow_easing())
        self._animation.valueChanged.connect(self._apply_width)
        self._animation.finished.connect(self._finish)

        current_changed = getattr(mode_stack, "currentChanged", None)
        if current_changed is None or not hasattr(current_changed, "connect"):
            raise RuntimeError("header reflow animator requires modeStack.currentChanged")
        current_changed.connect(self._mode_changed)

    def _mode_changed(self, _index: int) -> None:
        """Freeze the exact current width before phase_badge.setText() runs."""

        try:
            self._animation.stop()
            self._start_width = max(1, int(self.badge.width()))
            self._target_width = self._start_width
            self._applied_width = self._start_width
            self.badge.setFixedWidth(self._start_width)
        except RuntimeError:
            return

        self._generation += 1
        generation = self._generation
        QTimer.singleShot(0, lambda: self._start_after_text_update(generation))

    def _measure_target_width(self) -> int:
        """Measure the final styled label without unlocking the live header."""

        try:
            text = str(self.badge.text())
        except RuntimeError:
            return self._start_width

        cached = self._width_cache.get(text)
        if cached is not None:
            return cached

        try:
            parent = self.badge.parentWidget()
            probe = QLabel(text, parent)
            probe.setObjectName(self.badge.objectName())
            probe.setFont(self.badge.font())
            probe.ensurePolished()
            target = max(1, int(probe.sizeHint().width()))
            probe.deleteLater()
        except RuntimeError:
            target = self._start_width

        self._width_cache[text] = target
        return target

    def _start_after_text_update(self, generation: int) -> None:
        if generation != self._generation:
            return
        try:
            self._target_width = self._measure_target_width()
        except RuntimeError:
            return

        if abs(self._target_width - self._start_width) <= 1:
            self._finish()
            return

        self._animation.setStartValue(self._start_width)
        self._animation.setEndValue(self._target_width)
        self._animation.start()

    def _apply_width(self, value: object) -> None:
        try:
            width = max(1, int(round(float(value))))
        except (TypeError, ValueError):
            return
        if width == self._applied_width:
            return
        self._applied_width = width
        try:
            # setFixedWidth updates both bounds as one QWidget operation, avoiding
            # two separate layout invalidations for every animation frame.
            self.badge.setFixedWidth(width)
        except RuntimeError:
            pass

    def _finish(self) -> None:
        try:
            target = max(self._base_min_width, int(self._target_width))
            if self._applied_width != target:
                self.badge.setFixedWidth(target)
                self._applied_width = target

            # Preserve the exact landing width as the floor. Releasing only the
            # upper bound does not alter the current geometry, so there is no
            # endpoint snap. Longer runtime text remains free to grow later.
            self.badge.setMinimumWidth(target)
            self.badge.setMaximumWidth(self._base_max_width)
        except RuntimeError:
            pass


def _install_header_mode_reflow(
    window: QMainWindow,
    mode_stack: QWidget,
) -> _HeaderModeReflowAnimator | None:
    existing = getattr(window, "_header_mode_reflow_animator", None)
    if isinstance(existing, _HeaderModeReflowAnimator):
        return existing

    badge = getattr(window, "phase_badge", None)
    if not isinstance(badge, QLabel):
        return None
    animator = _HeaderModeReflowAnimator(window, mode_stack, badge)
    window._header_mode_reflow_animator = animator  # type: ignore[attr-defined]
    return animator


def install_workspace_mode_switch(window: QMainWindow) -> WorkspaceModeSwitch:
    existing = getattr(window, "_workspace_mode_switch", None)
    if isinstance(existing, WorkspaceModeSwitch):
        install_ai_settings_modal(window)
        return existing

    mode_stack = getattr(window, "mode_stack", None)
    single_button = getattr(window, "single_mode_button", None)
    batch_button = getattr(window, "batch_mode_button", None)
    set_mode = getattr(window, "_set_workspace_mode", None)
    if mode_stack is None or single_button is None or batch_button is None or not callable(set_mode):
        raise RuntimeError("workspace mode switch requires installed Single/Batch workspace")

    # Both pages are persistent. Establish one geometry owner before the header
    # switch is exposed so clicking the switch never becomes a layout boundary.
    install_workspace_layout_commit(window)

    # _set_workspace_mode hides this Single-only header action in Batch mode. Keep
    # its layout slot so the common header and modeStack geometry are invariant.
    open_run_button = getattr(window, "open_run_button", None)
    if isinstance(open_run_button, QWidget):
        policy = open_run_button.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        open_run_button.setSizePolicy(policy)

    legacy_card = single_button.parentWidget()
    if legacy_card is not None:
        legacy_card.setObjectName("")
        legacy_card.hide()

    root = window.centralWidget()
    outer = root.layout() if root is not None else None
    if root is None or not isinstance(outer, QVBoxLayout) or outer.count() < 1:
        raise RuntimeError("workspace mode switch expected the preserved root layout")

    header_item = outer.itemAt(0)
    header = header_item.layout() if header_item is not None else None
    if not isinstance(header, QHBoxLayout):
        raise RuntimeError("workspace mode switch expected the common header row")

    _install_header_mode_reflow(window, mode_stack)

    toggle = WorkspaceModeSwitch(root)
    toggle.set_checked_immediate(int(mode_stack.currentIndex()) == 1)

    def request_mode(checked: bool) -> None:
        target = 1 if checked else 0
        if target == int(mode_stack.currentIndex()):
            toggle.set_checked_immediate(target == 1)
            return
        transition = getattr(window, "_workspace_transition_controller", None)
        request = getattr(transition, "request_mode", None)
        if callable(request):
            request(target)
        else:
            set_mode(target)

    toggle.clicked.connect(request_mode)

    def sync_from_stack(index: int) -> None:
        target = int(index) == 1
        if toggle.isChecked() != target:
            toggle.set_checked_immediate(target)

    mode_stack.currentChanged.connect(sync_from_stack)
    header.addWidget(toggle, 0, Qt.AlignmentFlag.AlignBottom)
    window._workspace_mode_switch = toggle  # type: ignore[attr-defined]
    install_ai_settings_modal(window)
    return toggle