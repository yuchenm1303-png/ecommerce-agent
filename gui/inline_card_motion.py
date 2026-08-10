from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEasingCurve, QObject, QPropertyAnimation, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLayout,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .inline_motion_glass_guard import begin_inline_motion, end_inline_motion

_MAX_HEIGHT = 16_777_215
_MIN_DURATION_MS = 150
_MAX_DURATION_MS = 188


def _find_direct_layout(parent: QLayout, widget: QWidget) -> QLayout | None:
    for index in range(parent.count()):
        child = parent.itemAt(index).layout()
        if child is None:
            continue
        for child_index in range(child.count()):
            if child.itemAt(child_index).widget() is widget:
                return child
    return None


def _layout_index(parent: QLayout, child: QLayout) -> int:
    for index in range(parent.count()):
        if parent.itemAt(index).layout() is child:
            return index
    return -1


def _widget_index(parent: QLayout, widget: QWidget) -> int:
    for index in range(parent.count()):
        if parent.itemAt(index).widget() is widget:
            return index
    return -1


def _remove_layout(parent: QLayout, child: QLayout) -> None:
    index = _layout_index(parent, child)
    if index < 0:
        return
    item = parent.takeAt(index)
    layout = item.layout()
    if layout is not None:
        layout.deleteLater()


def _detach_widget(parent: QLayout, widget: QWidget) -> None:
    index = _widget_index(parent, widget)
    if index >= 0:
        parent.takeAt(index)


