from __future__ import annotations


class AIPlatformError(RuntimeError):
    """Base error for the detached provider-neutral AI platform."""


class AIConfigurationError(AIPlatformError, ValueError):
    pass


class AICredentialError(AIPlatformError):
    pass


class AITransportError(AIPlatformError):
    pass


class AIResponseError(AIPlatformError):
    pass


__all__ = [
    "AIConfigurationError",
    "AICredentialError",
    "AIPlatformError",
    "AIResponseError",
    "AITransportError",
]
