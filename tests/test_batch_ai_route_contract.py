"""Regression tests for Qwen/relay selection across Batch/Single/Source lanes."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app import ai_runtime_binding as runtime


ROOT = Path(__file__).resolve().parents[1]


class FakeProcessEnvironment:
    def __init__(self, values: dict[str, str]):
        self.values = dict(values)

    def remove(self, name: str) -> None:
        self.values.pop(name, None)

    def insert(self, name: str, value: str) -> None:
        self.values[name] = value


def config():
    return SimpleNamespace(
        provider="openai-compatible",
        base_url="",
        local_model="",
        fact_model="",
        web_model="",
        api_key_env="AI_API_KEY",
        ai_source="",
        runtime_ai_env={},
    )


@pytest.fixture
def clean_runtime(monkeypatch):
    for name in runtime._AI_CHILD_ENV_NAMES:
        monkeypatch.setenv(name, "prior-" + name)


def test_qwen_direct_remains_independent_and_clears_relay_web(clean_runtime, monkeypatch):
    monkeypatch.setattr(runtime, "load_active_ai_source", lambda: "qwen")
    settings = SimpleNamespace(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen3.7-plus",
        fact_model="qwen-fact",
        web_model="qwen-web",
    )
    monkeypatch.setattr(runtime, "load_qwen_profile", lambda: (settings, "qwen-secret"))
    cfg = runtime.apply_active_ai_runtime(config())

    assert cfg.ai_source == "qwen"
    assert cfg.local_model == "qwen3.7-plus"
    assert cfg.base_url == settings.base_url
    assert cfg.runtime_ai_env == {"AI_API_KEY": "qwen-secret"}


def test_relay_semantic_fact_and_web_use_independent_verified_connections(
    clean_runtime, monkeypatch, tmp_path
):
    monkeypatch.setattr(runtime, "load_active_ai_source", lambda: "relay")
    roles = {
        "semantic": ("relay-1", "model-sem"),
        "fact": ("relay-2", "model-fact"),
        "web": ("relay-3", "model-web"),
    }
    urls = {
        "relay-1": "https://sem.example/v1",
        "relay-2": "https://fact.example/v1",
        "relay-3": "https://web.example/v1",
    }
    keys = {
        "relay-1": "key-sem",
        "relay-2": "key-fact",
        "relay-3": "key-web",
    }
    class Pool:
        def binding_for(self, role):
            connection_id, model = roles[role]
            return SimpleNamespace(connection_id=connection_id, model=model)

        def connection(self, connection_id):
            return SimpleNamespace(base_url=urls[connection_id])

    monkeypatch.setattr(runtime, "load_relay_pool", lambda: Pool())
    monkeypatch.setattr(runtime, "load_relay_connection_key", lambda cid: keys[cid])
    monkeypatch.setattr(runtime, "relay_verification_directory", lambda: tmp_path)
    monkeypatch.setattr(runtime, "assert_verified_if_managed", lambda **kw: True)

    cfg = runtime.apply_active_ai_runtime(config())
    assert (cfg.ai_source, cfg.local_model, cfg.fact_model, cfg.web_model) == (
        "relay", "model-sem", "model-fact", "model-web"
    )
    assert cfg.runtime_ai_env == {
        "AI_API_KEY": "key-sem",
        runtime.RUNTIME_FACT_BASE_URL_ENV: urls["relay-2"],
        runtime.RUNTIME_FACT_KEY_ENV: "key-fact",
        runtime.RUNTIME_WEB_BASE_URL_ENV: urls["relay-3"],
        runtime.RUNTIME_WEB_KEY_ENV: "key-web",
    }

    inherited = {name: "wrong-other-account" for name in runtime._AI_CHILD_ENV_NAMES}
    env = FakeProcessEnvironment(inherited)
    runtime.apply_child_ai_environment(env, cfg)
    assert env.values == cfg.runtime_ai_env

    # Later source/profile switches do not mutate this account lane's key snapshot.
    monkeypatch.setattr(runtime, "load_active_ai_source", lambda: "qwen")
    monkeypatch.setattr(
        runtime,
        "load_qwen_profile",
        lambda: (
            SimpleNamespace(
                base_url="https://qwen.example/v1",
                model="qwen-new",
                fact_model="qwen-fact",
                web_model="qwen-web",
            ),
            "qwen-secret",
        ),
    )
    runtime.apply_active_ai_runtime(config())
    env2 = FakeProcessEnvironment({"AI_API_KEY": "new-global-qwen-secret"})
    runtime.apply_child_ai_environment(env2, cfg)
    assert env2.values["AI_API_KEY"] == "key-sem"
    assert env2.values[runtime.RUNTIME_WEB_KEY_ENV] == "key-web"


def test_unverified_relay_fails_closed_without_qwen_fallback(clean_runtime, monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "load_active_ai_source", lambda: "relay")
    class Pool:
        def binding_for(self, role):
            return SimpleNamespace(connection_id="relay-1", model="relay-model")

        def connection(self, _cid):
            return SimpleNamespace(base_url="https://relay.example/v1")

    monkeypatch.setattr(runtime, "load_relay_pool", lambda: Pool())
    monkeypatch.setattr(runtime, "load_relay_connection_key", lambda _cid: "key")
    monkeypatch.setattr(runtime, "relay_verification_directory", lambda: tmp_path)
    monkeypatch.setattr(runtime, "assert_verified_if_managed", lambda **kw: False)
    with pytest.raises(runtime.CapabilityProbeError, match="能力验证"):
        runtime.apply_active_ai_runtime(config())


def test_batch_source_cli_never_falls_back_to_qwen():
    batch = (ROOT / "gui/batch_runner.py").read_text(encoding="utf-8")
    source = (ROOT / "makro_batch_source.py").read_text(encoding="utf-8")
    single = (ROOT / "gui/readonly_runner.py").read_text(encoding="utf-8")
    individual = (ROOT / "gui/batch_individual_controls.py").read_text(encoding="utf-8")
    recovered = (ROOT / "gui/batch_parallel_runtime.py").read_text(encoding="utf-8")

    start = batch.split("    def _start_source(", 1)[1].split("    def _start_prepare_job(", 1)[0]
    for option, attribute in (
        ("--page-state-provider", "provider"),
        ("--page-state-base-url", "base_url"),
        ("--page-state-model", "local_model"),
        ("--page-state-api-key-env", "api_key_env"),
    ):
        assert f'"{option}", self.config.{attribute}' in start
    assert "_DEFAULT_PAGE_STATE_MODEL" not in source
    for name in ("--page-state-model", "--page-state-base-url", "--page-state-api-key-env"):
        assert f'parser.add_argument("{name}", required=True)' in source
    assert "apply_child_ai_environment(environment, self.config)" in single
    assert "apply_child_ai_environment(environment, self.config)" in batch
    assert "apply_active_ai_runtime(config)" in individual
    assert "apply_active_ai_runtime(config)" in recovered
    assert 'kind == "AI_AUTHENTICATION_FAILED"' in batch


def test_route_fingerprint_ignores_credentials_but_detects_source_switch():
    relay = config()
    relay.ai_source = "relay"
    relay.base_url = "https://relay.example/v1"
    relay.local_model = "gpt-custom"
    relay.fact_model = "model-fact"
    relay.web_model = "model-web"
    relay.runtime_ai_env = {
        "AI_API_KEY": "first-key",
        runtime.RUNTIME_WEB_BASE_URL_ENV: "https://web.example/v1",
        runtime.RUNTIME_WEB_KEY_ENV: "first-web-key",
    }
    signature = runtime.ai_route_fingerprint(relay)
    assert len(signature) == 64
    assert "first-key" not in signature
    relay.runtime_ai_env["AI_API_KEY"] = "rotated-key"
    relay.runtime_ai_env[runtime.RUNTIME_WEB_KEY_ENV] = "rotated-web-key"
    assert runtime.ai_route_fingerprint(relay) == signature
    relay.ai_source = "qwen"
    assert runtime.ai_route_fingerprint(relay) != signature


def test_batch_metadata_persists_ai_route_without_any_api_key(tmp_path):
    from gui.batch_model import create_batch_run, load_batch_run, save_batch_run

    batch = create_batch_run(tmp_path, ["https://example.org/product"])
    batch.ai_route_fingerprint = "ab" * 32
    save_batch_run(batch)
    stored = (tmp_path / "logs" / "batch-runs" / batch.batch_id / "batch.json").read_text(
        encoding="utf-8"
    )
    assert "ab" * 32 in stored
    assert "AI_API_KEY" not in stored
    assert load_batch_run(batch.root_dir).ai_route_fingerprint == "ab" * 32
