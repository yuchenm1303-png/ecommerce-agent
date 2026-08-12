from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = (ROOT / "gui" / "restore_snapshot.py").read_text(encoding="utf-8")
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_restore_snapshot_source_compiles_without_importing_pyside() -> None:
    compile(SNAPSHOT, "gui/restore_snapshot.py", "exec")


def test_restore_keeps_objects_alive_and_only_caches_pixels() -> None:
    assert "central.grab()" in SNAPSHOT
    assert "self.snapshot.setPixmap(pixmap)" in SNAPSHOT
    assert "self.snapshot.clear()" in SNAPSHOT
    assert "deleteLater" not in SNAPSHOT
    assert "setParent(None)" not in SNAPSHOT
    assert "QApplication.processEvents" not in SNAPSHOT


def test_snapshot_has_no_background_animation_or_repeating_timer() -> None:
    assert "_RELEASE_DELAY_MS = 48" in SNAPSHOT
    assert "QTimer.singleShot(" in SNAPSHOT
    assert "QTimer(" not in SNAPSHOT
    assert "QPropertyAnimation" not in SNAPSHOT
    assert "QVariantAnimation" not in SNAPSHOT
    assert "fade" not in SNAPSHOT.lower()


def test_restore_snapshot_is_mouse_transparent_and_exact_size() -> None:
    assert "WA_TransparentForMouseEvents" in SNAPSHOT
    assert "WA_TranslucentBackground" in SNAPSHOT
    assert "self.snapshot.setGeometry(self.central.rect())" in SNAPSHOT
    assert "self.snapshot.raise_()" in SNAPSHOT


def test_formal_runner_keeps_snapshot_before_single_window_show() -> None:
    assert "from gui.restore_snapshot import install_restore_snapshot" in RUN
    assert "install_restore_snapshot(window, quick_surface)" in RUN
    assert RUN.index("install_restore_snapshot(window, quick_surface)") < RUN.index(
        "window.showMaximized()"
    )
    assert "install_native_window_shell" not in RUN
