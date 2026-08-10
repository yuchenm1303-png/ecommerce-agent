from __future__ import annotations

from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QFrame, QLabel, QMainWindow

from .card_details_fast import FastCardDetailController


class StaticModalInteractionController(QObject):
    """Keep card-detail interaction inside the existing QWidget surface.

    The shared detail controller already owns the real blurred backdrop, scrim,
    drawer, close button, outside-click close and Escape handling. This adapter
    only makes passive card labels transparent to mouse events so the whole card
    body remains clickable. It deliberately creates no QQuickWindow and performs
    no cross-surface transition.
    """

    def __init__(self, window: QMainWindow, details: FastCardDetailController) -> None:
        super().__init__(window)
        self.window = window
        self.details = details
        self._passive_labels: dict[QLabel, bool] = {}
        self._install_card_surfaces()
        window.destroyed.connect(self.cleanup)

    @staticmethod
    def _label_is_passive(label: QLabel) -> bool:
        flags = label.textInteractionFlags()
        interactive = (
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        return not bool(flags & interactive)

    def _install_card_surfaces(self) -> None:
        for card in self.details._expandable_cards:  # noqa: SLF001
            if not isinstance(card, QFrame):
                continue
            card.setCursor(Qt.CursorShape.PointingHandCursor)
            for label in card.findChildren(QLabel):
                if not self._label_is_passive(label):
                    continue
                previous = label.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                self._passive_labels[label] = previous
                label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def cleanup(self) -> None:
        for label, previous in tuple(self._passive_labels.items()):
            try:
                label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, previous)
            except RuntimeError:
                pass
        self._passive_labels.clear()


def install_static_modal_interaction(
    window: QMainWindow,
    details: FastCardDetailController,
) -> StaticModalInteractionController:
    controller = StaticModalInteractionController(window, details)
    window._static_modal_interaction = controller  # type: ignore[attr-defined]
    return controller
