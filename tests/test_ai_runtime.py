from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.ai import (
    AGENT_FAST_ROLE,
    AIConfiguration,
    AIMessage,
    AIPlatform,
    ChatRequest,
    CredentialRef,
    CredentialResolver,
    LISTING_IDENTITY_ROLE,
    MessageRole,
    ModelBinding,
    ModelCapability,
    ModelProfile,
    OpenAIChatBackend,
    ProviderAdapter,
    ProviderConnection,
    StreamEventKind,
    StructuredOutputMode,
    StructuredRequest,
    ToolDefinition,
    build_ai_platform,
)


class _CreateRecorder:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.response


class _FakeClient:
    def __init__(self, create: _CreateRecorder) -> None:
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def _response(*, text: str = "ok", tool_calls: list[Any] | None = None) -> Any:
    message = SimpleNamespace(content=text, tool_calls=tool_calls or [])
    choice = SimpleNamespace(message=message, finish_reason="stop")
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=4, total_tokens=14)
    return SimpleNamespace(id="resp-1", choices=[choice], usage=usage)


def _connection(
    *,
    provider_id: str = "openai-main",
    adapter: ProviderAdapter = ProviderAdapter.OPENAI,
) -> ProviderConnection:
    return ProviderConnection(
        provider_id=provider_id,
        adapter=adapter,
        credential_ref=CredentialRef.environment("TEST_AI_KEY"),
        base_url=("https://api.vendor.test/v1" if adapter is ProviderAdapter.OPENAI_COMPATIBLE else ""),
    )


def test_configuration_supports_independent_listing_and_agent_models_without_secrets() -> None:
    providers = [
        _connection(provider_id="openai-main"),
        _connection(provider_id="dashscope", adapter=ProviderAdapter.OPENAI_COMPATIBLE),
    ]
    configuration = AIConfiguration.build(
        roles=[LISTING_IDENTITY_ROLE, AGENT_FAST_ROLE],
        providers=providers,
        bindings=[
            ModelBinding(
                role_id="listing.identity",
                provider_id="dashscope",
                model="qwen-listing",
                capabilities=frozenset(
                    {
                        ModelCapability.TEXT,
                        ModelCapability.STRUCTURED_OUTPUT,
                        ModelCapability.VISION,
                    }
                ),
            ),
            ModelBinding(
                role_id="agent.fast",
                provider_id="openai-main",
                model="gpt-agent",
                capabilities=AGENT_FAST_ROLE.required_capabilities,
            ),
        ],
    )

    assert configuration.profile_for("listing.identity").provider == "dashscope"
    assert configuration.profile_for("agent.fast").provider == "openai-main"
    safe = configuration.as_safe_dict()
    assert "qwen-listing" in str(safe)
    assert "TEST_AI_KEY" in str(safe)
    assert "sk-" not in str(safe)


def test_credential_resolver_supports_all_reference_sources_without_persistence() -> None:
    resolver = CredentialResolver(
        environment={"ENV_KEY": "env-secret"},
        keychain_lookup=lambda alias: "keychain-secret" if alias == "ai/openai" else None,
        runtime_lookup=lambda alias: "runtime-secret" if alias == "session/api" else None,
    )

    assert resolver.resolve(CredentialRef.environment("ENV_KEY")) == "env-secret"
    assert resolver.resolve(CredentialRef.os_keychain("ai/openai")) == "keychain-secret"
    assert resolver.resolve(CredentialRef.runtime("session/api")) == "runtime-secret"


def test_openai_runtime_normalizes_text_tools_usage_and_request_shape() -> None:
    raw_tool_call = SimpleNamespace(
        id="call-1",
        function=SimpleNamespace(name="search_products", arguments='{"query":"shampoo"}'),
    )
    recorder = _CreateRecorder(_response(text="", tool_calls=[raw_tool_call]))
    profile = AGENT_FAST_ROLE.bind(
        provider="openai-main",
        model="gpt-agent",
        capabilities=AGENT_FAST_ROLE.required_capabilities,
        credential_ref=CredentialRef.environment("TEST_AI_KEY"),
    )
    backend = OpenAIChatBackend(
        connection=_connection(),
        profile=profile,
        api_key="not-persisted",
        client=_FakeClient(recorder),
    )
    request = ChatRequest(
        messages=(AIMessage(MessageRole.USER, "find shampoo"),),
        tools=(
            ToolDefinition(
                name="search_products",
                description="Search products",
                input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
            ),
        ),
    )

    response = backend.complete(request)

    assert response.tool_calls[0].name == "search_products"
    assert response.tool_calls[0].arguments == {"query": "shampoo"}
    assert response.usage.total_tokens == 14
    assert recorder.calls[0]["model"] == "gpt-agent"
    assert recorder.calls[0]["tools"][0]["function"]["name"] == "search_products"


