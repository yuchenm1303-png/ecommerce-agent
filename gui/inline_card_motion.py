from __future__ import annotations

import time
from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, QTimer
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


def _glass_sync(window: QMainWindow) -> None:
    visual = getattr(window, "_visual_style", None)
    background = getattr(visual, "background", None)
    if background is not None and hasattr(background, "schedule_mask_update"):
        background.schedule_mask_update()


class AdaptiveReveal(QObject):
    """Animate inline card content while the normal layout keeps siblings apart."""

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
        self._responsive_suspended = False

        self.wrapper.setMinimumHeight(0)
        self.wrapper.setMaximumHeight(0)
        self.wrapper.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.toggle.setText(self.collapsed_text)
        self.toggle.toggled.connect(self.set_expanded)

    @staticmethod
    def _ease(progress: float) -> float:
        # Quintic smoothstep gives zero start/end velocity, avoiding the final snap.
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
            return max(0, int(self.expanded_height(natural)))
        return natural

    @staticmethod
    def _duration_for(distance: int) -> int:
        # A small card feels immediate; a taller card gets enough time to read as
        # physical expansion, but never turns into a slow accordion animation.
        return max(_MIN_DURATION_MS, min(_MAX_DURATION_MS, 148 + int(abs(distance) * 0.28)))

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
        setattr(self.window, "_inline_card_motion_active", True)

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
        shell = max(86, self.card.height() - max(0, self.wrapper.height()))
        target_card = min(max(108, shell + wrapper_height), max(108, available - 250))
        self.splitter.setSizes([max(250, available - target_card), target_card])

    def _tick(self) -> None:
        elapsed_ms = (time.perf_counter() - self._started) * 1000.0
        progress = min(1.0, elapsed_ms / max(1.0, float(self._duration_ms)))
        eased = self._ease(progress)
        height = int(round(self._from + (self._to - self._from) * eased))

        # This is the only animated layout property. The parent QVBox/QSplitter
        # reflows normally, so no two cards can occupy the same geometry.
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
    QTimer.singleShot(0, lambda: _glass_sync(window))
    return motions
