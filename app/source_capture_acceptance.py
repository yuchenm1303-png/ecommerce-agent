from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .source_snapshot import SourceSnapshot


_MIN_TEXT_SIGNAL_CHARS = 1_200
_MIN_TABLE_ROW_SIGNAL = 2
_MIN_IMAGE_SIGNAL = 3
_STRONG_TEXT_CHARS = 3_000
_STRONG_TABLE_ROWS = 8
_STRONG_IMAGE_COUNT = 5
_RICH_PRODUCT_JSONLD_SCORE = 4


@dataclass(slots=True, frozen=True)
class SourceCaptureAcceptance:
    """Mechanical completeness verdict for one captured supplier product state.

    This gate does not judge product semantics. It only prevents a transient SPA
    shell from becoming canonical source truth. A capture must expose more than one
    independent evidence surface, or one clearly strong surface, before downstream
    Resolver/photo selection may consume it.
    """

    ready: bool
    visible_text_chars: int
    table_rows: int
    embedded_data_items: int
    product_images: int
    json_ld_items: int
    rich_product_jsonld: bool
    signal_count: int
    strong_signal: bool
    reason: str

    def describe(self) -> str:
        return (
            f"text={self.visible_text_chars} rows={self.table_rows} "
            f"embedded={self.embedded_data_items} images={self.product_images} "
            f"jsonld={self.json_ld_items} rich_product_jsonld={int(self.rich_product_jsonld)} "
            f"signals={self.signal_count} strong={int(self.strong_signal)}"
        )


def _nonempty(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _product_jsonld_scores(value: Any) -> list[int]:
    scores: list[int] = []

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return

        raw_types = node.get("@type")
        types = raw_types if isinstance(raw_types, list) else [raw_types]
        is_product = any("product" in str(item or "").casefold() for item in types)
        if is_product:
            identity_present = any(
                _nonempty(node.get(key))
                for key in (
                    "sku",
                    "mpn",
                    "model",
                    "productID",
                    "gtin",
                    "gtin8",
                    "gtin12",
                    "gtin13",
                    "gtin14",
                )
            )
            score = sum(
                (
                    _nonempty(node.get("name")),
                    _nonempty(node.get("description")),
                    _nonempty(node.get("brand")),
                    identity_present,
                    _nonempty(node.get("image")) or _nonempty(node.get("images")),
                    _nonempty(node.get("offers")),
                )
            )
            scores.append(int(score))

        for child in node.values():
            if isinstance(child, (dict, list)):
                walk(child)

    walk(value)
    return scores


def assess_source_capture(
    snapshot: SourceSnapshot,
    *,
    product_image_count: int,
) -> SourceCaptureAcceptance:
    """Decide whether a supplier capture is complete enough to become canonical.

    The policy is intentionally domain-agnostic. A short shell with no specs,
    embedded product state and only one or two images is never accepted merely
    because JSON-LD exists. Rich JSON-LD can support one independent live signal,
    but cannot by itself turn a visibly partial SPA state into a successful capture.
    """

    visible_text_chars = len(str(snapshot.visible_text or "").strip())
    table_rows = len(snapshot.table_rows)
    embedded_data_items = len(snapshot.embedded_data)
    product_images = max(0, int(product_image_count))
    json_ld_items = len(snapshot.json_ld)
    json_ld_scores = _product_jsonld_scores(snapshot.json_ld)
    rich_product_jsonld = bool(
        json_ld_scores and max(json_ld_scores) >= _RICH_PRODUCT_JSONLD_SCORE
    )

    live_signals = {
        "text": visible_text_chars >= _MIN_TEXT_SIGNAL_CHARS,
        "table": table_rows >= _MIN_TABLE_ROW_SIGNAL,
        "embedded": embedded_data_items >= 1,
        "images": product_images >= _MIN_IMAGE_SIGNAL,
    }
    signal_count = sum(1 for passed in live_signals.values() if passed)
    strong_signal = bool(
        visible_text_chars >= _STRONG_TEXT_CHARS
        or table_rows >= _STRONG_TABLE_ROWS
        or product_images >= _STRONG_IMAGE_COUNT
    )
    ready = bool(
        strong_signal
        or signal_count >= 2
        or (rich_product_jsonld and signal_count >= 1)
    )

    passed_names = [name for name, passed in live_signals.items() if passed]
    if ready:
        reason = (
            "source evidence is independently sufficient: "
            + (", ".join(passed_names) if passed_names else "strong evidence surface")
        )
    else:
        reason = (
            "transient/partial source shell suspected; insufficient independent evidence "
            f"surfaces ({signal_count}/4 live signals)"
        )

    return SourceCaptureAcceptance(
        ready=ready,
        visible_text_chars=visible_text_chars,
        table_rows=table_rows,
        embedded_data_items=embedded_data_items,
        product_images=product_images,
        json_ld_items=json_ld_items,
        rich_product_jsonld=rich_product_jsonld,
        signal_count=signal_count,
        strong_signal=strong_signal,
        reason=reason,
    )


__all__ = ["SourceCaptureAcceptance", "assess_source_capture"]
