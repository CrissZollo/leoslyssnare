"""Small line icons, drawn as SVG and tinted to the current theme."""

from __future__ import annotations

import math

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

# 24x24 grid, 1.75 px strokes. "FILL" marks shapes that are solid.
_PATHS = {
    "mic": '<path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/>'
           '<path d="M19 10v2a7 7 0 0 1-14 0v-2"/><path d="M12 19v3"/>',
    "waveform": '<path d="M3 10v4"/><path d="M7.5 6v12"/><path d="M12 3v18"/>'
                '<path d="M16.5 8v8"/><path d="M21 11v2"/>',
    "download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/>',
    "shield-check": '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 '
                    '0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/>'
                    '<path d="m9 12 2 2 4-4"/>',
    "copy": '<rect width="13" height="13" x="9" y="9" rx="2"/>'
            '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
    "save": '<path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/>',
    "folder": '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 '
              '2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
    "users": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/>'
             '<path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    "folder-search": '<path d="M11 20H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 '
                     '1.67.9H20a2 2 0 0 1 2 2v3"/><circle cx="17" cy="17" r="3"/><path d="m21 21-1.9-1.9"/>',
    "pause": '<rect x="6" y="4" width="4" height="16" rx="1.2" FILL/><rect x="14" y="4" width="4" height="16" rx="1.2" FILL/>',
    "play": '<path d="M7 4.9v14.2a1.2 1.2 0 0 0 1.83 1.02l11.4-7.1a1.2 1.2 0 0 0 0-2.04L8.83 3.88A1.2 1.2 0 0 0 7 4.9Z" FILL/>',
    "stop": '<rect x="5" y="5" width="14" height="14" rx="2.5" FILL/>',
    "record": '<circle cx="12" cy="12" r="7" FILL/>',
    "file-text": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/>'
                 '<path d="M8 13h8"/><path d="M8 17h5"/>',
    "merge": '<path d="m8 6 4-4 4 4"/><path d="M12 2v10.3a4 4 0 0 1-1.17 2.87L4 22"/><path d="m20 22-5-5"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "chevron-left": '<path d="m15 18-6-6 6-6"/>',
    "chevron-right": '<path d="m9 18 6-6-6-6"/>',
    "eye": '<path d="M2.06 12.35a1 1 0 0 1 0-.7 10.75 10.75 0 0 1 19.88 0 1 1 0 0 1 0 .7 10.75 10.75 0 0 1-19.88 0"/>'
           '<circle cx="12" cy="12" r="3"/>',
    "eye-off": '<path d="M10.73 5.08a10.74 10.74 0 0 1 11.2 6.57 1 1 0 0 1 0 .7 10.75 10.75 0 0 1-1.44 2.49"/>'
               '<path d="M14.08 14.16a3 3 0 0 1-4.24-4.24"/>'
               '<path d="M17.48 17.5a10.75 10.75 0 0 1-15.42-5.15 1 1 0 0 1 0-.7 10.75 10.75 0 0 1 4.45-5.14"/>'
               '<path d="m2 2 20 20"/>',
    "check": '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    "alert": '<circle cx="12" cy="12" r="9"/><path d="M12 7.5v5"/><path d="M12 16.3h.01"/>',
}

_cache: dict[tuple, QPixmap] = {}


def _scale() -> int:
    screen = QGuiApplication.primaryScreen()
    return max(2, math.ceil(screen.devicePixelRatio())) if screen else 2


def pixmap(name: str, color: str, size: int = 18, stroke: float = 1.75) -> QPixmap:
    key = (name, color, size, stroke, _scale())
    cached = _cache.get(key)
    if cached is not None:
        return cached
    body = _PATHS[name].replace("FILL", f'fill="{color}" stroke="none"')
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
           f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')
    scale = _scale()
    image = QImage(size * scale, size * scale, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    QSvgRenderer(QByteArray(svg.encode())).render(painter, QRectF(0, 0, size * scale, size * scale))
    painter.end()
    result = QPixmap.fromImage(image)
    result.setDevicePixelRatio(scale)
    _cache[key] = result
    return result


def icon(name: str, color: str, disabled_color: str | None = None, size: int = 18) -> QIcon:
    result = QIcon()
    result.addPixmap(pixmap(name, color, size), QIcon.Normal)
    result.addPixmap(pixmap(name, disabled_color or color, size), QIcon.Disabled)
    return result
