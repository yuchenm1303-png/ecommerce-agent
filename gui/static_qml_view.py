from __future__ import annotations

import sys
from typing import Any

from PySide6.QtCore import QObject, QTimer, QUrl
from PySide6.QtQml import QQmlComponent
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtWidgets import QMainWindow

from .static_qml_bridge import StaticCardModel, StaticQmlBridge
from .static_qml_scene import STATIC_QML_SOURCE


_STATIC_QML_URL = QUrl("inmemory:/OriginalStaticRoot.qml")
_SWITCH_TRANSITION_MS = 300


class StaticQmlViewController(QObject):
    """Use one existing Quick scene for drift-off while the old QWidget tree stays alive."""

    def __init__(self, window: QMainWindow, visual: Any, startup_gate: Any) -> None:
        super().__init__(window)
        self.window = window
        self.visual = visual
        self.background = visual.background
        self.quick = self.background.quick_window
        self.engine = self.background.engine
        self.clock = getattr(window, "_presentation_clock", None)
        self.toggle = getattr(window, "_background_drift_switch", None)
        self.shell = getattr(window, "_native_window_shell", None)

        self._static_active = False
        self._static_requested = False
        self._load_started = False
        self._load_failed = False
        self._handoff_armed = False
        self._legacy_suspended = False
        self._pending_drift: bool | None = None
        self.item: QQuickItem | None = None
        self.component: QQmlComponent | None = None

        if not isinstance(self.quick, QQuickWindow):
            raise RuntimeError("static QML view requires the existing native QQuickWindow")
        if not callable(getattr(self.shell, "set_overlay_presented", None)):
            raise RuntimeError("static QML view requires native child presentation ownership")

        self.card_model = StaticCardModel(self)
        self.bridge = StaticQmlBridge(window, visual, self.card_model, self)
        self.bridge.bind_runtime_sources()

        context = self.engine.rootContext()
        context.setContextProperty("staticBridge", self.bridge)
        context.setContextProperty("staticCardModel", self.card_model)

        self._switch_timer = QTimer(self)
        self._switch_timer.setSingleShot(True)
        self._switch_timer.setInterval(_SWITCH_TRANSITION_MS)
        self._switch_timer.timeout.connect(self._commit_drift_view_switch)

        self.quick.widthChanged.connect(self._fit_and_refresh)
        self.quick.heightChanged.connect(self._fit_and_refresh)
        if self.toggle is not None:
            self.toggle.toggled.connect(self._on_drift_changed)

        handoff_ready = getattr(startup_gate, "handoffReady", None)
        if handoff_ready is None or not hasattr(handoff_ready, "connect"):
            raise RuntimeError("static QML view requires explicit startup handoff signal")
        handoff_ready.connect(self._activate_after_startup)

    @property
    def static_active(self) -> bool:
        return self._static_active

    def _ensure_scene_loaded(self) -> None:
        if self.item is not None or self._load_started or self._load_failed:
            return

        self._load_started = True
        component = QQmlComponent(self.engine, self)
        self.component = component
        component.statusChanged.connect(self._on_component_status_changed)
        try:
            component.setData(STATIC_QML_SOURCE.encode("utf-8"), _STATIC_QML_URL)
        except RuntimeError as exc:
            self._fail_scene(str(exc))
            return
        self._advance_component_status(component.status())

    def _on_component_status_changed(self, status: QQmlComponent.Status) -> None:
        self._advance_component_status(status)

    def _advance_component_status(self, status: QQmlComponent.Status) -> None:
        component = self.component
        if component is None or self.item is not None or self._load_failed:
            return
        if status in (QQmlComponent.Status.Null, QQmlComponent.Status.Loading):
            return
        if status == QQmlComponent.Status.Error:
            self._fail_scene(self._component_errors())
            return
        if status != QQmlComponent.Status.Ready:
            self._fail_scene(f"unexpected QQmlComponent status: {status}")
            return
        self._create_ready_scene()

    def _component_errors(self) -> str:
        component = self.component
        if component is None:
            return "QML component unavailable"
        try:
            errors = [error.toString() for error in component.errors()]
        except RuntimeError as exc:
            return str(exc)
        return "\n".join(errors) or "QML component creation failed without diagnostics"

    def _create_ready_scene(self) -> None:
        component = self.component
        if component is None or component.status() != QQmlComponent.Status.Ready:
            return
        try:
            created = component.create(self.engine.rootContext())
        except RuntimeError as exc:
            self._fail_scene(str(exc))
            return
        if not isinstance(created, QQuickItem):
            if created is not None:
                try:
                    created.deleteLater()
                except RuntimeError:
                    pass
            self._fail_scene(self._component_errors())
            return
        try:
            created.setParent(self)
            created.setParentItem(self.quick.contentItem())
            created.setVisible(False)
        except RuntimeError as exc:
            try:
                created.deleteLater()
            except RuntimeError:
                pass
            self._fail_scene(str(exc))
            return
        self.item = created
        self._fit()
        if self._static_requested:
            self._enter_static()

    def _set_native_glass_overlay_alpha(self, alpha: float) -> None:
        # native_background remains the only blur renderer.  Static QML draws only
        # the same black 64/102 overlay so we suppress the legacy black rectangle,
        # not the original blur mask itself.
        for frame in tuple(getattr(self.bridge, "_card_frames", ())):
            try:
                self.background.set_card_presentation(frame, scale=1.0, alpha=float(alpha))
            except RuntimeError:
                continue

    def _fail_scene(self, reason: str) -> None:
        if self._load_failed:
            return
        self._load_failed = True
        self._static_requested = False
        self._switch_timer.stop()
        self._pending_drift = None
        self.bridge.set_active(False)
        self._set_native_glass_overlay_alpha(64.0)
        try:
            self.shell.set_overlay_presented(True)
        except RuntimeError:
            pass
        self._resume_legacy()
        message = reason.strip() or "unknown QML creation failure"
        print(
            "[static-qml] original static scene unavailable; keeping legacy presentation:\n"
            + message,
            file=sys.stderr,
        )

    def _fit(self) -> None:
        item = self.item
        if item is None:
            return
        try:
            item.setWidth(float(max(1, self.quick.width())))
            item.setHeight(float(max(1, self.quick.height())))
        except RuntimeError:
            pass

    def _fit_and_refresh(self, *_args: object) -> None:
        self._fit()
        self.bridge.schedule_refresh()

    def _suspend_legacy(self) -> None:
        if self._legacy_suspended:
            return
        suspend = getattr(self.clock, "suspend", None)
        if callable(suspend):
            suspend("static_qml")
            self._legacy_suspended = True

    def _resume_legacy(self) -> None:
        if not self._legacy_suspended:
            return
        self._legacy_suspended = False
        resume = getattr(self.clock, "resume", None)
        if callable(resume):
            resume("static_qml")

    def _enter_static(self) -> None:
        self._static_requested = True
        if self._static_active or self._load_failed:
            return
        if self.item is None:
            self._ensure_scene_loaded()
            return

        self.bridge.refresh()
        self._suspend_legacy()
        self._fit()
        self._set_native_glass_overlay_alpha(0.0)
        self.bridge.set_active(True)
        try:
            self.item.setVisible(True)
            self.quick.requestUpdate()
        except RuntimeError as exc:
            self._fail_scene(str(exc))
            return

        self._static_active = True
        self._arm_rendered_handoff()

    def _arm_rendered_handoff(self) -> None:
        if self._handoff_armed:
            return
        self._handoff_armed = True
        try:
            self.quick.frameSwapped.connect(self._commit_static_handoff)
        except (RuntimeError, TypeError):
            self._handoff_armed = False
            QTimer.singleShot(0, self._commit_static_handoff)

    def _disconnect_handoff(self) -> None:
        if not self._handoff_armed:
            return
        self._handoff_armed = False
        try:
            self.quick.frameSwapped.disconnect(self._commit_static_handoff)
        except (RuntimeError, TypeError):
            pass

    def _commit_static_handoff(self) -> None:
        self._disconnect_handoff()
        if not self._static_active or not self._static_requested:
            return
        try:
            self.shell.set_overlay_presented(False)
        except RuntimeError:
            self._leave_static()

    def _leave_static(self) -> None:
        self._static_requested = False
        self._disconnect_handoff()
        if not self._static_active:
            self._resume_legacy()
            return

        self._static_active = False
        self._set_native_glass_overlay_alpha(64.0)
        try:
            self.shell.set_overlay_presented(True)
        except RuntimeError:
            pass
        self.bridge.set_active(False)
        if self.item is not None:
            try:
                self.item.setVisible(False)
            except RuntimeError:
                pass
        schedule = getattr(self.background, "schedule_mask_update", None)
        if callable(schedule):
            schedule()
        self._resume_legacy()

    def _activate_after_startup(self) -> None:
        enabled = bool(getattr(self.clock, "background_drift_enabled", False))
        if not enabled:
            self._enter_static()

    def _on_drift_changed(self, enabled: bool) -> None:
        # Preserve the original switch's 300 ms motion before changing render owner.
        self._pending_drift = bool(enabled)
        self._switch_timer.start()

    def _commit_drift_view_switch(self) -> None:
        target = self._pending_drift
        self._pending_drift = None
        if target is None:
            return
        if target:
            self._leave_static()
        else:
            self._enter_static()


def install_static_qml_view(
    window: QMainWindow,
    visual: Any,
    startup_gate: Any,
) -> StaticQmlViewController:
    existing = getattr(window, "_static_qml_view_controller", None)
    if isinstance(existing, StaticQmlViewController):
        return existing
    controller = StaticQmlViewController(window, visual, startup_gate)
    window._static_qml_view_controller = controller  # type: ignore[attr-defined]
    return controller


__all__ = ["StaticQmlViewController", "install_static_qml_view"]
