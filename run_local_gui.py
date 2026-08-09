from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QColor, QCursor, QPainter, QPixmap
        from PySide6.QtQml import QQmlApplicationEngine
        from PySide6.QtQuick import QQuickWindow, QSGRendererInterface
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print(
            "缺少开发 GUI 依赖 PySide6。\n"
            "请在 ecommerce-agent 当前 Python/venv 中执行：\n"
            "  python -m pip install -r requirements-gui.txt\n",
            file=sys.stderr,
        )
        return 2

    # The production Windows GUI now has one rendering system only: Qt Quick's
    # retained scene graph. Do not embed QQuickWidget/QOpenGLWidget into QWidget.
    os.environ.setdefault("QSG_RENDER_LOOP", "threaded")
    if sys.platform.startswith("win"):
        QQuickWindow.setGraphicsApi(QSGRendererInterface.GraphicsApi.Direct3D11)

    from gui.quick_bridge import QuickBridge, WallpaperProvider

    app = QApplication(sys.argv)
    app.setApplicationName("ecommerce-agent Acceptance Control Console")
    app.setOrganizationName("ecommerce-agent")

    # Preserve the exact small white-dot cursor; the larger follower circle is
    # rendered in the same Qt Quick scene as the wallpaper and sakura.
    cursor_pixmap = QPixmap(10, 10)
    cursor_pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(cursor_pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(QColor(0, 0, 0, 0))
    painter.setBrush(QColor(255, 255, 255, 255))
    painter.drawEllipse(1, 1, 8, 8)
    painter.end()
    QApplication.setOverrideCursor(QCursor(cursor_pixmap, 4, 4))

    project_root = Path(__file__).resolve().parent
    bridge = QuickBridge(project_root)
    engine = QQmlApplicationEngine()
    engine.addImageProvider("wallpaper", WallpaperProvider())
    engine.rootContext().setContextProperty("bridge", bridge)

    qml_path = project_root / "gui" / "qml" / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(qml_path)))
    if not engine.rootObjects():
        print(f"Qt Quick GUI failed to load: {qml_path}", file=sys.stderr)
        return 3

    # Keep Python-owned QObjects alive for the full QML engine lifetime.
    app._gui_bridge = bridge  # type: ignore[attr-defined]
    app._qml_engine = engine  # type: ignore[attr-defined]
    try:
        return app.exec()
    finally:
        QApplication.restoreOverrideCursor()


if __name__ == "__main__":
    raise SystemExit(main())
