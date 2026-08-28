from __future__ import annotations

import sys
from typing import Any

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, Property, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtQml import QQmlComponent
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtWidgets import QFrame, QMainWindow

from .activity_presence import ActivityPresence
from .static_qml_bridge import StaticCardModel, StaticQmlBridge
from .static_qml_fireworks import StaticQuickFireworks
from .static_qml_scene import STATIC_QML_SOURCE


_STATIC_QML_URL = QUrl("inmemory:/OriginalStaticRoot.qml")
_ACTIVITY_PRESENCE_QML_URL = QUrl("inmemory:/StaticActivityPresence.qml")

_ACTIVITY_PRESENCE_QML = r'''
import QtQuick

Item {
    id: root
    objectName: "staticActivityPresence"
    x: activityPresenceMirror.x
    y: activityPresenceMirror.y
    width: activityPresenceMirror.width
    height: activityPresenceMirror.height
    visible: activityPresenceMirror.sourceVisible && width > 0 && height > 0
    enabled: false
    z: 1000

    function modeColor(mode) {
        if (mode === "PREPARING") return "#9bdcff"
        if (mode === "READY") return "#b8b6ef"
        if (mode === "FILLING" || mode === "COMPLETE") return "#8fe1b9"
        if (mode === "FAILED") return "#f18da0"
        return "#d8e8ff"
    }

    Rectangle {
        x: 0.5
        y: 0.5
        width: Math.max(0, root.width - 1)
        height: Math.max(0, root.height - 1)
        radius: 9
        border.width: 1
        border.color: Qt.rgba(1, 1, 1, 28/255)
        gradient: Gradient {
            GradientStop { position: 0.0; color: Qt.rgba(14/255, 29/255, 50/255, 76/255) }
            GradientStop { position: 1.0; color: Qt.rgba(5/255, 14/255, 29/255, 92/255) }
        }
    }

    Rectangle {
        x: 10.5
        y: 9.5
        width: 7
        height: 7
        radius: 3.5
        color: root.modeColor(activityPresenceMirror.mode)
        opacity: activityPresenceMirror.active ? 0.94 : 0.76
        Behavior on opacity { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }
    }

    Text {
        x: 25
        y: 0
        width: 94
        height: 22
        text: activityPresenceMirror.mode
        color: root.modeColor(activityPresenceMirror.mode)
        font.family: "Microsoft YaHei UI"
        font.pixelSize: 12
        font.weight: Font.Bold
        verticalAlignment: Text.AlignVCenter
        horizontalAlignment: Text.AlignLeft
        elide: Text.ElideRight
    }

    Text {
        x: 114
        y: 0
        width: Math.max(20, root.width - 187)
        height: 22
        text: activityPresenceMirror.detail
        color: Qt.rgba(1, 1, 1, 215/255)
        font.family: "Microsoft YaHei UI"
        font.pixelSize: 11
        verticalAlignment: Text.AlignVCenter
        horizontalAlignment: Text.AlignLeft
        elide: Text.ElideRight
    }

    Text {
        x: Math.max(0, root.width - 66)
        y: 0
        width: 55
        height: 22
        text: String(activityPresenceMirror.percent) + "%"
        color: Qt.rgba(1, 1, 1, 205/255)
        font.family: "Microsoft YaHei UI"
        font.pixelSize: 11
        verticalAlignment: Text.AlignVCenter
        horizontalAlignment: Text.AlignRight
    }

    Text {
        x: 25
        y: 17
        width: Math.max(20, root.width - 37)
        height: 16
        text: activityPresenceMirror.meta
        color: Qt.rgba(218/255, 232/255, 250/255, 150/255)
        font.family: "Microsoft YaHei UI"
        font.pixelSize: 9
        verticalAlignment: Text.AlignVCenter
        horizontalAlignment: Text.AlignLeft
        elide: Text.ElideRight
    }

    Item {
        id: progressTrack
        x: 10
        y: root.height - 5.5
        width: Math.max(1, root.width - 20)
        height: 3

        Rectangle {
            anchors.fill: parent
            radius: 1.5
            color: Qt.rgba(1, 1, 1, 20/255)
        }

        Rectangle {
            width: progressTrack.width * activityPresenceMirror.percent / 100.0
            height: progressTrack.height
            radius: 1.5
            color: root.modeColor(activityPresenceMirror.mode)
            opacity: 0.72
            Behavior on width {
                NumberAnimation {
                    duration: 180
                    easing.type: Easing.OutCubic
                }
            }
        }
    }
}
'''


