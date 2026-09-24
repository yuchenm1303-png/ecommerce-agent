from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterable
from urllib.parse import urlsplit, urlunsplit

from .capabilities import ModelCapability
from .credentials import CredentialRef
from .profiles import ModelProfile
from .roles import ModelRole


_PROVIDER_ID_RE = re.compile(r"^[a-z][a-z0-9._-]{0,127}$")


class ProviderAdapter(str, Enum):
    """Transport/API family used by one configured AI provider connection.

    Adapter names describe protocol integration only. Business code should depend
    on a ``ModelRole``/``ModelProfile`` and never branch on these values.
    """

    OPENAI = "openai"
    OPENAI_COMPATIBLE = "openai-compatible"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"


class BaseUrlPolicy(str, Enum):
    FORBIDDEN = "forbidden"
    REQUIRED = "required"


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    adapter: ProviderAdapter
    executable: bool
    base_url_policy: BaseUrlPolicy


_PROVIDER_DESCRIPTORS: dict[ProviderAdapter, ProviderDescriptor] = {
    ProviderAdapter.OPENAI: ProviderDescriptor(
        adapter=ProviderAdapter.OPENAI,
        executable=True,
        base_url_policy=BaseUrlPolicy.FORBIDDEN,
    ),
    ProviderAdapter.OPENAI_COMPATIBLE: ProviderDescriptor(
        adapter=ProviderAdapter.OPENAI_COMPATIBLE,
        executable=True,
        base_url_policy=BaseUrlPolicy.REQUIRED,
    ),
    # Reserved extension slots. They are intentionally non-executable until a
    # dedicated adapter is implemented and tested; merely naming a provider must
    # never make the runtime pretend it is supported.
    ProviderAdapter.ANTHROPIC: ProviderDescriptor(
        adapter=ProviderAdapter.ANTHROPIC,
        executable=False,
        base_url_policy=BaseUrlPolicy.FORBIDDEN,
    ),
    ProviderAdapter.GEMINI: ProviderDescriptor(
        adapter=ProviderAdapter.GEMINI,
        executable=False,
        base_url_policy=BaseUrlPolicy.FORBIDDEN,
    ),
}


def provider_descriptor(adapter: ProviderAdapter | str) -> ProviderDescriptor:
    return _PROVIDER_DESCRIPTORS[ProviderAdapter(adapter)]


def _normalize_base_url(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    if not raw:
        return ""
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("provider base_url must be a complete http/https URL")
    if parsed.username or parsed.password:
        raise ValueError("provider base_url must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("provider base_url must not contain query parameters or fragments")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


@dataclass(frozen=True, slots=True)
class ProviderConnection:
    """Non-secret configuration for one concrete model provider account/endpoint."""

    provider_id: str
    adapter: ProviderAdapter
    credential_ref: CredentialRef
    base_url: str = ""
    display_name: str = ""

    def __post_init__(self) -> None:
        provider_id = str(self.provider_id or "").strip().casefold()
        if not _PROVIDER_ID_RE.fullmatch(provider_id):
            raise ValueError(f"invalid provider id: {self.provider_id!r}")
        adapter = ProviderAdapter(self.adapter)
        if not isinstance(self.credential_ref, CredentialRef):
            raise TypeError("credential_ref must be CredentialRef")
        base_url = _normalize_base_url(self.base_url)
        descriptor = provider_descriptor(adapter)
        if descriptor.base_url_policy is BaseUrlPolicy.REQUIRED and not base_url:
            raise ValueError(f"provider adapter {adapter.value!r} requires base_url")
        if descriptor.base_url_policy is BaseUrlPolicy.FORBIDDEN and base_url:
            raise ValueError(f"provider adapter {adapter.value!r} does not accept base_url")
        display_name = str(self.display_name or "").strip() or provider_id
        object.__setattr__(self, "provider_id", provider_id)
        object.__setattr__(self, "adapter", adapter)
        object.__setattr__(self, "base_url", base_url)
        object.__setattr__(self, "display_name", display_name)

    @property
    def executable(self) -> bool:
        return provider_descriptor(self.adapter).executable

    def as_safe_dict(self) -> dict[str, object]:
        return {
            "provider_id": self.provider_id,
            "adapter": self.adapter.value,
            "credential_ref": self.credential_ref.as_safe_dict(),
            "base_url": self.base_url,
            "display_name": self.display_name,
            "executable": self.executable,
        }

    def bind_role(
        self,
        role: ModelRole,
        *,
        model: str,
        capabilities: Iterable[ModelCapability],
    ) -> ModelProfile:
        if not self.executable:
            raise RuntimeError(
                f"provider adapter {self.adapter.value!r} is reserved but not executable"
            )
        return role.bind(
            provider=self.provider_id,
            model=model,
            capabilities=capabilities,
            credential_ref=self.credential_ref,
        )


class ProviderCatalog:
    """Registry of configured provider connections, independent from business roles."""

    def __init__(self) -> None:
        self._connections: dict[str, ProviderConnection] = {}

    def register(self, connection: ProviderConnection) -> None:
        provider_id = connection.provider_id
        if provider_id in self._connections:
            raise ValueError(f"provider connection already registered: {provider_id}")
        self._connections[provider_id] = connection

    def get(self, provider_id: str) -> ProviderConnection:
        key = str(provider_id or "").strip().casefold()
        try:
            return self._connections[key]
        except KeyError as exc:
            raise KeyError(f"unknown provider connection: {key or provider_id!r}") from exc

    def require_executable(self, provider_id: str) -> ProviderConnection:
        connection = self.get(provider_id)
        if not connection.executable:
            raise RuntimeError(
                f"provider adapter {connection.adapter.value!r} is reserved but not executable"
            )
        return connection

    def bind(
        self,
        role: ModelRole,
        *,
        provider_id: str,
        model: str,
        capabilities: Iterable[ModelCapability],
    ) -> ModelProfile:
        connection = self.require_executable(provider_id)
        return connection.bind_role(role, model=model, capabilities=capabilities)

    def all(self) -> tuple[ProviderConnection, ...]:
        return tuple(self._connections[key] for key in sorted(self._connections))


__all__ = [
    "BaseUrlPolicy",
    "ProviderAdapter",
    "ProviderCatalog",
    "ProviderConnection",
    "ProviderDescriptor",
    "provider_descriptor",
]
