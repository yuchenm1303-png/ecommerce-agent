from __future__ import annotations

import os
import sys
from pathlib import Path

# CI validates QML syntax/component construction without requiring a GPU/display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QSG_RHI_BACKEND", "software")
os.environ.setdefault("QSG_RENDER_LOOP", "basic")

from PySide6.QtCore import QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gui.quick_bridge import QuickBridge, WallpaperProvider  # noqa: E402


def main() -> int:
    app = QApplication(["gui-qml-smoke"])
    engine = QQmlApplicationEngine()
    warnings: list[str] = []
    engine.warnings.connect(lambda rows: warnings.extend(str(row) for row in rows))
    engine.addImageProvider("wallpaper", WallpaperProvider())
    bridge = QuickBridge(ROOT)
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(ROOT / "gui" / "qml" / "Main.qml")))
    app.processEvents()

    if not engine.rootObjects():
        print("QML root object was not created", file=sys.stderr)
        for warning in warnings:
            print(warning, file=sys.stderr)
        return 1

    fatal = [
        warning
        for warning in warnings
        if any(
            marker in warning
            for marker in (
                "is not a type",
                "Invalid property assignment",
                "Cannot assign",
                "Type .* unavailable",
                "Expected token",
                "Syntax error",
                "Cannot override FINAL property",
            )
        )
    ]
    if fatal:
        print("QML load produced fatal warnings:", file=sys.stderr)
        for warning in fatal:
            print(warning, file=sys.stderr)
        return 1

    print(f"QML smoke OK · roots={len(engine.rootObjects())} · warnings={len(warnings)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
