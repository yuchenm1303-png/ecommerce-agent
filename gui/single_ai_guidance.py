from __future__ import annotations

import json
import os
from pathlib import Path
from types import MethodType
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QVBoxLayout

from app.listing_content_policy import (
    LISTING_AI_GUIDANCE_ENV,
    MODEL_NAME_KEYWORDS_ENV,
)
from app.makro.requested_vertical import (
    REQUESTED_VERTICAL_ENV,
    clean_requested_vertical,
)

_GUIDANCE_LIMIT = 1000
_MODEL_KEYWORDS_LIMIT = 500
_SIDECAR = "listing-ai-guidance.json"
_VERTICAL_ORIGIN_PROPERTY = "listingVerticalOrigin"


def _clean(value: object, limit: int) -> str:
    return " ".join(str(value or "").split()).strip()[: max(1, int(limit))]


def _with_single_guidance(
    guidance: str,
    model_keywords: str,
    callback: Any,
    *,
    requested_vertical: str = "",
) -> Any:
    values = {
        LISTING_AI_GUIDANCE_ENV: _clean(guidance, _GUIDANCE_LIMIT),
        MODEL_NAME_KEYWORDS_ENV: _clean(model_keywords, _MODEL_KEYWORDS_LIMIT),
        REQUESTED_VERTICAL_ENV: clean_requested_vertical(requested_vertical),
    }
    sentinel = object()
    previous: dict[str, object] = {
        name: os.environ.get(name, sentinel) for name in values
    }
    try:
        for name, value in values.items():
            if value:
                os.environ[name] = value
            else:
                os.environ.pop(name, None)
        return callback()
    finally:
        for name, value in previous.items():
            if value is sentinel:
                os.environ.pop(name, None)
            else:
                os.environ[name] = str(value)


def _write_sidecar(
    root: Path,
    guidance: str,
    model_keywords: str,
    requested_vertical: str = "",
) -> None:
    target = root.resolve() / _SIDECAR
    payload = {
        "ai_guidance": _clean(guidance, _GUIDANCE_LIMIT),
        "model_name_keywords": _clean(model_keywords, _MODEL_KEYWORDS_LIMIT),
        "requested_vertical": clean_requested_vertical(requested_vertical),
        "source": "user",
        "contract": {
            "ai_guidance": "soft guidance only; may not override grounded product evidence or live Makro constraints",
            "model_name_keywords": "candidate title search terms; use only when relevant and evidence-supported",
            "requested_vertical": "optional exact Makro live Vertical request; blank means normal AI category resolution",
        },
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _install_vertical_input_mode(window: Any) -> None:
    vertical = getattr(window, "vertical_input", None)
    if not isinstance(vertical, QLineEdit):
        return

    vertical.setReadOnly(False)
    vertical.setPlaceholderText("类目（可选 · 留空由 AI 自动选择）")
    vertical.setToolTip(
        "可在任务开始前填写 Makro Vertical 名称或 canonical 值，例如 vehicle_electric_components。"
        "手动填写后，本次任务只会从 Makro 当前真实 live 搜索结果中精确匹配并验证该类目，不再让 AI 改选；"
        "留空则完全使用原来的 AI 自动类目逻辑。任务完成后这里仍会显示最终 Vertical，但显示结果不会自动变成下一任务的指定值。"
    )
    vertical.setProperty(
        _VERTICAL_ORIGIN_PROPERTY,
        "result" if vertical.text().strip() else "empty",
    )

    def mark_user_edit(_text: str) -> None:
        vertical.setProperty(_VERTICAL_ORIGIN_PROPERTY, "user")

    def mark_result(result: Any) -> None:
        if str(getattr(result, "vertical", "") or "").strip():
            vertical.setProperty(_VERTICAL_ORIGIN_PROPERTY, "result")

    vertical.textEdited.connect(mark_user_edit)
    window.runner.result_updated.connect(mark_result)


def _requested_vertical_from_ui(window: Any) -> str:
    vertical = getattr(window, "vertical_input", None)
    if not isinstance(vertical, QLineEdit):
        return ""
    if str(vertical.property(_VERTICAL_ORIGIN_PROPERTY) or "") != "user":
        return ""
    return clean_requested_vertical(vertical.text())


def install_single_ai_guidance(window: Any) -> None:
    """Install Single task guidance, title keywords, and optional Vertical request.

    AI guidance/title keywords remain soft hints. The optional Vertical is different:
    when the user actually edits the Vertical box before launch, Step 1 deterministically
    searches for that exact live Makro category and verifies the committed canonical
    value. An untouched result shown from the previous run is display-only and never
    leaks into the next task.
    """

    if getattr(window, "_single_ai_guidance_installed", False):
        return
    if hasattr(window, "ai_guidance_input") or hasattr(window, "model_name_keywords_input"):
        return

    card = window.url_input.parentWidget()
    layout = card.layout() if card is not None else None
    if not isinstance(layout, QVBoxLayout):
        return

    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)

    guidance_label = QLabel("AI 引导")
    guidance_label.setObjectName("sectionEyebrow")
    guidance_label.setMinimumWidth(58)
    guidance = QLineEdit()
    guidance.setObjectName("listingAiGuidanceInput")
    guidance.setPlaceholderText("例如：突出便携 / 户外场景，不改真实参数")
    guidance.setToolTip(
        "给本次 listing 的 AI 一个额外方向。它只影响证据允许范围内的理解、字段推理和文案表达；"
        "不能覆盖供应商事实、品牌、合规信息或 Makro live schema。"
    )

    keywords_label = QLabel("Model Name 流量词")
    keywords_label.setObjectName("sectionEyebrow")
    keywords_label.setMinimumWidth(118)
    keywords = QLineEdit()
    keywords.setObjectName("modelNameKeywordsInput")
    keywords.setPlaceholderText("例如：inflatable mattress, air bed, camping mattress")
    keywords.setToolTip(
        "仅作为 Model Name 标题的候选搜索词。AI 会筛掉与商品无关或证据不支持的词，"
        "再自然融入标题；不会原样强塞，也不会影响 Brand 等事实字段。"
    )

    row.addWidget(guidance_label, 0, Qt.AlignVCenter)
    row.addWidget(guidance, 1)
    row.addWidget(keywords_label, 0, Qt.AlignVCenter)
    row.addWidget(keywords, 1)

    # listing_offer_support inserts the seller offer row at index 2. Keep this
    # guidance row directly beneath it and above the Step 1/2/3 buttons.
    layout.insertLayout(3, row)
    window.ai_guidance_input = guidance
    window.model_name_keywords_input = keywords
    _install_vertical_input_mode(window)
    window._single_ai_guidance_installed = True

    original_start_mode = window._start_mode

    def start_mode(_window: Any, mode: str) -> None:
        guidance_value = _clean(_window.ai_guidance_input.text(), _GUIDANCE_LIMIT)
        keyword_value = _clean(_window.model_name_keywords_input.text(), _MODEL_KEYWORDS_LIMIT)
        requested_vertical = _requested_vertical_from_ui(_window)
        _with_single_guidance(
            guidance_value,
            keyword_value,
            lambda: original_start_mode(mode),
            requested_vertical=requested_vertical,
        )
        run_dir = getattr(_window.runner, "run_dir", None)
        if isinstance(run_dir, Path) and run_dir.exists():
            _write_sidecar(
                run_dir,
                guidance_value,
                keyword_value,
                requested_vertical,
            )

    window._start_mode = MethodType(start_mode, window)


__all__ = ["install_single_ai_guidance"]
