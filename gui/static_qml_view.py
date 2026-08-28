from __future__ import annotations

import sys
from typing import Any

from PySide6.QtCore import QEvent, QObject, QPointF, QTimer, Qt, QUrl
from PySide6.QtGui import QMouseEvent
from PySide6.QtQml import QQmlComponent
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtWidgets import QFrame, QMainWindow

from .static_qml_bridge import StaticCardModel, StaticQmlBridge
from .static_qml_fireworks import StaticQuickFireworks
from .static_qml_scene import STATIC_QML_SOURCE


_STATIC_QML_URL = QUrl("inmemory:/OriginalStaticRoot.qml")


class StaticQmlViewController(QObject):
    """Keep one Quick scene as the visible UI for both drift states.

    The legacy QWidget tree remains alive only as the business/state host and as a
    fallback if the Quick scene cannot be created. Background drift is no longer a
    renderer switch: it only changes the wallpaper parallax state inside the same
    QQuickWindow.
    """

    def __init__(self, window: QMainWindow, visual: Any, startup_gate: Any) -> None:
        super().__init__(window)
        self.window = window
        self.visual = visual
        self.background = visual.background
        self.quick = self.background.quick_window
        self.engine = self.background.engine
        self.clock = getattr(window, "_presentation_clock", None)
        self.card_fx = getattr(window, "_nekro_card_fx", None)
        self.shell = getattr(window, "_native_window_shell", None)
        self.legacy_fireworks = getattr(window, "_click_fireworks", None)

        self._quick_active = False
        self._quick_requested = False
        self._load_started = False
        self._load_failed = False
        self._handoff_armed = False
        self._card_fx_suspended_here = False
        self._startup_prepare_requested = False
        self._startup_snapshot_prepared = False
        self.item: QQuickItem | None = None
        self.component: QQmlComponent | None = None

        if not isinstance(self.quick, QQuickWindow):
            raise RuntimeError("unified Quick view requires the existing native QQuickWindow")
        if not callable(getattr(self.shell, "set_overlay_presented", None)):
            raise RuntimeError("unified Quick view requires native child presentation ownership")

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

        self.quick.installEventFilter(self)
        self.quick.widthChanged.connect(self._fit_and_refresh)
        self.quick.heightChanged.connect(self._fit_and_refresh)

        reveal_preparing = getattr(startup_gate, "revealPreparing", None)
        layout_invalidated = getattr(startup_gate, "layoutInvalidated", None)
        handoff_ready = getattr(startup_gate, "handoffReady", None)
        if (
            reveal_preparing is None
            or not hasattr(reveal_preparing, "connect")
            or layout_invalidated is None
            or not hasattr(layout_invalidated, "connect")
            or handoff_ready is None
            or not hasattr(handoff_ready, "connect")
        ):
            raise RuntimeError("unified Quick view requires explicit startup lifecycle signals")
        reveal_preparing.connect(self._prepare_startup_scene)
        layout_invalidated.connect(self._invalidate_startup_snapshot)
        handoff_ready.connect(self._activate_after_startup)
        window.destroyed.connect(self._cleanup)

        # Compile and create the hidden scene before the visible curtain movement.
        # The lightweight launch surface is still present at this point, so any
        # one-time QML import/component cost cannot steal animation frames later.
        self._ensure_scene_loaded()

    @property
    def static_active(self) -> bool:
        # Retain the established property name for workspace-transition callers.
        return self._quick_active

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
        if self._startup_prepare_requested:
            self._prepare_startup_snapshot()
        if self._quick_requested:
            self._activate_quick()

    def _prepare_startup_scene(self) -> None:
        if self._load_failed or self._quick_active:
            return
        self._startup_prepare_requested = True
        self._ensure_scene_loaded()
        if self.item is not None:
            self._prepare_startup_snapshot()

    def _prepare_startup_snapshot(self) -> None:
        if self._load_failed or self.item is None or self._quick_active:
            return
        self._fit()
        self.bridge.refresh()
        self._startup_snapshot_prepared = True

    def _invalidate_startup_snapshot(self) -> None:
        if not self._quick_active:
            self._startup_snapshot_prepared = False

    def _all_glass_frames(self) -> tuple[QFrame, ...]:
        glass = getattr(self.visual, "_glass", None)
        if not isinstance(glass, dict):
            return ()
        return tuple(frame for frame in glass if isinstance(frame, QFrame))

    def _set_native_glass_overlay_alpha(self, alpha: float) -> None:
        # native_background stays the only blur renderer. The Quick UI owns the
        # original black 64/102 overlay, so the background's duplicate overlay is
        # suppressed for the entire lifetime of the unified Quick presentation.
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

    def _set_widget_lane_enabled(self, enabled: bool) -> None:
        setter = getattr(self.clock, "set_widget_lane_enabled", None)
        if callable(setter):
            try:
                setter(bool(enabled))
            except RuntimeError:
                pass

    def _suspend_legacy_visuals(self) -> None:
        self._set_widget_lane_enabled(False)
        self._set_legacy_fireworks_enabled(False)

        card_suspend = getattr(self.card_fx, "suspend_for_modal", None)
        already_suspended = bool(getattr(self.card_fx, "_suspended", False))
        if callable(card_suspend) and not already_suspended:
            try:
                card_suspend()
                self._card_fx_suspended_here = True
            except RuntimeError:
                self._card_fx_suspended_here = False

    def _resume_legacy_fallback(self) -> None:
        if self._card_fx_suspended_here:
            self._card_fx_suspended_here = False
            card_resume = getattr(self.card_fx, "resume_from_modal", None)
            if callable(card_resume):
                try:
                    card_resume()
                except RuntimeError:
                    pass
        self._set_widget_lane_enabled(True)
        self._set_legacy_fireworks_enabled(True)

    def _fail_scene(self, reason: str) -> None:
        if self._load_failed:
            return
        self._load_failed = True
        self._quick_requested = False
        self._startup_snapshot_prepared = False
        self._disconnect_handoff()
        self.bridge.set_active(False)
        self.fireworks.clear()
        self._set_native_glass_overlay_alpha(64.0)
        if self.item is not None:
            try:
                self.item.setVisible(False)
            except RuntimeError:
                pass
        try:
            self.shell.set_overlay_presented(True)
        except RuntimeError:
            pass
        self._quick_active = False
        self._resume_legacy_fallback()
        message = reason.strip() or "unknown QML creation failure"
        print(
            "[static-qml] unified Quick scene unavailable; keeping legacy presentation:\n"
            + message,
            file=sys.stderr,
        )

    def _fit(self) -> None:
        width = float(max(1, self.quick.width()))
        height = float(max(1, self.quick.height()))
        if self.item is not None:
            try:
                self.item.setWidth(width)
                self.item.setHeight(height)
            except RuntimeError:
                pass
        try:
            self.fireworks.setWidth(width)
            self.fireworks.setHeight(height)
        except RuntimeError:
            pass

    def _fit_and_refresh(self, *_args: object) -> None:
        self._fit()
        if not self._quick_active:
            self._invalidate_startup_snapshot()
        self.bridge.schedule_refresh()

    def _activate_quick(self) -> None:
        self._quick_requested = True
        if self._quick_active and self.bridge.active:
            return
        if self._load_failed:
            return
        if self.item is None:
            self._ensure_scene_loaded()
            return

        # The normal path snapshots the live QWidget host while both curtains are
        # still closed. If geometry changed after that preparation boundary, the
        # invalidation signal makes this one correctness-preserving refresh run.
        if not self._startup_snapshot_prepared:
            self.bridge.refresh()
        self._startup_snapshot_prepared = False

        self._suspend_legacy_visuals()
        self._fit()
        self._set_native_glass_overlay_alpha(0.0)
        self.bridge.set_active(True)
        try:
            self.item.setVisible(True)
            self.quick.requestUpdate()
        except RuntimeError as exc:
            self._fail_scene(str(exc))
            return

        self._quick_active = True
        self._arm_handoff()

    def _arm_handoff(self) -> None:
        if self._handoff_armed:
            return
        self._handoff_armed = True
        try:
            self.quick.frameSwapped.connect(self._commit_handoff)
        except (RuntimeError, TypeError):
            self._handoff_armed = False
            QTimer.singleShot(0, self._commit_handoff)

    def _disconnect_handoff(self) -> None:
        if not self._handoff_armed:
            return
        self._handoff_armed = False
        try:
            self.quick.frameSwapped.disconnect(self._commit_handoff)
        except (RuntimeError, TypeError):
            pass

    def _commit_handoff(self) -> None:
        self._disconnect_handoff()
        if not self._quick_active or not self._quick_requested:
            return
        try:
            self.shell.set_overlay_presented(False)
        except RuntimeError as exc:
            self._fail_scene(str(exc))

    def _activate_after_startup(self) -> None:
        # Drift state no longer selects a renderer. Quick always owns presentation.
        self._activate_quick()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is not self.quick or not self._quick_active:
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
        self._disconnect_handoff()
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