class AdaptiveReveal(QObject):
    """Inline reveal with exactly one layout-changing property per frame.

    The detail wrapper is prepared at full internal height and the parent card
    clips it. Only the card's height constraint is animated by Qt's C++ driver.
    No Python frame timer, no per-frame splitter setSizes, and no second wrapper
    height animation participate in the hot path.
    """

    def __init__(
        self,
        window: QMainWindow,
        *,
        card: QFrame,
        wrapper: QWidget,
        toggle: QPushButton,
        expanded_text: str,
        collapsed_text: str,
        splitter: QSplitter | None = None,
        expanded_height: Callable[[int], int] | None = None,
    ) -> None:
        super().__init__(window)
        self.window = window
        self.card = card
        self.wrapper = wrapper
        self.toggle = toggle
        self.splitter = splitter
        self.expanded_text = expanded_text
        self.collapsed_text = collapsed_text
        self.expanded_height = expanded_height

        self._animation: QPropertyAnimation | None = None
        self._expanded = False
        self._responsive_suspended = False
        self._collapsed_card_height = max(1, card.height())
        self._expanded_wrapper_height = 0
        self._final_card_height = self._collapsed_card_height

        self.wrapper.setMinimumHeight(0)
        self.wrapper.setMaximumHeight(0)
        self.wrapper.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.wrapper.hide()
        self.toggle.setText(self.collapsed_text)
        self.toggle.toggled.connect(self.set_expanded)

    def _measure_target_wrapper_height(self) -> int:
        old = self.wrapper.maximumHeight()
        self.wrapper.setMaximumHeight(_MAX_HEIGHT)
        layout = self.wrapper.layout()
        if layout is not None:
            layout.activate()
        natural = max(0, self.wrapper.sizeHint().height())
        self.wrapper.setMaximumHeight(old)
        if self.expanded_height is not None:
            return max(0, int(self.expanded_height(natural)))
        return natural

    @staticmethod
    def _duration_for(distance: int) -> int:
        return max(
            _MIN_DURATION_MS,
            min(_MAX_DURATION_MS, 142 + int(abs(distance) * 0.15)),
        )

    def _stop_animation(self) -> None:
        animation = self._animation
        self._animation = None
        if animation is not None:
            animation.stop()
            animation.deleteLater()

    def _suspend_responsive_controller(self) -> None:
        if self._responsive_suspended:
            return
        mature = getattr(self.window, "_mature_ui", None)
        timer = getattr(mature, "_timer", None)
        root = getattr(mature, "root", None)
        if isinstance(timer, QTimer):
            timer.stop()
        if root is not None and mature is not None:
            try:
                root.removeEventFilter(mature)
            except RuntimeError:
                pass
        self._responsive_suspended = True
        begin_inline_motion(self.window)

    def _resume_responsive_controller(self) -> None:
        if not self._responsive_suspended:
            return
        mature = getattr(self.window, "_mature_ui", None)
        root = getattr(mature, "root", None)
        if root is not None and mature is not None:
            try:
                root.installEventFilter(mature)
            except RuntimeError:
                pass
        self._responsive_suspended = False
        if mature is not None and hasattr(mature, "schedule"):
            mature.schedule()
        end_inline_motion(self.window)

    def _expanded_card_target(self, wrapper_height: int) -> int:
        if self.splitter is None:
            return max(self._collapsed_card_height, self._collapsed_card_height + wrapper_height)

        available = max(1, self.splitter.height() - self.splitter.handleWidth())
        target = min(440, max(340, available - 300))
        return min(target, max(260, available - 260))

    def _build_constraint_animation(
        self,
        *,
        expanded: bool,
        start_height: int,
        end_height: int,
        duration: int,
    ) -> QPropertyAnimation:
        if self.splitter is not None and expanded:
            self.card.setMaximumHeight(max(end_height, 460))
            self.card.setMinimumHeight(start_height)
            animation = QPropertyAnimation(self.card, b"minimumHeight")
        else:
            self.card.setMinimumHeight(0)
            self.card.setMaximumHeight(start_height)
            animation = QPropertyAnimation(self.card, b"maximumHeight")

        animation.setStartValue(start_height)
        animation.setEndValue(end_height)
        animation.setDuration(duration)
        animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        return animation

    def set_expanded(self, expanded: bool) -> None:
        expanded = bool(expanded)
        self._stop_animation()
        self._suspend_responsive_controller()

        current_height = max(1, self.card.height())
        if expanded and not self._expanded:
            self._collapsed_card_height = current_height

        self._expanded = expanded
        self.toggle.setText(self.expanded_text if expanded else self.collapsed_text)

        if expanded:
            self.wrapper.show()
            self._expanded_wrapper_height = self._measure_target_wrapper_height()
            self.wrapper.setMaximumHeight(self._expanded_wrapper_height)
            if self.wrapper.layout() is not None:
                self.wrapper.layout().activate()
            if self.card.layout() is not None:
                self.card.layout().activate()
            end_height = self._expanded_card_target(self._expanded_wrapper_height)
            if self.splitter is None:
                # Land directly at the real final layout size so there is no
                # post-animation correction/jump by a few pixels.
                end_height = max(end_height, self.card.sizeHint().height())
            self._final_card_height = end_height
        else:
            end_height = max(1, self._collapsed_card_height)
            self._final_card_height = end_height

        if current_height == end_height:
            self._finish()
            return

        animation = self._build_constraint_animation(
            expanded=expanded,
            start_height=current_height,
            end_height=end_height,
            duration=self._duration_for(end_height - current_height),
        )
        animation.finished.connect(self._finish)
        self._animation = animation
        animation.start()

    def _finish(self) -> None:
        animation = self._animation
        self._animation = None
        if animation is not None:
            animation.deleteLater()

        if self._expanded:
            self.wrapper.setMaximumHeight(max(0, self._expanded_wrapper_height))
            self.wrapper.show()
            if self.splitter is not None:
                final_height = max(300, min(460, self._final_card_height))
                self.card.setMinimumHeight(final_height)
                self.card.setMaximumHeight(460)
            else:
                self.card.setMinimumHeight(0)
                self.card.setMaximumHeight(self._final_card_height)
        else:
            self.wrapper.setMaximumHeight(0)
            self.wrapper.hide()
            if self.splitter is not None:
                self.card.setMinimumHeight(108)
                self.card.setMaximumHeight(124)
            else:
                self.card.setMinimumHeight(0)
                self.card.setMaximumHeight(_MAX_HEIGHT)

        self._resume_responsive_controller()

    def cleanup(self) -> None:
        self._stop_animation()
        self._resume_responsive_controller()


