"""Native always-on-top progress panel used after the Qt GUI hands off update ownership."""
from __future__ import annotations

import ctypes
import sys
import threading
import time
from pathlib import Path


class NativeUpdatePanel:
    _WS_POPUP = 0x80000000
    _WS_CAPTION = 0x00C00000
    _WS_CHILD = 0x40000000
    _WS_VISIBLE = 0x10000000
    _WS_EX_TOPMOST = 0x00000008
    _WS_EX_TOOLWINDOW = 0x00000080
    _PBS_MARQUEE = 0x00000008
    _WM_USER = 0x0400
    _PBM_SETMARQUEE = _WM_USER + 10
    _WM_SETFONT = 0x0030
    _DEFAULT_GUI_FONT = 17
    _PM_REMOVE = 0x0001

    def __init__(self, target_version: str, log_path: str | Path | None = None) -> None:
        self.target_version = str(target_version or "").strip().lstrip("v")
        self.log_path = Path(log_path) if log_path else None
        self._initialized = threading.Event()
        self._stop = threading.Event()
        self._created = not (sys.platform == "win32" and bool(getattr(sys, "frozen", False)))
        self._hwnd = 0
        self._phase = 0
        self._detail = 0
        self._ui_thread: threading.Thread | None = None
        self._monitor_thread: threading.Thread | None = None
        if sys.platform == "win32" and bool(getattr(sys, "frozen", False)):
            self._ui_thread = threading.Thread(target=self._run_ui, daemon=True)
            self._ui_thread.start()
            self._initialized.wait(3.0)
            if self._created and self.log_path:
                self._monitor_thread = threading.Thread(target=self._monitor_log, daemon=True)
                self._monitor_thread.start()

    @property
    def ready(self) -> bool:
        return bool(self._created)

    def _log_event(self, message: str) -> None:
        path = self.log_path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"{time.time():.3f}\t{message}\n")
        except OSError:
            pass

    def set_phase(self, title: str, detail: str = "") -> None:
        if not self._hwnd:
            return
        try:
            user32 = ctypes.windll.user32
            user32.SetWindowTextW(self._phase, str(title))
            user32.SetWindowTextW(self._detail, str(detail))
            user32.SetWindowPos(self._hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
        except (AttributeError, OSError):
            pass

    def finish(self, success: bool) -> None:
        if success:
            self.set_phase("步骤 6/6 · 更新完成", "正在重新打开 Listing Studio…")
            time.sleep(0.7)
        self._stop.set()
        if self._ui_thread:
            self._ui_thread.join(timeout=1.2)

    def _monitor_log(self) -> None:
        path = self.log_path
        if path is None:
            return
        offset = path.stat().st_size if path.exists() else 0
        while not self._stop.is_set():
            try:
                if path.exists():
                    with path.open("r", encoding="utf-8", errors="replace") as stream:
                        stream.seek(offset)
                        text = stream.read()
                        offset = stream.tell()
                    for line in text.splitlines():
                        if "install tree lock audit start" in line:
                            self.set_phase("步骤 4/6 · 正在确认更新文件已释放", "正在检查是否还有其他程序占用 Listing Studio 文件。")
                        elif "running installer:" in line:
                            self.set_phase("步骤 5/6 · 正在安装新版本", "安装过程会在后台静默完成，请勿关闭电脑。")
                        elif "installer exit code 0" in line:
                            self.set_phase("步骤 5/6 · 正在验证安装结果", "新版本文件已经写入，正在进行最终完整性确认。")
                        elif "update complete; new application relaunched" in line:
                            self.set_phase("步骤 6/6 · 正在重新打开 Listing Studio", "安装已完成。")
                        elif "update failed status=" in line:
                            self.set_phase("更新未完成", "正在恢复原程序并准备错误详情…")
            except OSError:
                pass
            time.sleep(0.15)

    @staticmethod
    def _font(hwnd: int, font: int) -> None:
        try:
            ctypes.windll.user32.SendMessageW(hwnd, NativeUpdatePanel._WM_SETFONT, font, 1)
        except (AttributeError, OSError):
            pass

    def _run_ui(self) -> None:
        try:
            user32 = ctypes.windll.user32
            gdi32 = ctypes.windll.gdi32
            ctypes.windll.comctl32.InitCommonControls()
            user32.CreateWindowExW.restype = ctypes.c_void_p
            user32.CreateWindowExW.argtypes = [
                ctypes.c_ulong,
                ctypes.c_wchar_p,
                ctypes.c_wchar_p,
                ctypes.c_ulong,
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
            ]

            width, height = 620, 190
            x = max(0, (user32.GetSystemMetrics(0) - width) // 2)
            y = max(0, (user32.GetSystemMetrics(1) - height) // 2)
            hwnd = user32.CreateWindowExW(
                self._WS_EX_TOPMOST | self._WS_EX_TOOLWINDOW,
                "#32770",
                "Listing Studio 更新",
                self._WS_POPUP | self._WS_CAPTION,
                x, y, width, height,
                None, None, None, None,
            )
            if not hwnd:
                self._log_event("native update panel unavailable: CreateWindowExW returned 0")
                self._initialized.set()
                return
            phase = user32.CreateWindowExW(0, "STATIC", "步骤 4/6 · 正在接管更新任务", self._WS_CHILD | self._WS_VISIBLE, 34, 30, 540, 28, hwnd, None, None, None)
            detail = user32.CreateWindowExW(0, "STATIC", f"目标版本：v{self.target_version or '?'}", self._WS_CHILD | self._WS_VISIBLE, 34, 68, 540, 30, hwnd, None, None, None)
            bar = user32.CreateWindowExW(0, "msctls_progress32", "", self._WS_CHILD | self._WS_VISIBLE | self._PBS_MARQUEE, 34, 112, 540, 12, hwnd, None, None, None)
            footer = user32.CreateWindowExW(0, "STATIC", "请保持电脑开机。安装完成后程序会自动重新打开。", self._WS_CHILD | self._WS_VISIBLE, 34, 140, 540, 24, hwnd, None, None, None)
            font = gdi32.GetStockObject(self._DEFAULT_GUI_FONT)
            for control in (phase, detail, footer):
                if control:
                    self._font(control, font)
            if bar:
                user32.SendMessageW(bar, self._PBM_SETMARQUEE, 1, 25)
            self._hwnd, self._phase, self._detail = int(hwnd), int(phase), int(detail)
            self._created = True
            user32.ShowWindow(hwnd, 5)
            user32.UpdateWindow(hwnd)
            user32.SetForegroundWindow(hwnd)
            self._log_event(f"native update panel shown hwnd={int(hwnd)}")
            self._log_event(f"native update panel ready hwnd={int(hwnd)}")
            self._initialized.set()

            class POINT(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
            class MSG(ctypes.Structure):
                _fields_ = [("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint), ("wParam", ctypes.c_size_t), ("lParam", ctypes.c_ssize_t), ("time", ctypes.c_ulong), ("pt", POINT), ("lPrivate", ctypes.c_ulong)]
            msg = MSG()
            while not self._stop.is_set():
                while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, self._PM_REMOVE):
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
                time.sleep(0.02)
            user32.DestroyWindow(hwnd)
            self._log_event("native update panel closed")
        except Exception as exc:
            self._log_event(f"native update panel unavailable: {exc!r}")
            self._initialized.set()
        finally:
            self._hwnd = self._phase = self._detail = 0


__all__ = ["NativeUpdatePanel"]
