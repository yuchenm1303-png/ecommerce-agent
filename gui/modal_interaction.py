from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QTimer, Qt, QUrl
from PySide6.QtGui import QColor, QKeyEvent, QPixmap
from PySide6.QtQml import QQmlComponent
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtWidgets import QFrame, QLabel, QMainWindow

from .card_details_fast import FastCardDetailController


_OPEN_MS = 235
_CLOSE_MS = 165
_UNDERLAY_SETTLE_FALLBACK_MS = 48
_PANEL_RISE_PX = 18
_PANEL_CLOSE_DROP_PX = 12
_PANEL_OPEN_SCALE = 0.985
_PANEL_CLOSE_SCALE = 0.990

_STATE_CLOSED = "closed"
_STATE_OPENING_PENDING = "opening-pending"
_STATE_OPENING = "opening"
_STATE_OPEN = "open"
_STATE_CLOSING = "closing"


_TRANSITION_QML = rf"""
import QtQuick

Item {{
    id: root
    anchors.fill: parent
    visible: active

    property bool active: false
    property bool closingRequest: false
    property int command: 0
    property url blurUrl
    property url panelUrl
    property real panelX: 0
    property real panelY: 0
    property real panelW: 1
    property real panelH: 1

    signal transitionFinished(bool opened)

    function prepareOpen() {{
        blurImage.opacity = 0.0
        scrim.opacity = 0.0
        panelImage.opacity = 0.0
        panelImage.y = panelY + {_PANEL_RISE_PX}
        panelImage.scale = {_PANEL_OPEN_SCALE}
    }}

    function prepareClose() {{
        blurImage.opacity = 1.0
        scrim.opacity = 1.0
        panelImage.opacity = 1.0
        panelImage.y = panelY
        panelImage.scale = 1.0
    }}

    onActiveChanged: {{
        if (!active)
            return
        if (closingRequest)
            prepareClose()
        else
            prepareOpen()
    }}

    onCommandChanged: {{
        if (command <= 0)
            return
        if (closingRequest)
            closeAnimation.restart()
        else
            openAnimation.restart()
    }}

    Image {{
        id: blurImage
        anchors.fill: parent
        source: root.blurUrl
        cache: false
        asynchronous: false
        smooth: true
        fillMode: Image.Stretch
        opacity: 0.0
    }}

    Rectangle {{
        id: scrim
        anchors.fill: parent
        color: Qt.rgba(12 / 255.0, 17 / 255.0, 26 / 255.0, 94 / 255.0)
        opacity: 0.0
    }}

    Image {{
        id: panelImage
        x: root.panelX
        y: root.panelY
        width: root.panelW
        height: root.panelH
        source: root.panelUrl
        cache: false
        asynchronous: false
        smooth: true
        fillMode: Image.Stretch
        transformOrigin: Item.Center
        opacity: 0.0
    }}

    ParallelAnimation {{
        id: openAnimation
        OpacityAnimator {{
            target: blurImage
            from: 0.0
            to: 1.0
            duration: 190
            easing.type: Easing.OutCubic
        }}
        OpacityAnimator {{
            target: scrim
            from: 0.0
            to: 1.0
            duration: 175
            easing.type: Easing.OutCubic
        }}
        OpacityAnimator {{
            target: panelImage
            from: 0.0
            to: 1.0
            duration: 155
            easing.type: Easing.OutCubic
        }}
        YAnimator {{
            target: panelImage
            from: root.panelY + {_PANEL_RISE_PX}
            to: root.panelY
            duration: {_OPEN_MS}
            easing.type: Easing.OutQuart
        }}
        ScaleAnimator {{
            target: panelImage
            from: {_PANEL_OPEN_SCALE}
            to: 1.0
            duration: {_OPEN_MS}
            easing.type: Easing.OutQuart
        }}
        onFinished: {{
            blurImage.opacity = 1.0
            scrim.opacity = 1.0
            panelImage.opacity = 1.0
            panelImage.y = root.panelY
            panelImage.scale = 1.0
            root.transitionFinished(true)
        }}
    }}

    ParallelAnimation {{
        id: closeAnimation
        OpacityAnimator {{
            target: blurImage
            from: 1.0
            to: 0.0
            duration: 150
            easing.type: Easing.InCubic
        }}
        OpacityAnimator {{
            target: scrim
            from: 1.0
            to: 0.0
            duration: 145
            easing.type: Easing.InCubic
        }}
        OpacityAnimator {{
            target: panelImage
            from: 1.0
            to: 0.0
            duration: 125
            easing.type: Easing.InCubic
        }}
        YAnimator {{
            target: panelImage
            from: root.panelY
            to: root.panelY + {_PANEL_CLOSE_DROP_PX}
            duration: {_CLOSE_MS}
            easing.type: Easing.InCubic
        }}
        ScaleAnimator {{
            target: panelImage
            from: 1.0
            to: {_PANEL_CLOSE_SCALE}
            duration: {_CLOSE_MS}
            easing.type: Easing.InCubic
        }}
        onFinished: root.transitionFinished(false)
    }}
}}
"""


