from __future__ import annotations

import time
from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QLayout,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

_MAX_HEIGHT = 16_777_215
_TICK_MS = 8
_MASK_SYNC_MS = 32
_MIN_DURATION_MS = 156
_MAX_DURATION_MS = 218


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


def _detach_layout(parent: QLayout, child: QLayout) -> QLayout:
    index = _layout_index(parent, child)
    if index < 0:
        return child
    item = parent.takeAt(index)
    return item.layout() or child


def _detach_widget(parent: QLayout, widget: QWidget) -> QWidget:
    index = _widget_index(parent, widget)
    if index < 0:
        return widget
    item = parent.takeAt(index)
    return item.widget() or widget


def _glass_sync(window: QMainWindow) -> None:
    visual = getattr(window, "_visual_style", None)
    background = getattr(visual, "background", None)
    if background is not None and hasattr(background, "schedule_mask_update"):
        background.schedule_mask_update()


class AdaptiveReveal(QObject):
    """Animate an inline card body without ever overlapping sibling cards.

    Only the clipping wrapper height changes. The parent layout performs normal
    reflow each frame, so following cards move with the animation instead of
    jumping after it. For splitter-hosted cards, the splitter allocation follows
    the same progress curve.
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

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._tick)

        self._from = 0
        self._to = 0
        self._started = 0.0
        self._duration_ms = _MIN_DURATION_MS
        self._expanded = False
        self._last_mask_sync = 0.0

        self.wrapper.setMinimumHeight(0)
        self.wrapper.setMaximumHeight(0)
        self.wrapper.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.toggle.setText(self.collapsed_text)
        self.toggle.toggled.connect(self.set_expanded)

    @staticmethod
    def _ease(progress: float) -> float:
        # Quintic smoothstep: zero velocity at both ends and no abrupt layout snap.
        p = min(1.0, max(0.0, progress))
        return p * p * p * (p * (p * 6.0 - 15.0) + 10.0)

    def _measure_natural_height(self) -> int:
        old = self.wrapper.maximumHeight()
        self.wrapper.setMaximumHeight(_MAX_HEIGHT)
        layout = self.wrapper.layout()
        if layout is not None:
            layout.activate()
        natural = max(0, self.wrapper.sizeHint().height())
        self.wrapper.setMaximumHeight(old)
        return natural

    def _target_height(self) -> int:
        natural = self._measure_natural_height()
        if self.expanded_height is not None:
            natural = min(natural, max(0, int(self.expanded_height(natural))))
        return natural

    def _duration_for(self, distance: int) -> int:
        # Longer cards get a little more travel time, never enough to feel slow.
        return max(_MIN_DURATION_MS, min(_MAX_DURATION_MS, 148 + int(abs(distance) * 0.28)))

    def _suspend_responsive_controller(self) -> None:
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
        setattr(self.window, "_inline_card_motion_active", True)

    def _resume_responsive_controller(self) -> None:
        mature = getattr(self.window, "_mature_ui", None)
        root = getattr(mature, "root", None)
        if root is not None and mature is not None:
            try:
                root.installEventFilter(mature)
            except RuntimeError:
                pass
        setattr(self.window, "_inline_card_motion_active", False)
        if mature is not None and hasattr(mature, "schedule"):
            mature.schedule()

    def set_expanded(self, expanded: bool) -> None:
        expanded = bool(expanded)
        self._timer.stop()
        self._suspend_responsive_controller()

        self._from = max(0, self.wrapper.height())
        self._to = self._target_height() if expanded else 0
        self._started = time.perf_counter()
        self._duration_ms = self._duration_for(self._to - self._from)
        self._expanded = expanded
        self._last_mask_sync = 0.0

        self.wrapper.setEnabled(False)
        self.toggle.setText(self.expanded_text if expanded else self.collapsed_text)

        if self.splitter is not None:
            self.card.setMinimumHeight(0)
            self.card.setMaximumHeight(_MAX_HEIGHT)

        if self._from == self._to:
            self._finish()
            return
        self._timer.start()

    def _apply_splitter_height(self, wrapper_height: int) -> None:
        if self.splitter is None or self.splitter.count() < 2:
            return
        available = max(1, self.splitter.height() - self.splitter.handleWidth())
        # The collapsed shell is header + progress + margins; current card height
        # minus the currently visible wrapper is the most robust live estimate.
        shell = max(86, self.card.height() - max(0, self.wrapper.height()))
        target_card = min(max(108, shell + wrapper_height), max(108, available - 250))
        self.splitter.setSizes([max(250, available - target_card), target_card])

    def _tick(self) -> None:
        elapsed_ms = (time.perf_counter() - self._started) * 1000.0
        progress = min(1.0, elapsed_ms / max(1.0, float(self._duration_ms)))
        eased = self._ease(progress)
        height = int(round(self._from + (self._to - self._from) * eased))
        self.wrapper.setMaximumHeight(max(0, height))
        self._apply_splitter_height(height)

        now_ms = time.perf_counter() * 1000.0
        if now_ms - self._last_mask_sync >= _MASK_SYNC_MS:
            self._last_mask_sync = now_ms
            _glass_sync(self.window)

        if progress >= 1.0:
            self._finish()

    def _finish(self) -> None:
        self._timer.stop()
        self.wrapper.setMaximumHeight(max(0, self._to))
        self.wrapper.setEnabled(True)

        if self.splitter is not None:
            if self._expanded:
                self.card.setMinimumHeight(300)
                self.card.setMaximumHeight(480)
            else:
                self.card.setMinimumHeight(108)
                self.card.setMaximumHeight(124)

        _glass_sync(self.window)
        self._resume_responsive_controller()

    def cleanup(self) -> None:
        self._timer.stop()
        self._resume_responsive_controller()


def _build_real_settings_motion(window: QMainWindow) -> AdaptiveReveal | None:
    toggle = getattr(window, "real_settings_toggle", None)
    scope = getattr(window, "real_scope_combo", None)
    start = getattr(window, "real_start_button", None)
    if not all(isinstance(value, QWidget) for value in (toggle, scope, start)):
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

    controls = _find_direct_layout(layout, scope)
    actions = _find_direct_layout(layout, start)
    summary = _find_direct_layout(layout, toggle)
    if not all(isinstance(value, QLayout) for value in (controls, actions, summary)):
        return None

    try:
        toggle.toggled.disconnect()
    except (RuntimeError, TypeError):
        pass

    controls = _detach_layout(layout, controls)
    actions = _detach_layout(layout, actions)

    wrapper = QWidget(card)
    wrapper.setObjectName("realSettingsReveal")
    body = QVBoxLayout(wrapper)
    body.setContentsMargins(0, 0, 0, 0)
    body.setSpacing(6)
    body.addLayout(controls)
    body.addLayout(actions)

    for name in (
        "real_scope_combo",
        "real_save_check",
        "real_upload_check",
        "real_pick_images_button",
        "real_image_count",
        "real_qc_check",
        "real_policy_hint",
        "real_start_button",
        "real_stop_button",
    ):
        widget = getattr(window, name, None)
        if isinstance(widget, QWidget):
            widget.setVisible(True)

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

    phases = _find_direct_layout(layout, phase_units[0]) if isinstance(phase_units[0], QWidget) else None
    progress_row = _find_direct_layout(layout, progress) if isinstance(progress, QWidget) else None
    if not isinstance(phases, QLayout) or not isinstance(progress_row, QLayout):
        return None

    try:
        toggle.toggled.disconnect()
    except (RuntimeError, TypeError):
        pass

    phases = _detach_layout(layout, phases)
    _detach_widget(layout, tabs)

    wrapper = QWidget(console)
    wrapper.setObjectName("consoleDetailReveal")
    wrapper_layout = QVBoxLayout(wrapper)
    wrapper_layout.setContentsMargins(0, 0, 0, 0)
    wrapper_layout.setSpacing(5)
    wrapper_layout.addLayout(phases)
    wrapper_layout.addWidget(tabs, 1)

    for unit in phase_units:
        if isinstance(unit, QWidget):
            unit.setVisible(True)
    tabs.setVisible(True)

    progress_index = _layout_index(layout, progress_row)
    layout.insertWidget(max(0, progress_index + 1), wrapper, 1)

    def expanded_target(natural: int) -> int:
        available = max(1, body_splitter.height() - body_splitter.handleWidth())
        # Preserve at least 250 px for the field workspace. The wrapper gets the
        # rest after the ~90 px console shell, capped by its natural size.
        budget = max(220, min(360, available - 340))
        return min(natural, budget)

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
    window.destroyed.connect(lambda: [motion.cleanup() for motion in motions])
    QTimer.singleShot(0, lambda: _glass_sync(window))
    return motions
