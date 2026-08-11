from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACTIVITY = (ROOT / "gui" / "activity_presence.py").read_text(encoding="utf-8")
BOOT = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_activity_presence_is_installed_into_formal_gui() -> None:
    assert "from gui.activity_presence import install_activity_presence" in BOOT
    assert "install_activity_presence(window)" in BOOT


def test_activity_presence_uses_real_runner_progress_not_fake_percent_growth() -> None:
    assert "prep.progress_changed.connect(self._on_prep_progress)" in ACTIVITY
    assert "prep.phase_event.connect(self._on_phase_event)" in ACTIVITY
    assert "real.progress_changed.connect(self._on_real_progress)" in ACTIVITY
    assert "self.percent = max(0, min(100, int(percent)))" in ACTIVITY
    assert "self._sweep = (self._sweep + 0.045) % 1.0" in ACTIVITY
    assert "self.percent +=" not in ACTIVITY
    assert "self.percent = (self.percent" not in ACTIVITY


def test_activity_presence_animation_is_local_and_stops_when_idle() -> None:
    assert "self.setFixedHeight(30)" in ACTIVITY
    assert "self._timer.setInterval(90)" in ACTIVITY
    assert "self.update()" in ACTIVITY
    assert "window.update()" not in ACTIVITY
    assert "self._timer.stop()" in ACTIVITY
    assert "WA_TransparentForMouseEvents" in ACTIVITY


def test_activity_presence_covers_preparation_and_real_execution_states() -> None:
    for state in ("STANDBY", "PREPARING", "READY", "FILLING", "COMPLETE", "FAILED"):
        assert f'"{state}"' in ACTIVITY
    assert '"scan": "Source Capture"' in ACTIVITY
    assert '"plan": "Step 3 · Resolve / Fill Plan"' in ACTIVITY
    assert "photo_upload" in ACTIVITY
    assert "QC locked" in ACTIVITY
