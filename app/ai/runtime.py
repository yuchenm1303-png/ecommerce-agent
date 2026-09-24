from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .configuration import AIConfiguration
from .credential_resolver import CredentialResolver
from .openai_runtime import OpenAIChatBackend
from .platform import AIPlatform
from .profiles import ModelProfile
from .provider_catalog import ProviderAdapter, ProviderConnection


ClientFactory = Callable[[ProviderConnection, ModelProfile, str], Any]


def build_ai_platform(
    configuration: AIConfiguration,
    *,
    credential_resolver: CredentialResolver,
    client_factory: ClientFactory | None = None,
    request_timeout_seconds: float = 120.0,
) -> AIPlatform:
    """Build an executable platform from a validated, secret-free configuration.

    Credential values are resolved only here at runtime. They never enter
    ``AIConfiguration`` / ``ModelProfile`` snapshots.
    """

    if not isinstance(configuration, AIConfiguration):
        raise TypeError("configuration must be AIConfiguration")
    if not isinstance(credential_resolver, CredentialResolver):
        raise TypeError("credential_resolver must be CredentialResolver")

    platform = AIPlatform()
    for profile in configuration.profiles.all():
        connection = configuration.providers.require_executable(profile.provider)
        secret = credential_resolver.resolve(connection.credential_ref)
        client = (
            client_factory(connection, profile, secret)
            if client_factory is not None
            else None
        )
        if connection.adapter in {
            ProviderAdapter.OPENAI,
            ProviderAdapter.OPENAI_COMPATIBLE,
        }:
            backend = OpenAIChatBackend(
                connection=connection,
                profile=profile,
                api_key=secret,
                client=client,
                request_timeout_seconds=request_timeout_seconds,
            )
        else:  # pragma: no cover - catalog blocks non-executable adapters today
            raise RuntimeError(
                f"no runtime backend for provider adapter {connection.adapter.value!r}"
            )
        platform.register(profile, backend)
    return platform


__all__ = ["ClientFactory", "build_ai_platform"]
