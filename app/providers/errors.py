from __future__ import annotations


class JSONTaskProviderError(RuntimeError):
    """Base error for a JSON-task provider adapter."""


class JSONTaskTransportError(JSONTaskProviderError):
    """The remote request failed before a usable model response was obtained."""


class JSONTaskResponseError(JSONTaskProviderError):
    """The model responded, but the response could not satisfy the JSON task contract."""
