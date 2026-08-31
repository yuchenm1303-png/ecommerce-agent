from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import makro_resolve_ai
from app.image_optimization_gate import (
    IMAGE_OPTIMIZATION_ENV,
    image_optimization_enabled,
    set_image_optimization_enabled,
)


def test_image_optimization_gate_defaults_off_and_requires_explicit_enable() -> None:
    environment: dict[str, str] = {}

    assert image_optimization_enabled(environment) is False

    set_image_optimization_enabled(True, environment)
    assert environment[IMAGE_OPTIMIZATION_ENV] == "1"
    assert image_optimization_enabled(environment) is True

    set_image_optimization_enabled(False, environment)
    assert IMAGE_OPTIMIZATION_ENV not in environment
    assert image_optimization_enabled(environment) is False


def test_resolver_default_off_keeps_legacy_images_without_starting_image_ai(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv(IMAGE_OPTIMIZATION_ENV, raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "makro_resolve_ai.py",
            "--live-schema",
            str(tmp_path / "unused-live-schema.json"),
            "--output-dir",
            str(tmp_path),
        ],
    )
    monkeypatch.setattr(makro_resolve_ai, "run_resolver", lambda _args: 0)

    def unexpected_provider(_config):
        raise AssertionError("default-off image optimization must not build a ranking provider")

    monkeypatch.setattr(makro_resolve_ai, "build_semantic_provider", unexpected_provider)

    assert makro_resolve_ai.main() == 0


def test_explicit_gate_routes_to_new_image_pipeline(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(IMAGE_OPTIMIZATION_ENV, "1")
    run_dir = tmp_path / "resolve-ai-test"
    run_dir.mkdir()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "makro_resolve_ai.py",
            "--live-schema",
            str(tmp_path / "unused-live-schema.json"),
            "--output-dir",
            str(tmp_path),
        ],
    )
    monkeypatch.setattr(makro_resolve_ai, "run_resolver", lambda _args: 0)
    monkeypatch.setattr(makro_resolve_ai, "_new_resolver_run", lambda _root, _before: run_dir)
    monkeypatch.setattr(makro_resolve_ai, "provider_config", lambda _args: object())

    provider = object()
    monkeypatch.setattr(makro_resolve_ai, "build_semantic_provider", lambda _config: provider)
    monkeypatch.setattr(makro_resolve_ai, "set_progress", lambda _provider, _prefix: None)

    calls: list[tuple[Path, object]] = []

    def finalize(target_run_dir: Path, target_provider: object):
        calls.append((target_run_dir, target_provider))
        return SimpleNamespace(
            status="ai_ranked",
            selected=(run_dir / "selected.jpg",),
            model_calls=4,
            mechanical_candidate_count=15,
            semantically_rejected_count=14,
        )

    monkeypatch.setattr(makro_resolve_ai, "finalize_supplier_listing_images", finalize)

    assert makro_resolve_ai.main() == 0
    assert calls == [(run_dir, provider)]


def test_gui_exposes_but_keeps_image_optimization_locked_by_default() -> None:
    source = Path("gui/settings_modal_surface.py").read_text(encoding="utf-8")

    assert '("图像优化", optimization_row)' in source
    assert "_IMAGE_OPTIMIZATION_GUI_UNLOCKED = False" in source
    assert "set_image_optimization_enabled(False)" in source
    assert "self.image_optimization.setEnabled(_IMAGE_OPTIMIZATION_GUI_UNLOCKED)" in source
    assert "默认关闭 · 高负载 · 当前暂未开放" in source
