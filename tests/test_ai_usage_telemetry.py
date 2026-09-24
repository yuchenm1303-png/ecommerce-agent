from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from app.providers.usage_telemetry import (
    USAGE_JOURNAL_ENV,
    ensure_usage_journal,
    instrument_openai_client,
    load_run_usage_summary,
    summarize_usage_journal,
    usage_request_context,
)


class _Create:
    def __init__(self, responses):
        self._responses = list(responses)

    def create(self, **_kwargs):
        value = self._responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


class _Client:
    def __init__(self, *, chat_responses=(), response_responses=()):
        self.chat = SimpleNamespace(completions=_Create(chat_responses))
        self.responses = _Create(response_responses)


def _chat_response(request_id: str, prompt: int, completion: int):
    return SimpleNamespace(
        id=request_id,
        usage=SimpleNamespace(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=prompt + completion,
            prompt_tokens_details=SimpleNamespace(cached_tokens=2),
            completion_tokens_details=SimpleNamespace(reasoning_tokens=3),
        ),
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}'))],
    )


def test_usage_journal_counts_physical_retries_without_capturing_prompt(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv(USAGE_JOURNAL_ENV, raising=False)
    journal = ensure_usage_journal(tmp_path)
    client = instrument_openai_client(
        _Client(chat_responses=[_chat_response("r1", 100, 20), _chat_response("r2", 110, 25)]),
        default_model="qwen3.7-max",
    )

    with usage_request_context(
        task="resolve_product_facts",
        provider="openai-compatible-chat-semantic",
        model="qwen3.7-max",
    ):
        client.chat.completions.create(model="qwen3.7-max", messages=[{"role": "user", "content": "SECRET PROMPT"}])
        client.chat.completions.create(model="qwen3.7-max", messages=[{"role": "user", "content": "SECRET PROMPT"}])

    lines = journal.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert "SECRET PROMPT" not in journal.read_text(encoding="utf-8")
    events = [json.loads(line) for line in lines]
    assert [item["attempt"] for item in events] == [1, 2]
    assert len({item["logical_request_id"] for item in events}) == 1
    assert all(item["stage"] == "product_facts" for item in events)

    summary = summarize_usage_journal(journal)
    assert summary["physical_requests"] == 2
    assert summary["logical_requests"] == 1
    assert summary["retry_requests"] == 1
    assert summary["input_tokens"] == 210
    assert summary["output_tokens"] == 45
    assert summary["cached_input_tokens"] == 4
    assert summary["reasoning_tokens"] == 6
    assert summary["usage_reported_requests"] == 2
    assert summary["usage_missing_requests"] == 0
    assert summary["provider_usage_complete"] is True
    assert summary["by_model"]["qwen3.7-max"]["total_tokens"] == 255
    assert summary["by_task"]["resolve_product_facts"]["retry_requests"] == 1
    assert summary["journal"] == "ai-usage.jsonl"
    assert (tmp_path / "ai-usage-summary.json").is_file()


def test_nested_same_operation_context_reuses_logical_request_id(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv(USAGE_JOURNAL_ENV, raising=False)
    journal = ensure_usage_journal(tmp_path)
    client = instrument_openai_client(
        _Client(chat_responses=[_chat_response("r1", 10, 2), _chat_response("r2", 11, 3)]),
        default_model="qwen3.7-plus",
    )
    kwargs = {
        "task": "extract_independent_product_image_evidence",
        "provider": "openai-compatible-chat-semantic",
        "model": "qwen3.7-plus",
    }
    with usage_request_context(**kwargs):
        with usage_request_context(**kwargs):
            client.chat.completions.create(model="qwen3.7-plus")
        with usage_request_context(**kwargs):
            client.chat.completions.create(model="qwen3.7-plus")

    events = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
    assert len({item["logical_request_id"] for item in events}) == 1
    assert [item["attempt"] for item in events] == [1, 2]
    summary = summarize_usage_journal(journal)
    assert summary["by_stage"]["image_evidence"]["logical_requests"] == 1
    assert summary["by_stage"]["image_evidence"]["retry_requests"] == 1


def test_responses_usage_counts_web_search_tool_calls(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv(USAGE_JOURNAL_ENV, raising=False)
    journal = ensure_usage_journal(tmp_path)
    response = SimpleNamespace(
        id="web-1",
        usage=SimpleNamespace(input_tokens=50, output_tokens=10, total_tokens=60),
        output=[
            SimpleNamespace(type="web_search_call"),
            SimpleNamespace(type="web_search_call"),
            SimpleNamespace(type="message"),
        ],
    )
    client = instrument_openai_client(
        _Client(response_responses=[response]),
        default_model="qwen3.7-max",
    )

    with usage_request_context(
        task="web_search",
        provider="dashscope-qwen-responses-web-search",
        model="qwen3.7-max",
    ):
        client.responses.create(model="qwen3.7-max", input="query")

    event = json.loads(journal.read_text(encoding="utf-8").strip())
    assert event["stage"] == "web"
    assert event["api_kind"] == "responses"
    assert event["web_search_calls"] == 2
    assert event["total_tokens"] == 60
    assert event["usage_reported"] is True


def test_failed_sdk_call_marks_usage_incomplete(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv(USAGE_JOURNAL_ENV, raising=False)
    journal = ensure_usage_journal(tmp_path)
    client = instrument_openai_client(
        _Client(chat_responses=[TimeoutError("network timeout")]),
        default_model="qwen3.7-plus",
    )

    with usage_request_context(
        task="infer_grounded_supplier_product_identity",
        provider="openai-compatible-chat-semantic",
        model="qwen3.7-plus",
    ):
        try:
            client.chat.completions.create(model="qwen3.7-plus")
        except TimeoutError:
            pass
        else:
            raise AssertionError("expected timeout")

    event = json.loads(journal.read_text(encoding="utf-8").strip())
    assert event["status"] == "error"
    assert event["error_type"] == "TimeoutError"
    assert event["stage"] == "product_identity"
    assert event["usage_reported"] is False
    summary = summarize_usage_journal(journal)
    assert summary["error_requests"] == 1
    assert summary["usage_missing_requests"] == 1
    assert summary["provider_usage_complete"] is False


def test_load_run_usage_summary_never_exposes_local_path(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv(USAGE_JOURNAL_ENV, raising=False)
    journal = ensure_usage_journal(tmp_path)
    client = instrument_openai_client(
        _Client(chat_responses=[_chat_response("r1", 1, 1)]),
        default_model="qwen3.7-plus",
    )
    with usage_request_context(task="brand_choice", provider="provider", model="qwen3.7-plus"):
        client.chat.completions.create(model="qwen3.7-plus")
    summary = load_run_usage_summary(tmp_path)
    assert summary["journal"] == "ai-usage.jsonl"
    assert str(tmp_path) not in json.dumps(summary)


def test_production_workflows_bind_one_usage_journal_per_run() -> None:
    root = Path(__file__).resolve().parents[1]
    diagnostics = (root / "app" / "workflow_diagnostics.py").read_text(encoding="utf-8")
    one_link = (root / "makro_one_link.py").read_text(encoding="utf-8")
    image = (root / "app" / "image_evidence.py").read_text(encoding="utf-8")
    monitor = (root / "gui" / "batch_link_telemetry.py").read_text(encoding="utf-8")
    assert "from .providers.usage_telemetry import ensure_usage_journal" in diagnostics
    assert "ensure_usage_journal(sink.run_dir)" in diagnostics
    assert "from app.providers.usage_telemetry import ensure_usage_journal" in one_link
    assert "ensure_usage_journal(run_dir)" in one_link
    assert "from .providers.usage_telemetry import usage_request_context" in image
    assert "with usage_request_context(" in image
    assert "from app.providers.usage_telemetry import load_run_usage_summary" in monitor
    assert 'result["ai_usage_summary"]' in monitor
    assert 'safe_result["failure_diagnostic"]["ai_usage_summary"]' in monitor
