from __future__ import annotations

import os
from collections.abc import Callable, Mapping

from .credentials import CredentialRef, CredentialSource
from .errors import AICredentialError


SecretLookup = Callable[[str], str | None]


class CredentialResolver:
    """Resolve credential references without persisting secret material."""

    def __init__(
        self,
        *,
        environment: Mapping[str, str] | None = None,
        keychain_lookup: SecretLookup | None = None,
        runtime_lookup: SecretLookup | None = None,
    ) -> None:
        self._environment = os.environ if environment is None else environment
        self._keychain_lookup = keychain_lookup
        self._runtime_lookup = runtime_lookup

    def resolve(self, credential_ref: CredentialRef) -> str:
        if not isinstance(credential_ref, CredentialRef):
            raise TypeError("credential_ref must be CredentialRef")
        value: str | None
        if credential_ref.source is CredentialSource.ENVIRONMENT:
            value = self._environment.get(credential_ref.name)
        elif credential_ref.source is CredentialSource.OS_KEYCHAIN:
            if self._keychain_lookup is None:
                raise AICredentialError("OS keychain resolver is not configured")
            value = self._keychain_lookup(credential_ref.name)
        elif credential_ref.source is CredentialSource.RUNTIME:
            if self._runtime_lookup is None:
                raise AICredentialError("runtime credential resolver is not configured")
            value = self._runtime_lookup(credential_ref.name)
        else:  # pragma: no cover - enum is closed
            raise AICredentialError("unsupported credential source")

        secret = str(value or "").strip()
        if not secret:
            raise AICredentialError(
                f"credential reference could not be resolved: "
                f"{credential_ref.source.value}/{credential_ref.name}"
            )
        return secret


__all__ = ["CredentialResolver", "SecretLookup"]
