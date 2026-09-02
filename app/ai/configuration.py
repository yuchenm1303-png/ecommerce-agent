from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from .capabilities import ModelCapability
from .errors import AIConfigurationError
from .profiles import ModelProfile, ModelRegistry
from .provider_catalog import ProviderCatalog, ProviderConnection
from .roles import ModelRole


@dataclass(frozen=True, slots=True)
class ModelBinding:
    role_id: str
    provider_id: str
    model: str
    capabilities: frozenset[ModelCapability]

    def __post_init__(self) -> None:
        role_id = str(self.role_id or "").strip().casefold()
        provider_id = str(self.provider_id or "").strip().casefold()
        model = str(self.model or "").strip()
        capabilities = frozenset(ModelCapability(value) for value in self.capabilities)
        if not role_id:
            raise ValueError("role_id must not be empty")
        if not provider_id:
            raise ValueError("provider_id must not be empty")
        if not model:
            raise ValueError("model must not be empty")
        if not capabilities:
            raise ValueError("model binding must declare capabilities")
        object.__setattr__(self, "role_id", role_id)
        object.__setattr__(self, "provider_id", provider_id)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "capabilities", capabilities)

    def as_safe_dict(self) -> dict[str, object]:
        return {
            "role_id": self.role_id,
            "provider_id": self.provider_id,
            "model": self.model,
            "capabilities": sorted(value.value for value in self.capabilities),
        }


class AIConfiguration:
    """Validated, secret-free provider/model assignment snapshot."""

    def __init__(
        self,
        *,
        providers: ProviderCatalog,
        profiles: ModelRegistry,
        bindings: tuple[ModelBinding, ...],
    ) -> None:
        self.providers = providers
        self.profiles = profiles
        self.bindings = bindings

    @classmethod
    def build(
        cls,
        *,
        roles: Iterable[ModelRole],
        providers: Iterable[ProviderConnection],
        bindings: Iterable[ModelBinding],
    ) -> "AIConfiguration":
        role_map: dict[str, ModelRole] = {}
        for role in roles:
            if not isinstance(role, ModelRole):
                raise TypeError("roles must contain ModelRole values")
            if role.role_id in role_map:
                raise AIConfigurationError(f"duplicate AI role: {role.role_id}")
            role_map[role.role_id] = role

        catalog = ProviderCatalog()
        for connection in providers:
            catalog.register(connection)

        registry = ModelRegistry()
        normalized_bindings: list[ModelBinding] = []
        bound_roles: set[str] = set()
        for binding in bindings:
            if not isinstance(binding, ModelBinding):
                raise TypeError("bindings must contain ModelBinding values")
            if binding.role_id in bound_roles:
                raise AIConfigurationError(f"duplicate model binding for role: {binding.role_id}")
            try:
                role = role_map[binding.role_id]
            except KeyError as exc:
                raise AIConfigurationError(f"unknown AI role in binding: {binding.role_id}") from exc
            try:
                profile = catalog.bind(
                    role,
                    provider_id=binding.provider_id,
                    model=binding.model,
                    capabilities=binding.capabilities,
                )
            except (KeyError, RuntimeError, ValueError) as exc:
                raise AIConfigurationError(
                    f"invalid model binding for role {binding.role_id!r}: {exc}"
                ) from exc
            registry.register(profile)
            bound_roles.add(binding.role_id)
            normalized_bindings.append(binding)

        return cls(
            providers=catalog,
            profiles=registry,
            bindings=tuple(sorted(normalized_bindings, key=lambda item: item.role_id)),
        )

    def profile_for(self, role_id: str) -> ModelProfile:
        return self.profiles.get(role_id)

    def provider_for(self, role_id: str) -> ProviderConnection:
        profile = self.profile_for(role_id)
        return self.providers.get(profile.provider)

    def as_safe_dict(self) -> dict[str, object]:
        return {
            "providers": [connection.as_safe_dict() for connection in self.providers.all()],
            "bindings": [binding.as_safe_dict() for binding in self.bindings],
            "profiles": [profile.as_safe_dict() for profile in self.profiles.all()],
        }


__all__ = ["AIConfiguration", "ModelBinding"]
