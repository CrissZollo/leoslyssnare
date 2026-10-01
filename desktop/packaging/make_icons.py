"""Renders packaging/icon.svg to the PNG and ICO files the builds need."""

import os
import sys

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

here = os.path.dirname(os.path.abspath(__file__))
resources = os.path.join(here, "..", "leoslyssnare", "resources")


def render(renderer: QSvgRenderer, size: int) -> QImage:
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    return image


def png_bytes(image: QImage) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data)


def write_ico(path: str, images: list[QImage]) -> None:
    """An .ico file with embedded PNGs (supported since Windows Vista)."""
    blobs = [png_bytes(i) for i in images]
    header = bytearray((0).to_bytes(2, "little") + (1).to_bytes(2, "little") + len(blobs).to_bytes(2, "little"))
    offset = 6 + 16 * len(blobs)
    entries, payload = bytearray(), bytearray()
    for image, blob in zip(images, blobs):
        size = image.width() if image.width() < 256 else 0
        entries += bytes([size, size, 0, 0]) + (1).to_bytes(2, "little") + (32).to_bytes(2, "little")
        entries += len(blob).to_bytes(4, "little") + offset.to_bytes(4, "little")
        offset += len(blob)
        payload += blob
    with open(path, "wb") as f:
        f.write(header + entries + payload)


def main() -> None:
    app = QGuiApplication(sys.argv[:1] + ["-platform", "offscreen"])  # noqa: F841
    renderer = QSvgRenderer(os.path.join(here, "icon.svg"))
    os.makedirs(resources, exist_ok=True)
    render(renderer, 256).save(os.path.join(resources, "icon.png"))
    write_ico(os.path.join(here, "icon.ico"), [render(renderer, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    print("Icons written.")


if __name__ == "__main__":
    main()
