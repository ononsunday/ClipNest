"""生成 ClipNest 的多尺寸 Windows 图标，不额外依赖图像库。"""

from __future__ import annotations

import argparse
from pathlib import Path
import struct

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPen


def render_png(size: int) -> bytes:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / 256, size / 256)

    gradient = QLinearGradient(30, 16, 220, 245)
    gradient.setColorAt(0, QColor("#79bcff"))
    gradient.setColorAt(1, QColor("#337be0"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(gradient)
    painter.drawRoundedRect(QRectF(10, 10, 236, 236), 58, 58)

    painter.setBrush(QColor(226, 241, 255, 160))
    painter.drawRoundedRect(QRectF(65, 49, 120, 154), 18, 18)
    painter.setBrush(QColor("#ffffff"))
    painter.drawRoundedRect(QRectF(79, 64, 120, 154), 18, 18)

    painter.setBrush(QColor("#bddcff"))
    painter.drawRoundedRect(QRectF(112, 45, 54, 30), 10, 10)
    painter.setPen(QPen(QColor("#5599eb"), 10, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    for y, width in [(111, 57), (142, 68), (173, 41)]:
        painter.drawLine(105, y, 105 + width, y)
    painter.end()

    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise RuntimeError("无法生成 PNG 图标")
    return bytes(buffer.data())


def create_icon(destination: Path) -> None:
    sizes = (16, 24, 32, 48, 64, 128, 256)
    images = [render_png(size) for size in sizes]
    offset = 6 + 16 * len(images)
    directory = bytearray(struct.pack("<HHH", 0, 1, len(images)))
    for size, data in zip(sizes, images, strict=True):
        dimension = 0 if size == 256 else size
        directory.extend(struct.pack("<BBBBHHII", dimension, dimension, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(bytes(directory) + b"".join(images))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    create_icon(parser.parse_args().output)
