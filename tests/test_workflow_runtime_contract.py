from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import makro_one_link
from app import workflow_runtime


def _args(**overrides):
    values = {
        "provider": "openai-compatible",
        "model": "qwen3.7-plus",
        "fact_model": "qwen3.7-max",
        "structured_mode": "json_object",
        "request_timeout_seconds": 120.0,
        "product_url": "https://supplier.example/item/1",
        "source_profile_dir": "browser_profiles/source-edge",
        "source_cdp_port": 9333,
        "source_wait_ms": 1800,
        "source_scroll_wait_ms": 180,
        "source_max_scroll_steps": 120,
        "source_max_visible_text_chars": 120_000,
        "source_cache_dir": "logs/source-cache",
        "source_cache_ttl_seconds": 900,
        "image_batch_size": 3,
        "image_concurrency": 4,
        "local_batch_size": 12,
        "local_concurrency": 4,
        "web_enrich": "auto",
        "web_search_model": "qwen3.7-max",
        "web_batch_size": 5,
        "web_concurrency": 3,
        "semantic_cache_dir": "logs/semantic-cache",
        "api_key_env": "AI_API_KEY",
        "base_url": "https://api.example/v1",
        "web_base_url": "https://api.example/responses",
        "enable_thinking": False,
        "no_semantic_cache": False,
        "profile_dir": "browser_profiles/makro-edge",
        "cdp_port": 9222,
        "scroll_wait_ms": 250,
        "max_scroll_steps": 200,
        "upload_image": [],
        "upload_source_photos": False,
        "include_review_candidates": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_one_link_compatibility_names_delegate_to_runtime_owner() -> None:
    assert makro_one_link._run is workflow_runtime.run_command
    assert makro_one_link._scan_and_write_live_schema is workflow_runtime.scan_and_write_live_schema
    assert makro_one_link._single_run_dir is workflow_runtime.single_run_dir
    resolver_source = inspect.getsource(makro_one_link._resolver_command)
    executor_source = inspect.getsource(makro_one_link._executor_command)
    assert "return build_resolver_command(" in resolver_source
    assert 'script_path=Path(__file__).with_name("makro_resolve_ai.py")' in resolver_source
    assert "return build_executor_command(" in executor_source
    assert 'script_path=Path(__file__).with_name("makro_execute_listing.py")' in executor_source


def test_product_pack_imports_mechanical_helpers_from_runtime_owner() -> None:
    source = (Path(__file__).resolve().parents[1] / "makro_product_pack_workflow.py").read_text(encoding="utf-8")
    assert "from app.workflow_runtime import (" in source
    assert "build_resolver_command as _resolver_command" in source
    assert "run_command as _run" in source
    assert "scan_and_write_live_schema as _scan_and_write_live_schema" in source
    assert "single_run_dir as _single_run_dir" in source
    assert "from makro_one_link import (" not in source


def test_resolver_command_preserves_existing_cli_contract() -> None:
    command = workflow_runtime.build_resolver_command(
        _args(),
        Path("live-schema.json"),
        Path("resolver-root"),
        script_path=Path("makro_resolve_ai.py"),
    )
    assert command[:2] == [workflow_runtime.sys.executable, "makro_resolve_ai.py"]
    expected_pairs = {
        "--provider": "openai-compatible",
        "--model": "qwen3.7-plus",
        "--fact-model": "qwen3.7-max",
        "--live-schema": "live-schema.json",
        "--product-url": "https://supplier.example/item/1",
        "--source-cdp-port": "9333",
        "--local-batch-size": "12",
        "--web-search-model": "qwen3.7-max",
        "--output-dir": "resolver-root",
        "--api-key-env": "AI_API_KEY",
        "--base-url": "https://api.example/v1",
        "--web-base-url": "https://api.example/responses",
    }
    for option, value in expected_pairs.items():
        index = command.index(option)
        assert command[index + 1] == value
    assert "--disable-thinking" in command
    assert "--no-semantic-cache" not in command


def test_executor_command_preserves_rebind_and_upload_dedup_contract() -> None:
    manifest = {
        "primary_product_url": "https://supplier.example/item/1",
        "outputs": {
            "final_decisions": "decision.json",
            "primary_source_snapshot": "snapshot.json",
            "primary_source_product_images": ["a.jpg", "b.jpg"],
            "primary_source_screenshot": "page.png",
        },
    }
    command = workflow_runtime.build_executor_command(
        _args(upload_image=["a.jpg", "a.jpg", "c.jpg"]),
        live_schema=Path("live-schema.json"),
        creation_vertical="Dash Cams",
        resolver_manifest=manifest,
        executor_root=Path("executor-root"),
        script_path=Path("makro_execute_listing.py"),
    )
    assert command[:2] == [workflow_runtime.sys.executable, "makro_execute_listing.py"]
    assert "--all-step3" in command
    assert "--allow-section-save" in command
    assert command.count("--image") == 2
    upload_values = [command[index + 1] for index, item in enumerate(command) if item == "--upload-image"]
    assert upload_values == ["a.jpg", "c.jpg"]


def test_single_run_dir_requires_exactly_one_match(tmp_path: Path) -> None:
    target = tmp_path / "resolve-ai-001"
    target.mkdir()
    assert workflow_runtime.single_run_dir(tmp_path, "resolve-ai-") == target
    (tmp_path / "resolve-ai-002").mkdir()
    try:
        workflow_runtime.single_run_dir(tmp_path, "resolve-ai-")
    except RuntimeError as exc:
        assert "expected exactly one resolve-ai-* directory" in str(exc)
    else:
        raise AssertionError("multiple run directories must fail closed")
