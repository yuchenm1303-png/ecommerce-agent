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
    QVBoxLayout,
    QWidget,
)


_FIELD_HEIGHT = 30
_PROMPT_WIDTH = 112
_VALUE_WIDTH = 78
_STEPPER_WIDTH = 24
_STEPPER_HALF_HEIGHT = _FIELD_HEIGHT // 2
_STEPPER_GLYPH_PX = 7


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


def _step_button(spinbox: QSpinBox, text: str, direction: int, parent: QWidget) -> QPushButton:
    button = QPushButton(text, parent)
    button.setObjectName("quietButton")
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    button.setToolTip(f"{'增加' if direction > 0 else '减少'} {spinbox.singleStep()}")
    button.setFixedSize(_STEPPER_WIDTH, _STEPPER_HALF_HEIGHT)
    font = button.font()
    font.setPixelSize(_STEPPER_GLYPH_PX)
    button.setFont(font)
    button.clicked.connect(lambda: _step(spinbox, direction))
    return button


def install_numeric_field_chrome(
    spinbox: QSpinBox | None,
    *,
    prompt: str | None = None,
    prompt_width: int = _PROMPT_WIDTH,
    value_width: int = _VALUE_WIDTH,
) -> QFrame | None:
    """Replace one bare QSpinBox slot with a renderer-neutral numeric field.

    The original QSpinBox remains the sole business-state owner. The presentation
    chrome is built from ordinary Qt widgets so QWidget fallback and the Quick mirror
    expose the same prompt, value and compact stacked up/down stepper.
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

    value_cluster = QWidget(chrome)
    value_cluster.setObjectName("numericValueCluster")
    value_cluster.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    value_cluster.setFixedSize(max(58, int(value_width)) + _STEPPER_WIDTH, _FIELD_HEIGHT)
    value_row = QHBoxLayout(value_cluster)
    value_row.setContentsMargins(0, 0, 0, 0)
    value_row.setSpacing(0)

    spinbox.setParent(value_cluster)
    spinbox.setPrefix("")
    spinbox.setSuffix("")
    spinbox.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
    spinbox.setFixedSize(max(58, int(value_width)), _FIELD_HEIGHT)

    stepper = QWidget(value_cluster)
    stepper.setObjectName("numericFieldStepper")
    stepper.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    stepper.setFixedSize(_STEPPER_WIDTH, _FIELD_HEIGHT)
    stepper_layout = QVBoxLayout(stepper)
    stepper_layout.setContentsMargins(0, 0, 0, 0)
    stepper_layout.setSpacing(0)

    increment = _step_button(spinbox, "▲", 1, stepper)
    decrement = _step_button(spinbox, "▼", -1, stepper)
    stepper_layout.addWidget(increment)
    stepper_layout.addWidget(decrement)

    value_row.addWidget(spinbox)
    value_row.addWidget(stepper)
    row.addWidget(prompt_box)
    row.addWidget(value_cluster)

    owner.insertWidget(index, chrome, stretch, alignment)
    chrome.show()
    value_cluster.show()
    spinbox.show()
    stepper.show()

    chrome._numeric_prompt = prompt_box  # type: ignore[attr-defined]
    chrome._numeric_stepper = stepper  # type: ignore[attr-defined]
    chrome._numeric_decrement = decrement  # type: ignore[attr-defined]
    chrome._numeric_increment = increment  # type: ignore[attr-defined]
    spinbox._numeric_field_chrome = chrome  # type: ignore[attr-defined]
    return chrome


__all__ = ["install_numeric_field_chrome"]
