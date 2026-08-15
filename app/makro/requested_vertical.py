from __future__ import annotations

import os
import re


REQUESTED_VERTICAL_ENV = "ECOMMERCE_REQUESTED_VERTICAL"
_REQUESTED_VERTICAL_LIMIT = 120


def clean_requested_vertical(value: object) -> str:
    return " ".join(str(value or "").split()).strip()[:_REQUESTED_VERTICAL_LIMIT]


def current_requested_vertical() -> str:
    return clean_requested_vertical(os.getenv(REQUESTED_VERTICAL_ENV, ""))


def requested_vertical_query(value: object) -> str:
    """Convert a canonical slug/display value into a safe Makro search phrase."""

    text = clean_requested_vertical(value)
    text = re.sub(r"[_-]+", " ", text)
    return " ".join(text.split()).strip()


def requested_vertical_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", clean_requested_vertical(value).casefold()).strip()


def requested_vertical_matches_label(requested: object, candidate_label: object) -> bool:
    """Match only an exact full breadcrumb or exact leaf after slug normalization."""

    wanted = requested_vertical_key(requested)
    label = clean_requested_vertical(candidate_label)
    if not wanted or not label:
        return False
    full_key = requested_vertical_key(label)
    parts = [part.strip() for part in label.split("/") if part.strip()]
    leaf_key = requested_vertical_key(parts[-1] if parts else label)
    return wanted in {full_key, leaf_key}


__all__ = [
    "REQUESTED_VERTICAL_ENV",
    "clean_requested_vertical",
    "current_requested_vertical",
    "requested_vertical_matches_label",
    "requested_vertical_query",
]
