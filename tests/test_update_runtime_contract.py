from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PATH = ROOT / "gui" / "update_runtime.py"
CORE_PATH = ROOT / "app" / "updater_core.py"
RUNTIME = RUNTIME_PATH.read_text(encoding="utf-8")
CORE = CORE_PATH.read_text(encoding="utf-8")
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_update_runtime_sources_compile() -> None:
    compile(RUNTIME, str(RUNTIME_PATH), "exec")
    compile(CORE, str(CORE_PATH), "exec")
    compile(RUN, str(ROOT / "run_local_gui.py"), "exec")


def test_standalone_updater_refresh_is_content_addressed_and_atomic() -> None:
    assert "hashlib.sha256()" in RUNTIME
    assert "_sha256_file(target) == expected" in RUNTIME
    assert "_sha256_file(tmp) != expected" in RUNTIME
    assert "os.replace(tmp, target)" in RUNTIME
    assert "st_size" not in RUNTIME


def test_update_runtime_shuts_down_only_gui_owned_qprocesses() -> None:
    assert "window.findChildren(QProcess)" in RUNTIME
    assert "process.terminate()" in RUNTIME
    assert "process.kill()" in RUNTIME
    assert "waitForFinished" in RUNTIME
    assert "app.aboutToQuit.connect(_shutdown)" in RUNTIME


def test_standalone_updater_has_deterministic_exit_gates() -> None:
    assert '"ecommerceagent.exe"' in CORE
    assert '["taskkill", "/PID", str(pid), "/T", "/F"]' in CORE
    assert 'RESULT_WORKER_DID_NOT_EXIT = "worker_did_not_exit"' in CORE
    assert "return 6" in CORE
    assert "allow_owned_app_force_close=True" in CORE


def test_update_runtime_is_installed_before_update_checks() -> None:
    assert "from gui.update_runtime import install_update_runtime" in RUN
    assert "install_update_runtime(app, window)" in RUN
    assert RUN.index("install_update_runtime(app, window)") < RUN.index(
        "install_application_updater(window, access_controller=access_controller)"
    )