def _build_real_settings_motion(window: QMainWindow) -> AdaptiveReveal | None:
    toggle = getattr(window, "real_settings_toggle", None)
    scope = getattr(window, "real_scope_combo", None)
    start = getattr(window, "real_start_button", None)
    if not isinstance(toggle, QPushButton) or not isinstance(scope, QWidget) or not isinstance(start, QWidget):
        return None

    card = scope.parentWidget()
    while isinstance(card, QWidget) and not (
        isinstance(card, QFrame) and card.objectName() == "heroCard"
    ):
        card = card.parentWidget()
    if not isinstance(card, QFrame):
        return None
    layout = card.layout()
    if not isinstance(layout, QVBoxLayout):
        return None

    old_controls = _find_direct_layout(layout, scope)
    old_actions = _find_direct_layout(layout, start)
    summary = _find_direct_layout(layout, toggle)
    if old_controls is None or old_actions is None or summary is None:
        return None

    try:
        toggle.toggled.disconnect()
    except (RuntimeError, TypeError):
        pass

    _remove_layout(layout, old_controls)
    _remove_layout(layout, old_actions)

    wrapper = QWidget(card)
    wrapper.setObjectName("realSettingsReveal")
    body = QVBoxLayout(wrapper)
    body.setContentsMargins(0, 0, 0, 0)
    body.setSpacing(6)

    controls = QHBoxLayout()
    controls.setSpacing(9)
    for name in (
        "real_scope_combo",
        "real_save_check",
        "real_upload_check",
        "real_pick_images_button",
        "real_image_count",
        "real_qc_check",
    ):
        widget = getattr(window, name, None)
        if isinstance(widget, QWidget):
            widget.setVisible(True)
            controls.addWidget(widget)
    controls.addStretch(1)
    body.addLayout(controls)

    actions = QHBoxLayout()
    actions.setSpacing(8)
    policy = getattr(window, "real_policy_hint", None)
    if isinstance(policy, QWidget):
        policy.setVisible(True)
        actions.addWidget(policy, 1)
    actions.addSpacing(10)
    for name in ("real_start_button", "real_stop_button"):
        widget = getattr(window, name, None)
        if isinstance(widget, QWidget):
            widget.setVisible(True)
            actions.addWidget(widget)
    body.addLayout(actions)

    summary_index = _layout_index(layout, summary)
    layout.insertWidget(max(0, summary_index + 1), wrapper)
    card.setMaximumHeight(_MAX_HEIGHT)

    return AdaptiveReveal(
        window,
        card=card,
        wrapper=wrapper,
        toggle=toggle,
        expanded_text="收起设置 ︿",
        collapsed_text="展开设置 ﹀",
    )


def _build_console_motion(window: QMainWindow) -> AdaptiveReveal | None:
    toggle = getattr(window, "console_detail_toggle", None)
    console = getattr(window, "console", None)
    body_splitter = getattr(window, "_ui_polish_body_splitter", None)
    if not isinstance(toggle, QPushButton) or not isinstance(console, QFrame) or not isinstance(body_splitter, QSplitter):
        return None
    layout = console.layout()
    tabs = getattr(console, "tabs", None)
    phase_units = list(getattr(console, "phase_units", {}).values())
    progress = getattr(console, "progress", None)
    if not isinstance(layout, QVBoxLayout) or not isinstance(tabs, QTabWidget) or not phase_units:
        return None

    old_phases = _find_direct_layout(layout, phase_units[0]) if isinstance(phase_units[0], QWidget) else None
    progress_row = _find_direct_layout(layout, progress) if isinstance(progress, QWidget) else None
    if old_phases is None or progress_row is None:
        return None

    try:
        toggle.toggled.disconnect()
    except (RuntimeError, TypeError):
        pass

    _remove_layout(layout, old_phases)
    _detach_widget(layout, tabs)

    wrapper = QWidget(console)
    wrapper.setObjectName("consoleDetailReveal")
    wrapper_layout = QVBoxLayout(wrapper)
    wrapper_layout.setContentsMargins(0, 0, 0, 0)
    wrapper_layout.setSpacing(5)

    phases = QHBoxLayout()
    phases.setSpacing(7)
    for unit in phase_units:
        if isinstance(unit, QWidget):
            unit.setVisible(True)
            phases.addWidget(unit, 1)
    wrapper_layout.addLayout(phases)
    tabs.setVisible(True)
    wrapper_layout.addWidget(tabs, 1)

    progress_index = _layout_index(layout, progress_row)
    layout.insertWidget(max(0, progress_index + 1), wrapper, 1)

    def expanded_target(_natural: int) -> int:
        available = max(1, body_splitter.height() - body_splitter.handleWidth())
        target_total = min(440, max(340, available - 300))
        target_total = min(target_total, max(260, available - 260))
        collapsed_shell = max(96, console.height())
        return max(220, target_total - collapsed_shell)

    return AdaptiveReveal(
        window,
        card=console,
        wrapper=wrapper,
        toggle=toggle,
        expanded_text="收起详情 ︿",
        collapsed_text="展开详情 ﹀",
        splitter=body_splitter,
        expanded_height=expanded_target,
    )


def install_inline_card_motion(window: QMainWindow) -> list[AdaptiveReveal]:
    """Install adaptive inline expansion after ui_polish/ui_maturity are ready."""

    motions: list[AdaptiveReveal] = []
    for builder in (_build_real_settings_motion, _build_console_motion):
        motion = builder(window)
        if motion is not None:
            motions.append(motion)

    window._inline_card_motions = motions  # type: ignore[attr-defined]
    window.destroyed.connect(lambda *_: [motion.cleanup() for motion in motions])
    return motions
