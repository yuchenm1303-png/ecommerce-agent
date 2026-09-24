from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Property, Signal, Qt
from PySide6.QtWidgets import QLabel, QWidget


class _LiveLabelControl(QObject):
    """Stable QObject mirror for QLabel controls in the Quick presentation tree.

    StaticQmlBridge used to snapshot QLabel state into fresh dictionaries. Any
    high-frequency console clock update therefore changed the containing
    ``cardControls`` QVariantList and made QML recreate every delegate in the card.
    Keeping a long-lived QObject per label lets only the changed text/color binding
    update while the phase-strip delegates stay alive.
    """

    changed = Signal()

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self._state: dict[str, Any] = {
            "key": "",
            "name": "",
            "x": 0,
            "y": 0,
            "w": 0,
            "h": 0,
            "enabled": True,
            "clipEnabled": False,
            "clipX": 0,
            "clipY": 0,
            "clipW": 0,
            "clipH": 0,
            "fontFamily": "Microsoft YaHei UI",
            "fontSize": 13,
            "fontWeight": 400,
            "kind": "label",
            "text": "",
            "color": "#ffffffff",
            "wordWrap": False,
            "align": "left",
            "rich": False,
        }

    def _value(self, key: str, default: Any = None) -> Any:
        return self._state.get(key, default)

    key = Property(str, lambda self: str(self._value("key", "")), notify=changed)
    name = Property(str, lambda self: str(self._value("name", "")), notify=changed)
    x = Property(int, lambda self: int(self._value("x", 0)), notify=changed)
    y = Property(int, lambda self: int(self._value("y", 0)), notify=changed)
    w = Property(int, lambda self: int(self._value("w", 0)), notify=changed)
    h = Property(int, lambda self: int(self._value("h", 0)), notify=changed)
    enabled = Property(bool, lambda self: bool(self._value("enabled", True)), notify=changed)
    clipEnabled = Property(bool, lambda self: bool(self._value("clipEnabled", False)), notify=changed)
    clipX = Property(int, lambda self: int(self._value("clipX", 0)), notify=changed)
    clipY = Property(int, lambda self: int(self._value("clipY", 0)), notify=changed)
    clipW = Property(int, lambda self: int(self._value("clipW", 0)), notify=changed)
    clipH = Property(int, lambda self: int(self._value("clipH", 0)), notify=changed)
    fontFamily = Property(str, lambda self: str(self._value("fontFamily", "Microsoft YaHei UI")), notify=changed)
    fontSize = Property(int, lambda self: int(self._value("fontSize", 13)), notify=changed)
    fontWeight = Property(int, lambda self: int(self._value("fontWeight", 400)), notify=changed)
    kind = Property(str, lambda self: str(self._value("kind", "label")), notify=changed)
    text = Property(str, lambda self: str(self._value("text", "")), notify=changed)
    color = Property(str, lambda self: str(self._value("color", "#ffffffff")), notify=changed)
    wordWrap = Property(bool, lambda self: bool(self._value("wordWrap", False)), notify=changed)
    align = Property(str, lambda self: str(self._value("align", "left")), notify=changed)
    rich = Property(bool, lambda self: bool(self._value("rich", False)), notify=changed)

    def sync(self, bridge: Any, widget: QLabel, origin: QWidget) -> bool:
        data = bridge._base(widget, origin)
        if data is None:
            return False

        text = widget.text()
        name = widget.objectName()
        kind = (
            "badge"
            if name in {
                "phaseBadge",
                "appVersionBadge",
                "channelAccountStatusBadge",
                "batchAccountStatusBadge",
            }
            else "label"
        )
        next_state = dict(data)
        next_state.update(
            kind=kind,
            text=text,
            color=bridge._text_color(widget),
            wordWrap=bool(widget.wordWrap()),
            align=bridge._alignment(widget),
            rich=bool(
                widget.textFormat() == Qt.TextFormat.RichText
                or ("<" in text and ">" in text)
            ),
        )
        if next_state == self._state:
            return True
        self._state = next_state
        self.changed.emit()
        return True


def install_static_label_stability() -> None:
    """Keep QLabel identities stable inside the persistent Quick scene.

    This is deliberately a presentation-only patch. Business state, timers and
    phase semantics are untouched; only the mirror representation changes.
    """

    from . import static_qml_bridge as bridge_module

    bridge_cls = bridge_module.StaticQmlBridge
    if getattr(bridge_cls, "_live_label_stability_installed", False):
        return

    original_snapshot = bridge_cls._snapshot_widget
    original_rebuild = bridge_cls._rebuild_structure_cache

    def live_label_control(self: Any, widget: QLabel, origin: QWidget) -> _LiveLabelControl | None:
        pool = getattr(self, "_live_label_controls", None)
        if pool is None:
            pool = {}
            self._live_label_controls = pool
        identity = id(widget)
        control = pool.get(identity)
        if control is None:
            control = _LiveLabelControl(self)
            pool[identity] = control
        return control if control.sync(self, widget, origin) else None

    def snapshot_widget(self: Any, widget: QWidget, origin: QWidget) -> Any:
        if isinstance(widget, QLabel) and self._uses_live_controls(origin):
            if self._has_atomic_ancestor(widget, origin):
                return None
            try:
                return live_label_control(self, widget, origin)
            except RuntimeError:
                return None
        return original_snapshot(self, widget, origin)

    def rebuild_structure_cache(self: Any) -> None:
        original_rebuild(self)
        pool = getattr(self, "_live_label_controls", None)
        if not isinstance(pool, dict) or not pool:
            return
        try:
            live_ids = {id(widget) for widget in self.window.findChildren(QLabel)}
        except RuntimeError:
            live_ids = set()
        for identity, control in tuple(pool.items()):
            if identity in live_ids:
                continue
            pool.pop(identity, None)
            control.deleteLater()

    bridge_cls._snapshot_widget = snapshot_widget
    bridge_cls._rebuild_structure_cache = rebuild_structure_cache
    bridge_cls._live_label_stability_installed = True


__all__ = ["install_static_label_stability"]
