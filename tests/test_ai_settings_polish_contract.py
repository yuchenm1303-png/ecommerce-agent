from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_ai_settings_entry_uses_polished_surface() -> None:
    source = (ROOT / "gui" / "settings_modal_surface.py").read_text(encoding="utf-8")
    assert "from .ai_settings_polished_surface import" in source


def test_ai_settings_polish_prevents_vertical_stretch() -> None:
    source = (ROOT / "gui" / "ai_settings_polished_surface.py").read_text(encoding="utf-8")
    assert "QSizePolicy.Policy.Maximum" in source
    assert "root.setAlignment(Qt.AlignmentFlag.AlignTop)" in source
    assert "body_layout.addWidget(panel, 0, Qt.AlignmentFlag.AlignTop)" in source


def test_ai_settings_controls_keep_compact_height() -> None:
    source = (ROOT / "gui" / "ai_settings_polished_surface.py").read_text(encoding="utf-8")
    assert "editor.setMaximumHeight(36)" in source
    assert "combo.setMaximumHeight(36)" in source
    assert "button.setMaximumHeight(34)" in source
