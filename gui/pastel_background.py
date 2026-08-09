from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPixmap, QRadialGradient


# Custom ecommerce-agent backdrop.  The nekro/imsyy card, cursor and interaction
# language stays intact; only the wallpaper is ours.
#
# Palette: mist blue + lilac + dusty pink + a small cool mint highlight.
# The center is deliberately calmer so dense test tables remain readable through
# the original #00000040 / blur(10px) card treatment.


def _radial_fill(
    painter: QPainter,
    canvas: QPixmap,
    x: float,
    y: float,
    radius: float,
    center: QColor,
) -> None:
    gradient = QRadialGradient(
        QPointF(canvas.width() * x, canvas.height() * y),
        max(canvas.width(), canvas.height()) * radius,
    )
    gradient.setColorAt(0.0, center)
    fade = QColor(center)
    fade.setAlpha(0)
    gradient.setColorAt(1.0, fade)
    painter.fillRect(canvas.rect(), gradient)


def build_pastel_wallpaper(width: int = 1920, height: int = 1080) -> QPixmap:
    width = max(1280, int(width))
    height = max(720, int(height))

    canvas = QPixmap(width, height)
    canvas.fill(QColor("#9FB5E7"))

    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.Antialiasing, True)

    # Main diagonal atmosphere: cooler upper-left, warmer lower-right.
    base = QLinearGradient(0, 0, width, height)
    base.setColorAt(0.00, QColor("#7798CF"))
    base.setColorAt(0.28, QColor("#9A9FD1"))
    base.setColorAt(0.56, QColor("#C39FC7"))
    base.setColorAt(0.80, QColor("#E2ABC0"))
    base.setColorAt(1.00, QColor("#E8B9B0"))
    painter.fillRect(canvas.rect(), base)

    # Large blurred-light style blooms. They are intentionally oversized so the
    # result reads as atmospheric color, not decorative blobs.
    _radial_fill(painter, canvas, 0.14, 0.16, 0.43, QColor(184, 218, 255, 170))
    _radial_fill(painter, canvas, 0.76, 0.12, 0.34, QColor(229, 205, 255, 128))
    _radial_fill(painter, canvas, 0.90, 0.68, 0.38, QColor(255, 190, 206, 128))
    _radial_fill(painter, canvas, 0.18, 0.88, 0.34, QColor(177, 232, 225, 72))
    _radial_fill(painter, canvas, 0.50, 0.48, 0.42, QColor(244, 224, 239, 58))

    # A faint vertical veil behind the central workspace keeps the background
    # interesting at the edges without competing with tables and logs.
    veil = QLinearGradient(width * 0.22, 0, width * 0.78, 0)
    veil.setColorAt(0.0, QColor(255, 255, 255, 0))
    veil.setColorAt(0.45, QColor(245, 238, 250, 28))
    veil.setColorAt(0.55, QColor(245, 238, 250, 28))
    veil.setColorAt(1.0, QColor(255, 255, 255, 0))
    painter.fillRect(canvas.rect(), veil)

    # Very subtle horizon glow gives the scene depth without becoming a graphic.
    horizon = QLinearGradient(0, height * 0.52, 0, height)
    horizon.setColorAt(0.0, QColor(255, 255, 255, 0))
    horizon.setColorAt(0.72, QColor(255, 224, 232, 18))
    horizon.setColorAt(1.0, QColor(243, 207, 218, 38))
    painter.fillRect(canvas.rect(), horizon)

    painter.end()
    return canvas


def install_pastel_background(background) -> QPixmap:
    """Replace the network wallpaper with our local procedural pastel scene.

    `background` is the existing NekroBackground instance.  Keeping that object
    means the original 10px glass-blur sampling path continues to work exactly
    as before; only its source image changes.
    """

    wallpaper = build_pastel_wallpaper()

    # NekroBackground's network completion lambda resolves this method at call
    # time. Replacing it here prevents a late upstream wallpaper download from
    # overwriting the custom background after the window has already opened.
    def ignore_remote_wallpaper(reply) -> None:
        reply.deleteLater()

    background._wallpaper_finished = ignore_remote_wallpaper
    background._source = wallpaper
    background._rebuild()
    background.update()
    return wallpaper