class GlassModalInteractionController(QObject):
    """GPU modal bridge with one permanently mapped transparent Quick overlay.

    The QWidget application remains mapped and owns all real content. A single
    input-transparent QQuickWindow is primed once after the event loop starts and
    then stays transparent above the QWidget child for the application lifetime.
    Opening/closing only toggles scene-graph items inside that already-mapped
    surface, so Windows never has to show/hide/reorder a native transition HWND.

    The underlay is frozen before capture. Only the blurred backdrop and modal
    panel are snapshotted; the live base UI remains visible through the transparent
    overlay. At both handoff edges, QWidget repaint is completed while the final
    Quick frame still covers it, then the Quick item is made transparent.
    """

    def __init__(self, window: QMainWindow, details: FastCardDetailController) -> None:
        super().__init__(window)
        self.window = window
        self.details = details
        self.root = window.centralWidget()
        if self.root is None:
            raise RuntimeError("glass modal interaction requires a central widget")

        visual = getattr(window, "_visual_style", None)
        self.background = getattr(visual, "background", None)
        self.quick_window = getattr(self.background, "quick_window", None)
        self.engine = getattr(self.background, "engine", None)

        self.transition_window: QQuickWindow | None = None
        self.transition_item: QQuickItem | None = None
        self._transition_error = ""
        self._state = _STATE_CLOSED
        self._transitioning = False
        self._target_open = False
        self._command = 0
        self._snapshot_revision = 0
        self._blur_snapshot = QPixmap()
        self._blur_url = QUrl()
        self._passive_labels: dict[QLabel, bool] = {}
        self._prepared_modal = False
        self._pending_ratio: tuple[float, float] | None = None
        self._open_frame_waiting = False
        self._underlay_suspended = False
        self._pointer_timer_was_active = False

        self._original_show_prepared_modal = self.details._show_prepared_modal  # noqa: SLF001
        self.details._show_prepared_modal = self._show_modal_with_transition  # type: ignore[method-assign]  # noqa: SLF001

        self.root.installEventFilter(self)
        self._install_passive_card_surfaces()
        self._rewire_close_inputs()
        window.destroyed.connect(self.cleanup)

        # Startup-safe: this runs only after shell.show() has completed and the
        # event loop begins. The transition surface is mapped once, transparent.
        QTimer.singleShot(0, self._prime_transition_surface)

    @staticmethod
    def _error_text(exc: Exception) -> str:
        text = str(exc).strip()
        return f"{type(exc).__name__}: {text}" if text else type(exc).__name__

    def _prime_transition_surface(self) -> None:
        self._ensure_transition_surface()

    def _sync_transition_window_geometry(self, *_args: object) -> None:
        surface = self.transition_window
        owner = self.quick_window
        if surface is None or owner is None:
            return
        surface.setGeometry(0, 0, max(1, owner.width()), max(1, owner.height()))

    def _deactivate_transition(self) -> None:
        item = self.transition_item
        surface = self.transition_window
        if item is not None:
            try:
                item.setProperty("active", False)
            except RuntimeError:
                pass
        if surface is not None:
            try:
                surface.requestUpdate()
            except RuntimeError:
                pass

    def _clear_snapshots(self) -> None:
        self._blur_snapshot = QPixmap()
        self._blur_url = QUrl()

    def _suspend_underlay(self) -> None:
        if self._underlay_suspended:
            return
        self._underlay_suspended = True

        card_fx = getattr(self.window, "_nekro_card_fx", None)
        suspend_cards = getattr(card_fx, "suspend_for_modal", None)
        if callable(suspend_cards):
            try:
                suspend_cards()
            except RuntimeError:
                pass

        timer = getattr(self.background, "_pointer_timer", None)
        self._pointer_timer_was_active = bool(
            isinstance(timer, QTimer) and timer.isActive()
        )
        if self._pointer_timer_was_active:
            timer.stop()

        quick = self.quick_window
        if quick is not None:
            try:
                quick.setProperty("animationRunning", False)
            except RuntimeError:
                pass

    def _resume_underlay(self) -> None:
        if not self._underlay_suspended:
            return
        self._underlay_suspended = False

        card_fx = getattr(self.window, "_nekro_card_fx", None)
        resume_cards = getattr(card_fx, "resume_from_modal", None)
        if callable(resume_cards):
            try:
                resume_cards()
            except RuntimeError:
                pass

        if self.background is not None:
            try:
                self.background._last_pointer_norm = None  # noqa: SLF001
            except (AttributeError, RuntimeError):
                pass

        timer = getattr(self.background, "_pointer_timer", None)
        if self._pointer_timer_was_active and isinstance(timer, QTimer):
            try:
                timer.start()
            except RuntimeError:
                pass
        self._pointer_timer_was_active = False

    def _disconnect_underlay_frame_wait(self) -> None:
        if not self._open_frame_waiting:
            return
        self._open_frame_waiting = False
        quick = self.quick_window
        if quick is None:
            return
        try:
            quick.frameSwapped.disconnect(self._on_underlay_frame_swapped)
        except (RuntimeError, TypeError):
            pass

    def _on_underlay_frame_swapped(self) -> None:
        if self._state != _STATE_OPENING_PENDING or not self._open_frame_waiting:
            return
        self._disconnect_underlay_frame_wait()
        self._begin_pending_open()

    def _wait_for_stable_underlay_frame(self) -> None:
        quick = self.quick_window
        if quick is None:
            self._begin_pending_open()
            return

        try:
            quick.frameSwapped.connect(
                self._on_underlay_frame_swapped,
                Qt.ConnectionType.QueuedConnection,
            )
            self._open_frame_waiting = True
            quick.requestUpdate()
            QTimer.singleShot(
                _UNDERLAY_SETTLE_FALLBACK_MS,
                self._begin_pending_open,
            )
        except (RuntimeError, TypeError):
            self._disconnect_underlay_frame_wait()
            self._begin_pending_open()

    def _capture_raw_backdrop(self) -> QPixmap:
        screen = self.window.screen()
        pixmap = QPixmap()
        if screen is not None:
            global_pos = self.root.mapToGlobal(QPoint(0, 0))
            screen_origin = screen.geometry().topLeft()
            local_pos = global_pos - screen_origin
            pixmap = screen.grabWindow(
                0,
                local_pos.x(),
                local_pos.y(),
                self.root.width(),
                self.root.height(),
            )
        if pixmap.isNull():
            pixmap = self.root.grab()
        return pixmap

    def _prepare_hidden_modal(
        self,
        *,
        ratio: tuple[float, float],
        blurred: QPixmap,
    ) -> None:
        self.details._modal_ratio = ratio  # noqa: SLF001
        updates_were_enabled = self.root.updatesEnabled()
        if updates_were_enabled:
            self.root.setUpdatesEnabled(False)

        try:
            self.details.backdrop.setPixmap(blurred)
            self.details.backdrop.setGeometry(self.root.rect())
            self.details.scrim.setGeometry(self.root.rect())
            self.details.drawer.setGeometry(self.details._drawer_rect())  # noqa: SLF001
            self.details.body_layout.activate()
            if self.details.drawer.layout() is not None:
                self.details.drawer.layout().activate()
            self.details.scroll.verticalScrollBar().setValue(0)
            self.details.ghost.hide()
            self.details.backdrop.hide()
            self.details.scrim.hide()
            self.details.drawer.hide()
            self._prepared_modal = True
        finally:
            if updates_were_enabled:
                self.root.setUpdatesEnabled(True)

        self.details._schedule_geometry()  # noqa: SLF001

    def _reveal_prepared_modal(self) -> None:
        if not self._prepared_modal:
            return
        updates_were_enabled = self.root.updatesEnabled()
        if updates_were_enabled:
            self.root.setUpdatesEnabled(False)
        try:
            self.details.backdrop.show()
            self.details.backdrop.raise_()
            self.details.scrim.show()
            self.details.scrim.raise_()
            self.details.drawer.show()
            self.details.drawer.raise_()
            self.details.ghost.hide()
        finally:
            if updates_were_enabled:
                self.root.setUpdatesEnabled(True)

        self.details.close_button.setFocus(Qt.FocusReason.OtherFocusReason)
        self.details._schedule_geometry()  # noqa: SLF001
        self._prepared_modal = False

    def _fallback_open(self, exc: Exception | None = None) -> None:
        if exc is not None:
            self._transition_error = self._error_text(exc)
        self._disconnect_underlay_frame_wait()
        self._state = _STATE_OPEN
        self._transitioning = False
        self._target_open = True
        self._pending_ratio = None
        self._reveal_prepared_modal()
        self.root.repaint()
        self._deactivate_transition()

    def _abort_open(self, exc: Exception | None = None) -> None:
        if exc is not None:
            self._transition_error = self._error_text(exc)
        self._disconnect_underlay_frame_wait()
        self._state = _STATE_CLOSED
        self._transitioning = False
        self._target_open = False
        self._pending_ratio = None
        self._prepared_modal = False
        self._deactivate_transition()
        self._clear_snapshots()
        self._resume_underlay()

    def _fallback_closed(self, exc: Exception | None = None) -> None:
        if exc is not None:
            self._transition_error = self._error_text(exc)
        self._state = _STATE_CLOSED
        self._transitioning = False
        self._target_open = False
        try:
            self.details.close()
            self.root.repaint()
        except RuntimeError:
            pass
        self._prepared_modal = False
        self._deactivate_transition()
        self._clear_snapshots()
        self._resume_underlay()

    def _ensure_transition_surface(self) -> bool:
        if self.transition_window is not None and self.transition_item is not None:
            return True
        if self.quick_window is None or self.engine is None:
            self._transition_error = "native Quick renderer unavailable"
            return False

        surface: QQuickWindow | None = None
        component: QQmlComponent | None = None
        obj: QObject | None = None
        try:
            surface = QQuickWindow(self.quick_window)
            surface.setObjectName("glassModalTransitionWindow")
            surface.setFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowDoesNotAcceptFocus
                | Qt.WindowType.WindowTransparentForInput
            )
            surface.setColor(QColor(0, 0, 0, 0))
            surface.setPersistentGraphics(True)
            surface.setPersistentSceneGraph(True)
            surface.setGeometry(
                0,
                0,
                max(1, self.quick_window.width()),
                max(1, self.quick_window.height()),
            )

            component = QQmlComponent(self.engine, self)
            component.setData(
                _TRANSITION_QML.encode("utf-8"),
                QUrl("inline:glass-modal-transition.qml"),
            )
            status = component.status()
            if status != QQmlComponent.Status.Ready:
                errors = "\n".join(error.toString() for error in component.errors())
                self._transition_error = errors or f"QML component status={status}"
                return False

            obj = component.create()
            if not isinstance(obj, QQuickItem):
                self._transition_error = "QML transition root was not a QQuickItem"
                return False

            obj.setParent(self)
            obj.setParentItem(surface.contentItem())
            obj.setProperty("active", False)
            obj.transitionFinished.connect(self._on_transition_finished)  # type: ignore[attr-defined]

            self.transition_window = surface
            self.transition_item = obj
            surface = None
            obj = None

            self.quick_window.widthChanged.connect(self._sync_transition_window_geometry)
            self.quick_window.heightChanged.connect(self._sync_transition_window_geometry)

            # One native show/raise for the entire process lifetime. After this,
            # the window stays mapped and transparent; transitions never touch
            # native visibility or sibling HWND z-order again.
            self.transition_window.show()
            self.transition_window.raise_()
            self.transition_window.requestUpdate()

            self._transition_error = ""
            return True
        except Exception as exc:
            self._transition_error = self._error_text(exc)
            return False
        finally:
            if obj is not None:
                obj.deleteLater()
            if component is not None:
                component.deleteLater()
            if surface is not None:
                surface.hide()
                surface.close()
                surface.deleteLater()

    def _rewire_close_inputs(self) -> None:
        try:
            self.details.close_button.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.details.close_button.clicked.connect(self.request_close)

        try:
            self.details.scrim.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.details.scrim.clicked.connect(self.request_close)

    @staticmethod
    def _label_is_passive(label: QLabel) -> bool:
        flags = label.textInteractionFlags()
        interactive = (
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        return not bool(flags & interactive)

    def _install_passive_card_surfaces(self) -> None:
        for card in self.details._expandable_cards:  # noqa: SLF001
            card.setCursor(Qt.CursorShape.PointingHandCursor)
            for label in card.findChildren(QLabel):
                if not self._label_is_passive(label):
                    continue
                previous = label.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                self._passive_labels[label] = previous
                label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def _snapshot_path(self, stem: str, *, slot: int, alpha: bool = False) -> Path:
        temp_dir = getattr(self.background, "_temp_dir", None)
        if not isinstance(temp_dir, Path):
            raise RuntimeError("native Quick temporary directory is unavailable")
        suffix = ".png" if alpha else ".bmp"
        return temp_dir / f"{stem}_{slot}{suffix}"

    @staticmethod
    def _save_pixmap(path: Path, pixmap: QPixmap, *, alpha: bool = False) -> QUrl:
        fmt = "PNG" if alpha else "BMP"
        if pixmap.isNull() or not pixmap.save(str(path), fmt):
            raise RuntimeError(f"Failed to publish modal transition image: {path.name}")
        return QUrl.fromLocalFile(str(path))

    def _next_snapshot_slot(self) -> int:
        self._snapshot_revision += 1
        return self._snapshot_revision & 1

    def _set_panel_geometry(self) -> None:
        item = self.transition_item
        if item is None:
            return
        target = self.details._drawer_rect()  # noqa: SLF001
        item.setProperty("panelX", float(target.x()))
        item.setProperty("panelY", float(target.y()))
        item.setProperty("panelW", float(target.width()))
        item.setProperty("panelH", float(target.height()))

    def _configure_open_assets(self, blurred: QPixmap, panel: QPixmap) -> None:
        item = self.transition_item
        if item is None:
            return
        slot = self._next_snapshot_slot()
        blur_url = self._save_pixmap(
            self._snapshot_path("modal_blur", slot=slot),
            blurred,
        )
        panel_url = self._save_pixmap(
            self._snapshot_path("modal_panel", slot=slot, alpha=True),
            panel,
            alpha=True,
        )
        self._blur_snapshot = blurred
        self._blur_url = blur_url
        item.setProperty("blurUrl", blur_url)
        item.setProperty("panelUrl", panel_url)
        self._set_panel_geometry()

    def _configure_close_assets(self, panel: QPixmap) -> None:
        item = self.transition_item
        if item is None:
            return
        slot = self._next_snapshot_slot()
        panel_url = self._save_pixmap(
            self._snapshot_path("modal_panel", slot=slot, alpha=True),
            panel,
            alpha=True,
        )
        if self._blur_url.isEmpty():
            blurred = self.details.backdrop.pixmap()
            if blurred.isNull():
                blurred = self.details._blur_pixmap(self._capture_raw_backdrop())  # noqa: SLF001
            blur_url = self._save_pixmap(
                self._snapshot_path("modal_blur", slot=slot),
                blurred,
            )
            self._blur_snapshot = blurred
            self._blur_url = blur_url
            item.setProperty("blurUrl", blur_url)
        item.setProperty("panelUrl", panel_url)
        self._set_panel_geometry()

    def _issue_transition(self, *, closing: bool) -> None:
        item = self.transition_item
        surface = self.transition_window
        if item is None or surface is None:
            return

        item.setProperty("closingRequest", closing)
        item.setProperty("active", True)
        self._command += 1
        item.setProperty("command", self._command)
        surface.requestUpdate()

    def _begin_pending_open(self) -> None:
        if self._state != _STATE_OPENING_PENDING:
            return
        self._disconnect_underlay_frame_wait()
        ratio = self._pending_ratio
        if ratio is None:
            self._abort_open()
            return

        try:
            raw = self._capture_raw_backdrop()
            blurred = self.details._blur_pixmap(raw)  # noqa: SLF001
            self._prepare_hidden_modal(ratio=ratio, blurred=blurred)

            if not self._ensure_transition_surface():
                self._fallback_open()
                return

            panel = self.details.drawer.grab()
            self._configure_open_assets(
                blurred if not blurred.isNull() else raw,
                panel,
            )
            self._pending_ratio = None
            self._state = _STATE_OPENING
            self._transitioning = True
            self._target_open = True
            self._issue_transition(closing=False)
        except Exception as exc:
            if self._prepared_modal:
                self._fallback_open(exc)
                return
            try:
                self._original_show_prepared_modal(ratio=ratio)
                self._pending_ratio = None
                self._state = _STATE_OPEN
                self._target_open = True
                self._transitioning = False
            except Exception:
                self._abort_open(exc)

    def _show_modal_with_transition(self, *, ratio: tuple[float, float]) -> None:
        if self._state != _STATE_CLOSED:
            return

        self._state = _STATE_OPENING_PENDING
        self._transitioning = False
        self._target_open = True
        self._pending_ratio = ratio
        self._suspend_underlay()
        self._wait_for_stable_underlay_frame()

    def request_close(self, *_args: object) -> None:
        if self._state != _STATE_OPEN:
            return
        if self.details.drawer.isHidden() and self.details.scrim.isHidden():
            self._state = _STATE_CLOSED
            self._target_open = False
            self._resume_underlay()
            return

        try:
            if not self._ensure_transition_surface():
                self._fallback_closed()
                return

            panel = self.details.drawer.grab()
            self._configure_close_assets(panel)
            self._state = _STATE_CLOSING
            self._transitioning = True
            self._target_open = False
            self._issue_transition(closing=True)
        except Exception as exc:
            self._fallback_closed(exc)

    def _on_transition_finished(self, opened: bool) -> None:
        if opened:
            if self._state != _STATE_OPENING:
                return
            self._state = _STATE_OPEN
            self._transitioning = False
            self._target_open = True

            # Paint the real modal completely while the final Quick frame is
            # still above it. Only then make the permanent Quick overlay clear.
            self._reveal_prepared_modal()
            self.root.repaint()
            self._deactivate_transition()
            return

        if self._state != _STATE_CLOSING:
            return
        self._state = _STATE_CLOSED
        self._transitioning = False
        self._target_open = False

        # Exact inverse handoff: remove the real modal and synchronously repaint
        # the frozen base UI while the final Quick frame still covers it.
        self.details.close()
        self._prepared_modal = False
        self.root.repaint()
        self._deactivate_transition()
        self._clear_snapshots()
        self._resume_underlay()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self.root and event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            if event.key() == Qt.Key.Key_Escape and self._state == _STATE_OPEN:
                self.request_close()
                return True
        return False

    def cleanup(self) -> None:
        self._disconnect_underlay_frame_wait()
        self._state = _STATE_CLOSED
        self._transitioning = False
        self._target_open = False
        self._pending_ratio = None
        self._prepared_modal = False
        self._deactivate_transition()
        self._resume_underlay()
        try:
            self.root.removeEventFilter(self)
        except RuntimeError:
            pass

        for label, previous in tuple(self._passive_labels.items()):
            try:
                label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, previous)
            except RuntimeError:
                pass
        self._passive_labels.clear()

        try:
            self.details._show_prepared_modal = self._original_show_prepared_modal  # type: ignore[method-assign]  # noqa: SLF001
        except RuntimeError:
            pass

        if self.transition_item is not None:
            try:
                self.transition_item.setProperty("active", False)
                self.transition_item.setParentItem(None)
            except RuntimeError:
                pass
            self.transition_item.deleteLater()
            self.transition_item = None

        if self.transition_window is not None:
            try:
                self.transition_window.hide()
                self.transition_window.releaseResources()
                self.transition_window.close()
            except RuntimeError:
                pass
            self.transition_window.deleteLater()
            self.transition_window = None


def install_modal_interaction(
    window: QMainWindow,
    details: FastCardDetailController,
) -> GlassModalInteractionController:
    controller = GlassModalInteractionController(window, details)
    window._glass_modal_interaction = controller  # type: ignore[attr-defined]
    return controller
