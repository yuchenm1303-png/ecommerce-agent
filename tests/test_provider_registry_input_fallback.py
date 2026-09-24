from __future__ import annotations

import pytest

from app.providers.registry import ProviderConfig, build_semantic_provider


class _FakeChatCompletions:
    def create(self, **kwargs):
        raise AssertionError("network should not be reached in this construction test")


class _FakeChat:
    def __init__(self) -> None:
        self.completions = _FakeChatCompletions()


class _FakeClient:
    def __init__(self) -> None:
        self.chat = _FakeChat()


def test_openai_compatible_registry_installs_product_identity_input_fallback(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.providers.registry._bind_source_page_state_provider",
        lambda provider: provider,
    )
    provider = build_semantic_provider(
        ProviderConfig(
            provider="openai-compatible",
            model="qwen-test",
            api_key_env="TEST_AI_KEY",
            base_url="https://example.invalid/v1",
        ),
        environ={"TEST_AI_KEY": "test-key"},
        client=_FakeClient(),
    )

    # Vertical diagnostics is the outer proxy; the product-identity fallback must
    # still be present immediately inside it so every configured compatible model
    # gets the same deterministic image-inspection recovery path.
    inner = provider._delegate
    assert inner.__class__.__name__ == "ProductIdentityInputFallbackProvider"
    assert inner.model == "qwen-test"
