from __future__ import annotations

import sys
from typing import Any

from PySide6.QtCore import QEvent, QObject, QPointF, QTimer, Qt, QUrl
from PySide6.QtGui import QMouseEvent
from PySide6.QtQml import QQmlComponent
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtWidgets import QFrame, QMainWindow, QWidget

from .static_qml_bridge import StaticCardModel, StaticQmlBridge
from .static_qml_fireworks import StaticQuickFireworks
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
        self.card_fx = getattr(window, "_nekro_card_fx", None)
        self.toggle = getattr(window, "_background_drift_switch", None)
        self.shell = getattr(window, "_native_window_shell", None)
        self.legacy_fireworks = getattr(window, "_click_fireworks", None)

        self._static_active = False
        self._static_requested = False
        self._load_started = False
        self._load_failed = False
        self._static_handoff_armed = False
        self._legacy_handoff_armed = False
        self._legacy_suspended = False
        self._card_fx_suspended_here = False
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

        self.fireworks = StaticQuickFireworks()
        self.fireworks.setParent(self)
        self.fireworks.setParentItem(self.quick.contentItem())
        self.fireworks.setZ(30000.0)

        self._switch_timer = QTimer(self)
        self._switch_timer.setSingleShot(True)
        self._switch_timer.setInterval(_SWITCH_TRANSITION_MS)
        self._switch_timer.timeout.connect(self._commit_drift_view_switch)

        self.quick.installEventFilter(self)
        self.quick.widthChanged.connect(self._fit_and_refresh)
        self.quick.heightChanged.connect(self._fit_and_refresh)
        if self.toggle is not None:
            self.toggle.toggled.connect(self._on_drift_changed)

        handoff_ready = getattr(startup_gate, "handoffReady", None)
        if handoff_ready is None or not hasattr(handoff_ready, "connect"):
            raise RuntimeError("static QML view requires explicit startup handoff signal")
        handoff_ready.connect(self._activate_after_startup)
        window.destroyed.connect(self._cleanup)

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

    def _all_glass_frames(self) -> tuple[QFrame, ...]:
        glass = getattr(self.visual, "_glass", None)
        if not isinstance(glass, dict):
            return ()
        return tuple(frame for frame in glass if isinstance(frame, QFrame))

    def _set_native_glass_overlay_alpha(self, alpha: float) -> None:
        # native_background remains the only blur renderer. Static QML draws only
        # the same black 64/102 overlay, so suppress the old overlay but not blur.
        # Apply to every registered card, including hidden workspace pages, so
        # returning to legacy can never expose stale alpha/scale state later.
        for frame in self._all_glass_frames():
            try:
                self.background.set_card_presentation(frame, scale=1.0, alpha=float(alpha))
            except RuntimeError:
                continue

    def _set_legacy_fireworks_enabled(self, enabled: bool) -> None:
        setter = getattr(self.legacy_fireworks, "set_enabled", None)
        if callable(setter):
            try:
                setter(bool(enabled))
            except RuntimeError:
                pass

    def _fail_scene(self, reason: str) -> None:
        if self._load_failed:
            return
        self._load_failed = True
        self._static_requested = False
        self._switch_timer.stop()
        self._pending_drift = None
        self._disconnect_static_handoff()
        self._disconnect_legacy_handoff()
        self.bridge.set_active(False)
        self.fireworks.clear()
        self._set_native_glass_overlay_alpha(64.0)
        self._set_legacy_fireworks_enabled(True)
        try:
            self.shell.set_overlay_presented(True)
        except RuntimeError:
            pass
        self._static_active = False
        self._resume_legacy()
        message = reason.strip() or "unknown QML creation failure"
        print(
            "[static-qml] original static scene unavailable; keeping legacy presentation:\n"
            + message,
            file=sys.stderr,
        )

    def _fit(self) -> None:
        width = float(max(1, self.quick.width()))
        height = float(max(1, self.quick.height()))
        item = self.item
        if item is not None:
            try:
                item.setWidth(width)
                item.setHeight(height)
            except RuntimeError:
                pass
        try:
            self.fireworks.setWidth(width)
            self.fireworks.setHeight(height)
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

        card_suspend = getattr(self.card_fx, "suspend_for_modal", None)
        already_suspended = bool(getattr(self.card_fx, "_suspended", False))
        if callable(card_suspend) and not already_suspended:
            try:
                card_suspend()
                self._card_fx_suspended_here = True
            except RuntimeError:
                self._card_fx_suspended_here = False

    def _resume_legacy(self) -> None:
        if self._card_fx_suspended_here:
            self._card_fx_suspended_here = False
            card_resume = getattr(self.card_fx, "resume_from_modal", None)
            if callable(card_resume):
                try:
                    card_resume()
                except RuntimeError:
                    pass

        if not self._legacy_suspended:
            return
        self._legacy_suspended = False
        resume = getattr(self.clock, "resume", None)
        if callable(resume):
            resume("static_qml")

    def _prime_legacy_surface(self) -> None:
        """Prepare the hidden QWidget/backdrop before native presentation returns."""

        root = self.window.centralWidget()
        if isinstance(root, QWidget):
            try:
                layout = root.layout()
                if layout is not None:
                    layout.activate()
                root.repaint()
            except RuntimeError:
                pass

        for frame in self._all_glass_frames():
            try:
                if frame.isVisibleTo(self.window):
                    frame.repaint()
            except RuntimeError:
                continue

        geometry_timer = getattr(self.background, "_geometry_timer", None)
        if geometry_timer is not None:
            try:
                geometry_timer.stop()
            except RuntimeError:
                pass
        flush_geometry = getattr(self.background, "_flush_geometry", None)
        if callable(flush_geometry):
            try:
                flush_geometry()
            except RuntimeError:
                pass

    def _enter_static(self) -> None:
        self._static_requested = True
        if self._legacy_handoff_armed:
            self._disconnect_legacy_handoff()
        if self._static_active and self.bridge.active:
            return
        if self._load_failed:
            return
        if self.item is None:
            self._ensure_scene_loaded()
            return

        self.bridge.refresh()
        self._suspend_legacy()
        self._set_legacy_fireworks_enabled(False)
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
        self._arm_static_handoff()

    def _arm_static_handoff(self) -> None:
        if self._static_handoff_armed:
            return
        self._static_handoff_armed = True
        try:
            self.quick.frameSwapped.connect(self._commit_static_handoff)
        except (RuntimeError, TypeError):
            self._static_handoff_armed = False
            QTimer.singleShot(0, self._commit_static_handoff)

    def _disconnect_static_handoff(self) -> None:
        if not self._static_handoff_armed:
            return
        self._static_handoff_armed = False
        try:
            self.quick.frameSwapped.disconnect(self._commit_static_handoff)
        except (RuntimeError, TypeError):
            pass

    def _commit_static_handoff(self) -> None:
        self._disconnect_static_handoff()
        if not self._static_active or not self._static_requested:
            return
        try:
            self.shell.set_overlay_presented(False)
        except RuntimeError:
            self._leave_static()

    def _arm_legacy_handoff(self) -> None:
        if self._legacy_handoff_armed:
            return
        self._legacy_handoff_armed = True
        try:
            self.quick.frameSwapped.connect(self._commit_legacy_handoff)
        except (RuntimeError, TypeError):
            self._legacy_handoff_armed = False
            QTimer.singleShot(0, self._commit_legacy_handoff)

    def _disconnect_legacy_handoff(self) -> None:
        if not self._legacy_handoff_armed:
            return
        self._legacy_handoff_armed = False
        try:
            self.quick.frameSwapped.disconnect(self._commit_legacy_handoff)
        except (RuntimeError, TypeError):
            pass

    def _commit_legacy_handoff(self) -> None:
        self._disconnect_legacy_handoff()
        if self._static_requested:
            return

        try:
            self.shell.set_overlay_presented(True)
        except RuntimeError:
            pass
        self._static_active = False
        self._set_legacy_fireworks_enabled(True)
        self._resume_legacy()

    def _leave_static(self) -> None:
        self._static_requested = False
        self._disconnect_static_handoff()
        if not self._static_active:
            self._disconnect_legacy_handoff()
            self._set_native_glass_overlay_alpha(64.0)
            self._set_legacy_fireworks_enabled(True)
            self._resume_legacy()
            return
        if self._legacy_handoff_armed:
            return

        # QWidget is still hidden at the native HWND boundary here. Rebuild its
        # backing store and glass geometry first, while the already-rendered static
        # scene remains the only presentation visible to the user.
        self._prime_legacy_surface()
        self.fireworks.clear()
        self._set_native_glass_overlay_alpha(64.0)

        # Remove static QML from a real Quick frame before the transparent QWidget
        # child is shown again. Showing the child first lets the still-present QML
        # scene shine through its transparent regions, which is the duplicated/card-
        # scrambled frame seen when drift was enabled.
        self.bridge.set_active(False)
        if self.item is not None:
            try:
                self.item.setVisible(False)
            except RuntimeError:
                pass
        self._arm_legacy_handoff()
        try:
            self.quick.requestUpdate()
        except RuntimeError:
            self._commit_legacy_handoff()

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

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is not self.quick or not self._static_active:
            return False
        if event.type() not in {
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonDblClick,
        }:
            return False
        if not isinstance(event, QMouseEvent) or event.button() != Qt.MouseButton.LeftButton:
            return False
        try:
            self.fireworks.spawn(QPointF(event.position()))
        except RuntimeError:
            pass
        return False

    def _cleanup(self) -> None:
        self._switch_timer.stop()
        self._disconnect_static_handoff()
        self._disconnect_legacy_handoff()
        self.fireworks.clear()
        try:
            self.quick.removeEventFilter(self)
        except RuntimeError:
            pass


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