class StaticActivityPresenceMirror(QObject):
    """Mirror the legacy activity strip as a real child of its Quick card."""

    changed = Signal()

    def __init__(self, window: QMainWindow, quick: QQuickWindow, engine: Any, parent: QObject) -> None:
        super().__init__(parent)
        self.window = window
        self.quick = quick
        self.engine = engine

        controller = getattr(window, "_activity_presence_controller", None)
        candidate = getattr(controller, "widget", None)
        self.widget = candidate if isinstance(candidate, ActivityPresence) else None
        self._card_frame = self._find_card_frame()

        self._x = 0
        self._y = 0
        self._width = 0
        self._height = 0
        self._source_visible = False
        self._mode = "STANDBY"
        self._detail = "等待准备流程"
        self._meta = "总进度 · 等待商品任务"
        self._percent = 0
        self._active = False
        self._item: QQuickItem | None = None
        self._component: QQmlComponent | None = None
        self._host_item: QQuickItem | None = None

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(0)
        self._refresh_timer.timeout.connect(self.refresh)

        self.engine.rootContext().setContextProperty("activityPresenceMirror", self)

        if self.widget is not None:
            self.widget.installEventFilter(self)
            self._bind_runtime_sources()
            self.refresh()

    def _find_card_frame(self) -> QFrame | None:
        widget = self.widget
        visual = getattr(self.window, "_visual_style", None)
        glass = getattr(visual, "_glass", None)
        if widget is None or not isinstance(glass, dict):
            return None
        frames = {frame for frame in glass if isinstance(frame, QFrame)}
        try:
            current = widget.parentWidget()
            while current is not None and current is not self.window:
                if isinstance(current, QFrame) and current in frames:
                    return current
                current = current.parentWidget()
        except RuntimeError:
            return None
        return None

    @staticmethod
    def _descendant_items(root: QQuickItem) -> list[QQuickItem]:
        items: list[QQuickItem] = []
        pending = list(root.childItems())
        while pending:
            item = pending.pop()
            items.append(item)
            try:
                pending.extend(item.childItems())
            except RuntimeError:
                continue
        return items

    def _quick_card_item(self) -> QQuickItem | None:
        host = self._host_item
        frame = self._card_frame
        if host is None or frame is None:
            return None
        try:
            point = frame.mapTo(self.window, QPoint(0, 0))
            expected = (
                float(point.x()),
                float(point.y()),
                float(frame.width()),
                float(frame.height()),
            )
        except RuntimeError:
            return None

        for item in self._descendant_items(host):
            try:
                values = (
                    item.property("cardX"),
                    item.property("cardY"),
                    item.property("cardW"),
                    item.property("cardH"),
                )
                if any(value is None for value in values):
                    continue
                actual = tuple(float(value) for value in values)
            except (RuntimeError, TypeError, ValueError):
                continue
            if all(abs(actual[index] - expected[index]) <= 0.5 for index in range(4)):
                return item
        return None

    def _sync_card_parent(self) -> QMainWindow | QFrame:
        item = self._item
        host = self._host_item
        frame = self._card_frame
        if item is None or host is None or frame is None:
            return self.window

        card_item = self._quick_card_item()
        if card_item is not None:
            try:
                if item.parentItem() is not card_item:
                    item.setParentItem(card_item)
                return frame
            except RuntimeError:
                pass

        try:
            if item.parentItem() is not host:
                item.setParentItem(host)
        except RuntimeError:
            pass
        return self.window

    def _get_x(self) -> int:
        return self._x

    x = Property(int, _get_x, notify=changed)

    def _get_y(self) -> int:
        return self._y

    y = Property(int, _get_y, notify=changed)

    def _get_width(self) -> int:
        return self._width

    width = Property(int, _get_width, notify=changed)

    def _get_height(self) -> int:
        return self._height

    height = Property(int, _get_height, notify=changed)

    def _get_source_visible(self) -> bool:
        return self._source_visible

    sourceVisible = Property(bool, _get_source_visible, notify=changed)

    def _get_mode(self) -> str:
        return self._mode

    mode = Property(str, _get_mode, notify=changed)

    def _get_detail(self) -> str:
        return self._detail

    detail = Property(str, _get_detail, notify=changed)

    def _get_meta(self) -> str:
        return self._meta

    meta = Property(str, _get_meta, notify=changed)

    def _get_percent(self) -> int:
        return self._percent

    percent = Property(int, _get_percent, notify=changed)

    def _get_active(self) -> bool:
        return self._active

    active = Property(bool, _get_active, notify=changed)

    def _connect(self, signal: object) -> None:
        if signal is None or not hasattr(signal, "connect"):
            return
        try:
            signal.connect(self.schedule_refresh)
        except (RuntimeError, TypeError):
            pass

    def _bind_runtime_sources(self) -> None:
        for source_name in ("runner", "execution_runner"):
            source = getattr(self.window, source_name, None)
            for signal_name in (
                "progress_changed",
                "result_updated",
                "running_changed",
                "phase_event",
                "log",
                "completed",
                "failed",
            ):
                self._connect(getattr(source, signal_name, None))

    def attach(self, host_item: QQuickItem) -> None:
        self._host_item = host_item
        if self.widget is None or self._item is not None:
            return
        if self._component is None:
            component = QQmlComponent(self.engine, self)
            self._component = component
            component.statusChanged.connect(self._on_component_status_changed)
            component.setData(_ACTIVITY_PRESENCE_QML.encode("utf-8"), _ACTIVITY_PRESENCE_QML_URL)
        self._advance_component_status(self._component.status())

    def _on_component_status_changed(self, status: QQmlComponent.Status) -> None:
        self._advance_component_status(status)

    def _advance_component_status(self, status: QQmlComponent.Status) -> None:
        component = self._component
        if component is None or self._item is not None:
            return
        if status in (QQmlComponent.Status.Null, QQmlComponent.Status.Loading):
            return
        if status == QQmlComponent.Status.Error:
            errors = "\n".join(error.toString() for error in component.errors())
            raise RuntimeError("ActivityPresence Quick mirror failed: " + errors)
        if status != QQmlComponent.Status.Ready or self._host_item is None:
            return
        created = component.create(self.engine.rootContext())
        if not isinstance(created, QQuickItem):
            if created is not None:
                created.deleteLater()
            raise RuntimeError("ActivityPresence Quick mirror did not create a QQuickItem")
        created.setParent(self)
        created.setParentItem(self._host_item)
        self._item = created
        self.refresh()

    def schedule_refresh(self, *_args: object) -> None:
        if self.widget is None or self._refresh_timer.isActive():
            return
        self._refresh_timer.start()

    def refresh(self) -> None:
        self._refresh_timer.stop()
        widget = self.widget
        if widget is None:
            return
        try:
            origin = self._sync_card_parent()
            point = widget.mapTo(origin, QPoint(0, 0))
            width = max(0, int(widget.width()))
            height = max(0, int(widget.height()))
            source_visible = bool(
                widget.isVisibleTo(self.window)
                and width > 0
                and height > 0
            )
            snapshot = (
                int(point.x()),
                int(point.y()),
                width,
                height,
                source_visible,
                str(widget.mode or "STANDBY").upper(),
                str(widget.detail or "等待任务"),
                str(widget.meta or "总进度 · 0%"),
                max(0, min(100, int(round(float(widget.target_percent))))),
                bool(widget.active),
            )
        except RuntimeError:
            return

        current = (
            self._x,
            self._y,
            self._width,
            self._height,
            self._source_visible,
            self._mode,
            self._detail,
            self._meta,
            self._percent,
            self._active,
        )
        if snapshot == current:
            return

        (
            self._x,
            self._y,
            self._width,
            self._height,
            self._source_visible,
            self._mode,
            self._detail,
            self._meta,
            self._percent,
            self._active,
        ) = snapshot
        self.changed.emit()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self.widget and event.type() in {
            QEvent.Type.Move,
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.Hide,
            QEvent.Type.LayoutRequest,
        }:
            self.schedule_refresh()
        return False

    def cleanup(self) -> None:
        self._refresh_timer.stop()
        widget = self.widget
        self.widget = None
        if widget is not None:
            try:
                widget.removeEventFilter(self)
            except RuntimeError:
                pass
        item = self._item
        self._item = None
        if item is not None:
            try:
                item.setParentItem(None)
                item.deleteLater()
            except RuntimeError:
                pass


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

        self.activity_presence = StaticActivityPresenceMirror(
            window,
            self.quick,
            self.engine,
            self,
        )
        self.bridge.sceneChanged.connect(self.activity_presence.schedule_refresh)

        self.fireworks = StaticQuickFireworks()
        self.fireworks.setParent(self)
        self.fireworks.setParentItem(self.quick.contentItem())
        self.fireworks.setZ(30000.0)

        self.quick.installEventFilter(self)
        self.quick.widthChanged.connect(self._fit_and_refresh)
        self.quick.heightChanged.connect(self._fit_and_refresh)

        handoff_ready = getattr(startup_gate, "handoffReady", None)
        if handoff_ready is None or not hasattr(handoff_ready, "connect"):
            raise RuntimeError("unified Quick view requires explicit startup handoff signal")
        handoff_ready.connect(self._activate_after_startup)
        window.destroyed.connect(self._cleanup)

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
            self.activity_presence.attach(created)
        except RuntimeError as exc:
            try:
                created.deleteLater()
            except RuntimeError:
                pass
            self._fail_scene(str(exc))
            return

        self.item = created
        self._fit()
        if self._quick_requested:
            self._activate_quick()

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
        self.bridge.schedule_refresh()
        self.activity_presence.schedule_refresh()

    def _activate_quick(self) -> None:
        self._quick_requested = True
        if self._quick_active and self.bridge.active:
            return
        if self._load_failed:
            return
        if self.item is None:
            self._ensure_scene_loaded()
            return

        # Snapshot the still-live QWidget business host before hiding its native
        # child. From this point onward only the Quick scene is visible, regardless
        # of whether wallpaper drift is enabled or disabled.
        self.bridge.refresh()
        self.activity_presence.refresh()
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
        self.activity_presence.cleanup()
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
