from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = (ROOT / "gui" / "static_modal_interaction.py").read_text(encoding="utf-8")
DETAILS = (ROOT / "gui" / "card_details_fast.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_runtime_uses_static_widget_modal_not_second_quick_window() -> None:
    assert "from gui.static_modal_interaction import install_static_modal_interaction" in RUNNER
    assert "install_static_modal_interaction(window, details)" in RUNNER
    assert "from gui.modal_interaction import install_modal_interaction" not in RUNNER
    assert "install_modal_interaction(window, details)" not in RUNNER
    assert "modal_overlay_zorder" not in RUNNER
    assert "QQuickWindow(" not in STATIC
    assert "QQmlComponent" not in STATIC
    assert "QQuickItem" not in STATIC


def test_real_modal_stays_in_existing_qwidget_tree() -> None:
    assert "self.backdrop = QLabel(self.root)" in DETAILS
    assert "self.scrim.setGeometry(self.root.rect())" in DETAILS
    assert "self.drawer.setGeometry(self._drawer_rect())" in DETAILS
    assert "self.backdrop.show()" in DETAILS
    assert "self.scrim.show()" in DETAILS
    assert "self.drawer.show()" in DETAILS
    assert "self.drawer.hide()" in DETAILS
    assert "self.scrim.hide()" in DETAILS
    assert "self.backdrop.hide()" in DETAILS
    assert "self.window.hide()" not in DETAILS
    assert "self.window.show()" not in DETAILS


def test_static_modal_keeps_whole_card_body_clickable() -> None:
    assert "def _label_is_passive" in STATIC
    assert "Qt.TextInteractionFlag.TextSelectableByMouse" in STATIC
    assert "Qt.TextInteractionFlag.LinksAccessibleByMouse" in STATIC
    assert "WA_TransparentForMouseEvents" in STATIC
    assert "card.setCursor(Qt.CursorShape.PointingHandCursor)" in STATIC


def test_close_paths_remain_owned_by_real_detail_controller() -> None:
    assert "self.close_button.clicked.connect(self.close)" in (ROOT / "gui" / "card_details.py").read_text(encoding="utf-8")
    assert "self.scrim.clicked.connect(self.close)" in (ROOT / "gui" / "card_details.py").read_text(encoding="utf-8")
    assert "event.key() == Qt.Key.Key_Escape" in DETAILS
    assert "self.close()" in DETAILS


def test_static_modal_adapter_compiles_without_importing_pyside() -> None:
    compile(STATIC, str(ROOT / "gui" / "static_modal_interaction.py"), "exec")
