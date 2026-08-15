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

_GUIDANCE_LIMIT = 1000
_MODEL_KEYWORDS_LIMIT = 500
_SIDECAR = "listing-ai-guidance.json"


def _clean(value: object, limit: int) -> str:
    return " ".join(str(value or "").split()).strip()[: max(1, int(limit))]


def _with_single_guidance(guidance: str, model_keywords: str, callback: Any) -> Any:
    values = {
        LISTING_AI_GUIDANCE_ENV: _clean(guidance, _GUIDANCE_LIMIT),
        MODEL_NAME_KEYWORDS_ENV: _clean(model_keywords, _MODEL_KEYWORDS_LIMIT),
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


def _write_sidecar(root: Path, guidance: str, model_keywords: str) -> None:
    target = root.resolve() / _SIDECAR
    payload = {
        "ai_guidance": _clean(guidance, _GUIDANCE_LIMIT),
        "model_name_keywords": _clean(model_keywords, _MODEL_KEYWORDS_LIMIT),
        "source": "user",
        "contract": {
            "ai_guidance": "soft guidance only; may not override grounded product evidence or live Makro constraints",
            "model_name_keywords": "candidate title search terms; use only when relevant and evidence-supported",
        },
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def install_single_ai_guidance(window: Any) -> None:
    """Install Single-only AI guidance and Model Name search-term inputs.

    The values are process-local hints. They never become direct field overrides:
    the worker still has to ground product facts and obey the current live Makro
    schema before Resolver/Fill Plan can use them.
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
    window._single_ai_guidance_installed = True

    original_start_mode = window._start_mode

    def start_mode(_window: Any, mode: str) -> None:
        guidance_value = _clean(_window.ai_guidance_input.text(), _GUIDANCE_LIMIT)
        keyword_value = _clean(_window.model_name_keywords_input.text(), _MODEL_KEYWORDS_LIMIT)
        _with_single_guidance(
            guidance_value,
            keyword_value,
            lambda: original_start_mode(mode),
        )
        run_dir = getattr(_window.runner, "run_dir", None)
        if isinstance(run_dir, Path) and run_dir.exists():
            _write_sidecar(run_dir, guidance_value, keyword_value)

    window._start_mode = MethodType(start_mode, window)


__all__ = ["install_single_ai_guidance"]
