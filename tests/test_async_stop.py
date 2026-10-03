import os
import subprocess
import sys
import time
from types import SimpleNamespace

from PySide6.QtCore import QProcess, QTimer
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox

from gui.readonly_runner import ReadOnlyRunner
from gui.main_window import MainWindow


def test_real_subprocess_stop_is_nonblocking_and_finishes_once(tmp_path):
    app = QApplication.instance() or QApplication([])
    runner = ReadOnlyRunner(tmp_path)
    runner._start_process(["-u", "-c", "import time; print('ready', flush=True); time.sleep(20)"])
    process = runner.process
    assert process.waitForStarted(2000)
    failures = QSignalSpy(runner.failed)
    ticks = []
    heartbeat = QTimer()
    heartbeat.setInterval(20)
    heartbeat.timeout.connect(lambda: ticks.append(1))
    heartbeat.start()
    started = time.monotonic()
    runner.stop()
    runner.stop()
    assert time.monotonic() - started < 0.5
    deadline = time.monotonic() + 6
    while runner.process is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    heartbeat.stop()
    if process.state() != QProcess.NotRunning:
        process.kill()
        process.waitForFinished(2000)
    assert runner.process is None
    assert failures.count() == 1
    assert ticks
    assert "用户停止" in failures.at(0)[0]


def test_stop_deadline_never_kills_a_replacement_process(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    runner = ReadOnlyRunner(tmp_path)
    calls = []
    callbacks = []
    old = SimpleNamespace(state=lambda: QProcess.Running, terminate=lambda: calls.append("terminate"),
                          kill=lambda: calls.append("kill"))
    runner.process = old
    monkeypatch.setattr(QTimer, "singleShot", lambda delay, owner, callback: callbacks.append(callback))
    runner.stop()
    runner.stop()
    assert calls == ["terminate"]
    old.state = lambda: QProcess.NotRunning
    callbacks[0]()
    assert calls == ["terminate"]
    runner.process = SimpleNamespace(state=lambda: QProcess.Running)
    callbacks[0]()
    assert calls == ["terminate"]
    runner.process = None


def test_user_stop_does_not_open_failure_dialog(monkeypatch):
    app = QApplication.instance() or QApplication([])
    owner = SimpleNamespace(phase_badge=QLabel())
    def unexpected(*args):
        raise AssertionError("Cancellation must not open a modal failure dialog")
    monkeypatch.setattr(QMessageBox, "warning", unexpected)
    MainWindow._run_failed(owner, "测试已由用户停止；浏览器现场保留。")
    assert "已停止" in owner.phase_badge.text()


def test_qml_click_can_destroy_sender_and_open_nested_loop_safely():
    # Isolate a native Qt regression so a future failure cannot abort pytest.
    script = '''
import sys
from PySide6.QtCore import QObject, Slot, QTimer, QUrl, QMetaObject, Qt, QEventLoop, QCoreApplication, QEvent
from PySide6.QtQml import QQmlEngine, QQmlComponent
from PySide6.QtWidgets import QApplication, QPushButton
import shiboken6
from gui.static_qml_bridge import StaticQmlBridge
if sys.platform == "win32":
    import ctypes
    ctypes.windll.kernel32.SetErrorMode(2)
app = QApplication([])
print("APP_READY", flush=True)
button = QPushButton()
calls = []
class Bridge(QObject):
    @Slot(str)
    def click(self, key):
        StaticQmlBridge.click(self, key)
    def _target(self, key):
        return button
    def _dispatch_click(self, target):
        StaticQmlBridge._dispatch_click(self, target)
    def schedule_refresh(self):
        pass
bridge = Bridge()
engine = QQmlEngine()
engine.rootContext().setContextProperty("bridge", bridge)
component = QQmlComponent(engine)
component.setData(b'import QtQml; QtObject { signal fire(); onFire: bridge.click("stop") }', QUrl())
root = component.create()
print("QML_READY", flush=True)
assert root is not None, component.errors()
QQmlEngine.setObjectOwnership(root, QQmlEngine.CppOwnership)
def native_action():
    print("NATIVE_ACTION", flush=True)
    calls.append("clicked")
    root.deleteLater()
    print("SENDER_DELETION_QUEUED", flush=True)
    loop = QEventLoop()
    QTimer.singleShot(20, loop.quit)
    loop.exec()
button.clicked.connect(native_action)
QMetaObject.invokeMethod(root, "fire", Qt.DirectConnection)
print("HANDLER_RETURNED", flush=True)
assert not calls, "Native action ran inside the QML signal handler"
app.processEvents()
assert calls == ["clicked"]
QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
assert not shiboken6.isValid(root)
engine.rootContext().setContextProperty("bridge", None)
component.deleteLater()
engine.deleteLater()
button.deleteLater()
bridge.deleteLater()
app.processEvents()
print("QML_STOP_SAFE")
'''
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    result = subprocess.run([sys.executable, "-u", "-c", script], env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "QML_STOP_SAFE" in result.stdout
