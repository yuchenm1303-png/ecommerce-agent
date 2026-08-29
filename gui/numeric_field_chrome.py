from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QBoxLayout,
    QFrame,
    QHBoxLayout,
    QLayout,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QWidget,
)


_FIELD_HEIGHT = 30
_PROMPT_WIDTH = 112
_VALUE_WIDTH = 78
_STEP_BUTTON_WIDTH = 30


def _find_owner_layout(layout: QLayout | None, target: QWidget) -> tuple[QBoxLayout, int] | None:
    if layout is None:
        return None
    for index in range(layout.count()):
        item = layout.itemAt(index)
        if item.widget() is target and isinstance(layout, QBoxLayout):
            return layout, index
        child = item.layout()
        found = _find_owner_layout(child, target)
        if found is not None:
            return found
        widget = item.widget()
        if isinstance(widget, QWidget):
            found = _find_owner_layout(widget.layout(), target)
            if found is not None:
                return found
    return None


def _step(spinbox: QSpinBox, direction: int) -> None:
    if not spinbox.isEnabled():
        return
    spinbox.setValue(spinbox.value() + int(direction) * spinbox.singleStep())


def install_numeric_field_chrome(
    spinbox: QSpinBox | None,
    *,
    prompt: str | None = None,
    prompt_width: int = _PROMPT_WIDTH,
    value_width: int = _VALUE_WIDTH,
) -> QFrame | None:
    """Replace one bare QSpinBox slot with a renderer-neutral numeric field.

    The original QSpinBox remains the sole business-state owner. The surrounding
    prompt and +/- buttons are ordinary Qt widgets, so both the QWidget fallback and
    the StaticQmlBridge see exactly the same presentation structure instead of asking
    QML to reconstruct native QSpinBox prefix/button chrome.
    """

    if not isinstance(spinbox, QSpinBox):
        return None
    existing = getattr(spinbox, "_numeric_field_chrome", None)
    if isinstance(existing, QFrame):
        return existing

    parent = spinbox.parentWidget()
    if not isinstance(parent, QWidget):
        return None
    found = _find_owner_layout(parent.layout(), spinbox)
    if found is None:
        return None
    owner, index = found

    item = owner.itemAt(index)
    alignment = item.alignment() if item is not None else Qt.AlignmentFlag(0)
    stretch = owner.stretch(index)

    prefix = str(prompt if prompt is not None else spinbox.prefix()).strip()
    suffix = str(spinbox.suffix()).strip()
    if not prefix:
        prefix = "数值"

    chrome = QFrame(parent)
    chrome.setObjectName("numericFieldChrome")
    chrome.setFrameShape(QFrame.Shape.NoFrame)
    chrome.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    chrome.setFixedHeight(_FIELD_HEIGHT)

    row = QHBoxLayout(chrome)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(5)

    prompt_box = QLineEdit(chrome)
    prompt_box.setObjectName("numericFieldPrompt")
    prompt_box.setReadOnly(True)
    prompt_box.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    prompt_box.setText(prefix + (f" · {suffix}" if suffix else ""))
    prompt_box.setToolTip(spinbox.toolTip())
    prompt_box.setFixedSize(max(72, int(prompt_width)), _FIELD_HEIGHT)

    owner.removeWidget(spinbox)
    spinbox.setParent(chrome)
    spinbox.setPrefix("")
    spinbox.setSuffix("")
    spinbox.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
    spinbox.setMinimumWidth(max(58, int(value_width)))
    spinbox.setMaximumWidth(max(58, int(value_width)))
    spinbox.setFixedHeight(_FIELD_HEIGHT)

    decrement = QPushButton("−", chrome)
    decrement.setObjectName("quietButton")
    decrement.setToolTip(f"减少 {spinbox.singleStep()}")
    decrement.setFixedSize(_STEP_BUTTON_WIDTH, _FIELD_HEIGHT)
    decrement.clicked.connect(lambda: _step(spinbox, -1))

    increment = QPushButton("+", chrome)
    increment.setObjectName("quietButton")
    increment.setToolTip(f"增加 {spinbox.singleStep()}")
    increment.setFixedSize(_STEP_BUTTON_WIDTH, _FIELD_HEIGHT)
    increment.clicked.connect(lambda: _step(spinbox, 1))

    row.addWidget(prompt_box)
    row.addWidget(spinbox)
    row.addWidget(decrement)
    row.addWidget(increment)

    owner.insertWidget(index, chrome, stretch, alignment)
    chrome.show()
    spinbox.show()

    chrome._numeric_prompt = prompt_box  # type: ignore[attr-defined]
    chrome._numeric_decrement = decrement  # type: ignore[attr-defined]
    chrome._numeric_increment = increment  # type: ignore[attr-defined]
    spinbox._numeric_field_chrome = chrome  # type: ignore[attr-defined]
    return chrome


__all__ = ["install_numeric_field_chrome"]
