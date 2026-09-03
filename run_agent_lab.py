from __future__ import annotations

import os
import sys
from pathlib import Path

from app.runtime_paths import runtime_root


os.environ.setdefault("QSG_RENDER_LOOP", "threaded")


def _auto_configure_existing_dashscope_key(controller: object) -> bool:
    """Make the development lab immediately usable when a test key already exists.

    The generic AI smoke tests and local development convention use AI_API_KEY.
    Agent Lab previously ignored that existing runtime credential and therefore
    rendered every interaction control disabled until the same key was pasted a
    second time into the left panel. Resolve the existing environment credential
    at process start instead; it remains memory-only and is never persisted.
    """

    api_key = str(
        os.environ.get("AI_API_KEY")
        or os.environ.get("DASHSCOPE_API_KEY")
        or ""
    ).strip()
    if not api_key:
        return False
    model = str(os.environ.get("AGENT_LAB_MODEL") or "qwen-plus").strip() or "qwen-plus"
    configure = getattr(controller, "configureProvider")
    base_url = str(getattr(controller, "dashscopeBaseUrl"))
    return bool(configure("openai-compatible", base_url, model, api_key))


def main() -> int:
    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine
    except ImportError:
        print(
            "缺少 Agent Lab GUI 依赖 PySide6。请执行：python -m pip install -r requirements-gui.txt",
            file=sys.stderr,
        )
        return 2

    from gui.agent_lab_controller import AgentLabController

    app = QGuiApplication(sys.argv)
    app.setApplicationName("Listing Studio · Agent Workspace")
    app.setOrganizationName("ecommerce-agent")

    controller = AgentLabController(runtime_root())
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("agentLab", controller)
    qml_path = Path(__file__).resolve().parent / "gui" / "qml" / "AgentLab.qml"
    engine.load(QUrl.fromLocalFile(str(qml_path)))
    if not engine.rootObjects():
        print(f"Agent Lab QML failed to load: {qml_path}", file=sys.stderr)
        return 3

    _auto_configure_existing_dashscope_key(controller)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