def test_structured_output_auto_uses_native_schema_and_compatible_json_object() -> None:
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
    request = StructuredRequest(
        chat=ChatRequest(messages=(AIMessage(MessageRole.USER, "return ok"),)),
        json_schema=schema,
        mode=StructuredOutputMode.AUTO,
    )

    native_recorder = _CreateRecorder(_response(text='{"ok":true}'))
    native_profile = LISTING_IDENTITY_ROLE.bind(
        provider="openai-main",
        model="gpt-listing",
        capabilities=LISTING_IDENTITY_ROLE.required_capabilities,
        credential_ref=CredentialRef.environment("TEST_AI_KEY"),
    )
    native = OpenAIChatBackend(
        connection=_connection(),
        profile=native_profile,
        api_key="not-persisted",
        client=_FakeClient(native_recorder),
    )
    assert native.complete_structured(request) == {"ok": True}
    assert native_recorder.calls[0]["response_format"]["type"] == "json_schema"

    compat_recorder = _CreateRecorder(_response(text='{"ok":true}'))
    compat_profile = LISTING_IDENTITY_ROLE.bind(
        provider="dashscope",
        model="qwen-listing",
        capabilities=LISTING_IDENTITY_ROLE.required_capabilities,
        credential_ref=CredentialRef.environment("TEST_AI_KEY"),
    )
    compat = OpenAIChatBackend(
        connection=_connection(provider_id="dashscope", adapter=ProviderAdapter.OPENAI_COMPATIBLE),
        profile=compat_profile,
        api_key="not-persisted",
        client=_FakeClient(compat_recorder),
    )
    assert compat.complete_structured(request) == {"ok": True}
    assert compat_recorder.calls[0]["response_format"] == {"type": "json_object"}
    assert "JSON Schema" in compat_recorder.calls[0]["messages"][0]["content"]


def test_streaming_normalizes_text_tool_deltas_and_completion() -> None:
    chunks = [
        SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="hel", tool_calls=[]), finish_reason=None)]
        ),
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id="call-1",
                                function=SimpleNamespace(name="lookup", arguments='{"id":'),
                            )
                        ],
                    ),
                    finish_reason=None,
                )
            ]
        ),
        SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="lo", tool_calls=[]), finish_reason="stop")]
        ),
    ]
    recorder = _CreateRecorder(chunks)
    profile = AGENT_FAST_ROLE.bind(
        provider="openai-main",
        model="gpt-agent",
        capabilities=AGENT_FAST_ROLE.required_capabilities,
        credential_ref=CredentialRef.environment("TEST_AI_KEY"),
    )
    backend = OpenAIChatBackend(
        connection=_connection(),
        profile=profile,
        api_key="not-persisted",
        client=_FakeClient(recorder),
    )

    events = list(backend.stream(ChatRequest(messages=(AIMessage(MessageRole.USER, "hello"),))))

    assert [event.kind for event in events] == [
        StreamEventKind.TEXT_DELTA,
        StreamEventKind.TOOL_CALL_DELTA,
        StreamEventKind.TEXT_DELTA,
        StreamEventKind.COMPLETED,
    ]
    assert events[1].tool_name == "lookup"
    assert recorder.calls[0]["stream"] is True


def test_platform_fails_closed_when_request_needs_undeclared_tool_capability() -> None:
    profile = ModelProfile(
        profile_id="test.text",
        provider="vendor",
        model="text-model",
        capabilities=frozenset({ModelCapability.TEXT}),
    )
    platform = AIPlatform()
    platform.register(profile, object())
    request = ChatRequest(
        messages=(AIMessage(MessageRole.USER, "use tool"),),
        tools=(ToolDefinition("lookup", "lookup", {"type": "object"}),),
    )

    with pytest.raises(ValueError, match="tool_calling"):
        platform.execute_chat("test.text", request)


def test_runtime_builder_resolves_credentials_and_executes_configured_role() -> None:
    configuration = AIConfiguration.build(
        roles=[AGENT_FAST_ROLE],
        providers=[_connection()],
        bindings=[
            ModelBinding(
                role_id="agent.fast",
                provider_id="openai-main",
                model="gpt-agent",
                capabilities=AGENT_FAST_ROLE.required_capabilities,
            )
        ],
    )
    recorder = _CreateRecorder(_response(text="ready"))
    secrets_seen: list[str] = []

    def client_factory(_connection: Any, _profile: Any, secret: str) -> Any:
        secrets_seen.append(secret)
        return _FakeClient(recorder)

    platform = build_ai_platform(
        configuration,
        credential_resolver=CredentialResolver(environment={"TEST_AI_KEY": "runtime-secret"}),
        client_factory=client_factory,
    )
    response = platform.execute_chat(
        "agent.fast",
        ChatRequest(messages=(AIMessage(MessageRole.USER, "hello"),)),
    )

    assert response.text == "ready"
    assert secrets_seen == ["runtime-secret"]
    assert "runtime-secret" not in str(configuration.as_safe_dict())
