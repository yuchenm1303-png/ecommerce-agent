from __future__ import annotations

import os
import sys
from pathlib import Path

from app.runtime_paths import runtime_root


os.environ.setdefault("QSG_RENDER_LOOP", "threaded")


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
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
