from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Sequence

from PySide6.QtCore import QProcess

from app.runtime_paths import is_frozen

_WORKER_EXE = "EcommerceAgentWorker.exe"
_HELPER_SCRIPTS = frozenset(
    {
        "makro_gui_workflow.py",
        "makro_product_pack_workflow.py",
        "makro_execute_listing.py",
        "makro_batch_source.py",
        "makro_batch_job.py",
        "makro_resolve_ai.py",
        "makro_plan_listing.py",
        "makro_one_link.py",
    }
)
_HELPER_SCRIPT_NAMES = frozenset(name.casefold() for name in _HELPER_SCRIPTS)


def packaged_worker_executable() -> Path:
    current = Path(sys.executable).resolve()
    if current.name.casefold() == _WORKER_EXE.casefold():
        return current
    override = str(os.getenv("ECOMMERCE_AGENT_WORKER", "") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return current.with_name(_WORKER_EXE)


def _same_executable(left: str, right: str) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def route_process_start(
    program: str,
    arguments: Sequence[str],
    *,
    frozen: bool | None = None,
    current_executable: str | None = None,
    worker_executable: str | None = None,
) -> tuple[str, list[str]]:
    """Route known internal Python children to the packaged console worker."""

    args = [str(value) for value in arguments]
    if not args:
        return str(program), args

    frozen_now = is_frozen() if frozen is None else bool(frozen)
    if not frozen_now:
        return str(program), args

    current = str(current_executable or sys.executable)
    if not _same_executable(str(program), current):
        return str(program), args
    if Path(args[0]).name.casefold() not in _HELPER_SCRIPT_NAMES:
        return str(program), args

    worker = Path(worker_executable).resolve() if worker_executable else packaged_worker_executable()
    if not worker.is_file() and frozen is None:
        raise RuntimeError(f"安装包内部任务程序缺失：{worker}。请重新安装 EcommerceAgent。")
    return str(worker), args


class RoutedQProcess(QProcess):
    """QProcess that rewrites only known internal script launches."""

    def start(self, *args, **kwargs):  # type: ignore[override]
        if len(args) >= 2 and isinstance(args[0], str) and isinstance(args[1], (list, tuple)):
            program, arguments = route_process_start(args[0], args[1])
            return super().start(program, arguments, *args[2:], **kwargs)
        return super().start(*args, **kwargs)


def install_frozen_process_router(window: object) -> None:
    """Install packaged process routing without changing scheduler/business code."""

    if not is_frozen():
        return

    worker = packaged_worker_executable()
    if not worker.is_file():
        raise RuntimeError(f"安装包不完整，缺少内部任务程序：{worker.name}。请重新安装。")

    from gui import batch_runner, readonly_runner, real_execution

    for module in (readonly_runner, real_execution, batch_runner):
        setattr(module, "QProcess", RoutedQProcess)

    setattr(window, "_frozen_process_router_installed", True)


__all__ = [
    "RoutedQProcess",
    "install_frozen_process_router",
    "packaged_worker_executable",
    "route_process_start",
]
