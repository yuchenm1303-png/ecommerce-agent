from __future__ import annotations

import copy
import json
from typing import Any

from .transient_retry import exception_text


_PRODUCT_IDENTITY_TASK = "infer_grounded_supplier_product_identity"
_IMAGE_INSPECTION_MARKERS = (
    "data_inspection_failed",
    "datainspectionfailed",
    "input image data may contain inappropriate content",
)


def _is_image_inspection_rejection(exc: BaseException) -> bool:
    text = exception_text(exc)
    return any(marker in text for marker in _IMAGE_INSPECTION_MARKERS)


def _identity_text_only_request(request_payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Rebind Product Identity to the remaining non-image evidence.

    This is deliberately not an image-moderation bypass. Once a provider rejects
    image input, the rejected images are removed completely and the model may use
    only the already-captured text/table/structured supplier evidence.
    """

    fallback = copy.deepcopy(request_payload)
    sources = [item for item in fallback.get("grounded_sources") or [] if isinstance(item, dict)]
    image_sources = [item for item in sources if str(item.get("kind") or "").casefold() == "image"]
    text_sources = [item for item in sources if str(item.get("kind") or "").casefold() != "image"]
    if not image_sources or not text_sources:
        return fallback, 0

    retained_refs = [
        str(item.get("source_id") or "").strip()
        for item in text_sources
        if str(item.get("source_id") or "").strip()
    ]
    if not retained_refs:
        return fallback, 0

    fallback["grounded_sources"] = text_sources

    context = fallback.get("context")
    if isinstance(context, dict):
        if isinstance(context.get("allowed_evidence_refs"), list):
            context["allowed_evidence_refs"] = retained_refs
        context["visual_evidence_status"] = "provider_rejected_image_input"

    contract = fallback.get("json_contract")
    if isinstance(contract, dict):
        properties = contract.get("properties")
        if isinstance(properties, dict):
            evidence_refs = properties.get("evidence_refs")
            if isinstance(evidence_refs, dict):
                items = evidence_refs.get("items")
                if isinstance(items, dict) and isinstance(items.get("enum"), list):
                    items["enum"] = retained_refs

    prompt = str(fallback.get("prompt_instruction") or "").strip()
    fallback["prompt_instruction"] = (
        prompt
        + ("\n\n" if prompt else "")
        + "The provider rejected the image attachments. Continue from the remaining text, table, "
          "metadata and structured evidence only. Do not infer or cite any visual-only fact."
    )
    return fallback, len(image_sources)


class ProductIdentityInputFallbackProvider:
    """Transparent provider proxy for one narrow provider-side image rejection.

    A deterministic ``data_inspection_failed`` response must never be retried with
    the same multimodal payload. Product Identity can still proceed from the page's
    grounded textual/structured evidence, so this proxy performs exactly one
    text-only retry for that task and no others.
    """

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate

    @property
    def name(self) -> str:
        return str(getattr(self._delegate, "name", "semantic-provider"))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def extract_json(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        task = str(request_payload.get("task") or "").strip()
        try:
            return self._delegate.extract_json(request_payload)
        except Exception as exc:
            if task != _PRODUCT_IDENTITY_TASK or not _is_image_inspection_rejection(exc):
                raise

            fallback, removed_images = _identity_text_only_request(request_payload)
            if removed_images <= 0:
                raise

            remaining_sources = [
                item
                for item in fallback.get("grounded_sources") or []
                if isinstance(item, dict)
            ]
            print(
                "AI_INPUT_FALLBACK "
                + json.dumps(
                    {
                        "task": task,
                        "reason": "data_inspection_failed",
                        "action": "retry_text_only_once",
                        "rejected_image_count": removed_images,
                        "remaining_grounded_sources": len(remaining_sources),
                        "model": str(getattr(self._delegate, "model", "") or ""),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                flush=True,
            )
            return self._delegate.extract_json(fallback)


def with_product_identity_input_fallback(provider: Any) -> Any:
    if isinstance(provider, ProductIdentityInputFallbackProvider):
        return provider
    return ProductIdentityInputFallbackProvider(provider)


__all__ = [
    "ProductIdentityInputFallbackProvider",
    "with_product_identity_input_fallback",
]
