"""Custom widgets that Qt's stylesheets can't express: the live waveform, the
switch, the progress bar, speaker avatars and the drop zone."""

from __future__ import annotations

from collections import deque

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractButton, QComboBox, QFrame, QLabel, QListView, QPushButton,
                               QSizePolicy, QVBoxLayout, QWidget)

from . import icons, theme

# The bar heights of the app icon, used as the app's resting "voice".
MOTIF = (0.26, 0.60, 1.0, 0.74, 0.46, 0.20)


def label(text: str = "", role: str | None = None, wrap: bool = False) -> QLabel:
    result = QLabel(text)
    if role:
        result.setProperty("role", role)
    result.setWordWrap(wrap)
    return result


def repolish(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


class ElidedLabel(QLabel):
    """A one-line label that ends in … instead of widening the layout."""

    def __init__(self, text: str = "", role: str | None = None) -> None:
        super().__init__(text)
        if role:
            self.setProperty("role", role)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(0)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setFont(self.font())
        painter.setPen(QColor(theme.current().ink))
        elided = self.fontMetrics().elidedText(self.text(), Qt.ElideMiddle, self.width())
        painter.drawText(self.rect(), Qt.AlignVCenter | Qt.AlignLeft, elided)


class Card(QFrame):
    def __init__(self, margins=(20, 16, 20, 18), spacing: int = 14) -> None:
        super().__init__()
        self.setObjectName("card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(*margins)
        self.body.setSpacing(spacing)


class Rule(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("rule")
        self.setFixedHeight(1)


# MARK: - Buttons

class Button(QPushButton):
    """A push button whose icon follows its variant colour and the theme."""

    def __init__(self, text: str = "", icon: str | None = None, variant: str = "secondary",
                 size: str | None = None, tooltip: str | None = None) -> None:
        super().__init__()
        self._icon_name = icon
        self.setText(text)
        self.setProperty("variant", variant)
        if size:
            self.setProperty("size", size)
        if tooltip:
            self.setToolTip(tooltip)
        self.setFocusPolicy(Qt.TabFocus)  # a focus ring for the keyboard, not after every click
        self.setCursor(Qt.PointingHandCursor)
        self.setIconSize(QSize(18, 18))
        self.toggled.connect(self.refresh)
        theme.bus.changed.connect(self.refresh)
        self.refresh()

    def setText(self, text: str) -> None:
        # Qt packs the icon tight against the text; an en space gives it air.
        super().setText(f"\u2002{text}" if self._icon_name and text else text)

    def set_variant(self, variant: str) -> None:
        self.setProperty("variant", variant)
        repolish(self)
        self.refresh()

    def set_icon_name(self, name: str | None) -> None:
        self._icon_name = name
        self.refresh()

    def refresh(self, *_) -> None:
        if not self._icon_name:
            return
        t = theme.current()
        variant = self.property("variant")
        color = {"primary": t.brand_ink, "record": "#FFFFFF", "dark": t.canvas,
                 "ghost": t.muted, "link": t.brand}.get(variant, t.ink)
        if self.isCheckable() and self.isChecked():
            color = t.brand
        self.setIcon(icons.icon(self._icon_name, color, t.faint))


class Combo(QComboBox):
    """A combo box with our own chevron. Scrolling over it doesn't change it."""

    # Just before the list opens, so lists that change (microphones, apps) can be refreshed.
    opening = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setView(QListView())
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)

    def wheelEvent(self, event) -> None:
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()

    def showPopup(self) -> None:
        self.opening.emit()
        super().showPopup()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        t = theme.current()
        pixmap = icons.pixmap("chevron-down", t.muted if self.isEnabled() else t.faint, 16, 2.0)
        painter = QPainter(self)
        painter.drawPixmap(self.width() - 28, (self.height() - 16) // 2, pixmap)


class Switch(QAbstractButton):
    TRACK_W, TRACK_H = 38, 22

    def __init__(self, text: str = "") -> None:
        super().__init__()
        self.setText(text)
        self.setCheckable(True)
        self.setFocusPolicy(Qt.TabFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Fixed if not text else QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._position = 0.0
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(140)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)
        self._animation.valueChanged.connect(self._move)
        self.toggled.connect(self._animate)

    def _move(self, value) -> None:
        self._position = float(value)
        self.update()

    def _animate(self, checked: bool) -> None:
        target = 1.0 if checked else 0.0
        if not self.isVisible():
            self._move(target)
            return
        self._animation.stop()
        self._animation.setStartValue(self._position)
        self._animation.setEndValue(target)
        self._animation.start()

    def setChecked(self, checked: bool) -> None:
        super().setChecked(checked)
        self._move(1.0 if checked else 0.0)

    def sizeHint(self) -> QSize:
        width = self.TRACK_W
        if self.text():
            width += 10 + self.fontMetrics().horizontalAdvance(self.text())
        return QSize(width, self.TRACK_H + 4)

    def paintEvent(self, event) -> None:
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setOpacity(1.0 if self.isEnabled() else 0.45)
        track = QRectF(1, (self.height() - self.TRACK_H) / 2, self.TRACK_W - 2, self.TRACK_H)
        p.setPen(Qt.NoPen)
        p.setBrush(theme.mix(t.line_strong if not t.dark else "#434E62", t.brand, self._position))
        p.drawRoundedRect(track, track.height() / 2, track.height() / 2)
        knob = track.height() - 6
        x = track.left() + 3 + (track.width() - knob - 6) * self._position
        p.setBrush(QColor("#FFFFFF" if not t.dark or self._position > 0.5 else "#C9D1DE"))
        p.drawEllipse(QRectF(x, track.top() + 3, knob, knob))
        if self.hasFocus() and self.focusPolicy() != Qt.NoFocus:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(t.brand), 2))
            p.drawRoundedRect(track.adjusted(-2, -2, 2, 2), track.height() / 2 + 2, track.height() / 2 + 2)
        if self.text():
            p.setOpacity(1.0 if self.isEnabled() else 0.45)
            p.setPen(QColor(t.ink))
            p.drawText(QRectF(self.TRACK_W + 10, 0, self.width() - self.TRACK_W - 10, self.height()),
                       Qt.AlignVCenter | Qt.AlignLeft, self.text())


# MARK: - Recording

class Waveform(QWidget):
    """Live input level as a scrolling row of bars, like the app icon.
    At rest it shows the icon's own bars on a dotted baseline."""

    BAR, GAP = 4, 3

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(64)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.levels: deque[float] = deque(maxlen=600)
        self.mode = "idle"  # idle, recording, paused

    def push(self, level: float) -> None:
        self.levels.append(max(0.0, min(1.0, level)))
        self.update()

    def set_mode(self, mode: str) -> None:
        if mode == "idle":
            self.levels.clear()
        self.mode = mode
        self.update()

    def paintEvent(self, event) -> None:
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        step = self.BAR + self.GAP
        count = max(1, (self.width() + self.GAP) // step)
        left = (self.width() - (count * step - self.GAP)) / 2
        mid = self.height() / 2

        def bar(index: int, height: float, color: QColor) -> None:
            p.setBrush(color)
            p.drawRoundedRect(QRectF(left + index * step, mid - height / 2, self.BAR, height),
                              self.BAR / 2, self.BAR / 2)

        baseline = theme.with_alpha(t.line_strong, 0.9)
        recent = list(self.levels)[-count:] if self.mode != "idle" else []
        first = count - len(recent)
        for index in range(first):
            bar(index, 4, baseline)
        if self.mode == "idle":
            start = count // 2 - len(MOTIF) // 2
            for offset, scale in enumerate(MOTIF):
                bar(start + offset, 4 + scale * (self.height() - 12), theme.with_alpha(t.brand, 0.9))
            return
        color = t.record if self.mode == "recording" else t.warn
        for offset, level in enumerate(recent):
            index = first + offset
            fade = 0.3 + 0.7 * index / max(1, count - 1)
            if self.mode == "paused":
                fade *= 0.6
            hot = self.mode == "recording" and level > 0.85
            bar(index, max(4.0, (level ** 1.4) * (self.height() - 4)),
                theme.with_alpha(t.record_hover if hot else color, fade))


class Motif(QWidget):
    """The icon's bars at rest, used on the empty transcript."""

    def __init__(self, height: int = 56) -> None:
        super().__init__()
        self.setFixedSize(len(MOTIF) * 14 - 6, height)

    def paintEvent(self, event) -> None:
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        for index, scale in enumerate(MOTIF):
            height = 6 + scale * (self.height() - 6)
            p.setBrush(theme.mix(t.brand_soft, t.brand, 0.35 + 0.65 * scale))
            p.drawRoundedRect(QRectF(index * 14, (self.height() - height) / 2, 8, height), 4, 4)


class StatusDot(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(10, 10)
        self.color = "muted"
        self.alpha = 1.0

    def set_state(self, color: str, alpha: float = 1.0) -> None:
        self.color, self.alpha = color, alpha
        self.update()

    def paintEvent(self, event) -> None:
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(theme.with_alpha(getattr(t, self.color), self.alpha))
        p.drawEllipse(self.rect())


# MARK: - Progress

class ProgressBar(QWidget):
    """Thin progress bar. `set_value(None)` shows an endless sliding bar."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(6)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._value: float | None = 0.0
        self._phase = 0.0
        self._animation = QVariantAnimation(self)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.setDuration(1300)
        self._animation.setLoopCount(-1)
        self._animation.valueChanged.connect(self._advance)

    def set_value(self, value: float | None) -> None:
        self._value = value
        self._sync()
        self.update()

    def _sync(self) -> None:
        running = self._value is None and self.isVisible()
        if running and self._animation.state() != QVariantAnimation.Running:
            self._animation.start()
        elif not running:
            self._animation.stop()

    def showEvent(self, event) -> None:
        self._sync()

    def hideEvent(self, event) -> None:
        self._sync()

    def _advance(self, value) -> None:
        self._phase = float(value)
        self.update()

    def paintEvent(self, event) -> None:
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        track = QRectF(self.rect())
        radius = track.height() / 2
        p.setPen(Qt.NoPen)
        p.setBrush(theme.with_alpha(t.brand, 0.18))
        p.drawRoundedRect(track, radius, radius)
        clip = QPainterPath()
        clip.addRoundedRect(track, radius, radius)
        p.setClipPath(clip)
        p.setBrush(QColor(t.brand))
        if self._value is None:
            width = track.width() * 0.35
            x = -width + (track.width() + width) * self._phase
            p.drawRoundedRect(QRectF(x, 0, width, track.height()), radius, radius)
        else:
            p.drawRoundedRect(QRectF(0, 0, max(track.height(), track.width() * self._value), track.height()),
                              radius, radius)


# MARK: - Transcript

class Avatar(QWidget):
    def __init__(self, size: int = 34) -> None:
        super().__init__()
        self.setFixedSize(size, size)
        self.speaker = 1
        self.text = "1"

    def set_speaker(self, speaker: int, name: str) -> None:
        self.speaker = speaker
        self.text = (name.strip()[:1] or str(speaker)).upper()
        self.update()

    def paintEvent(self, event) -> None:
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(t.speaker_color(self.speaker)))
        p.drawEllipse(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5))
        font = QFont(self.font())
        font.setPixelSize(max(11, self.height() // 2 - 2))
        font.setWeight(QFont.DemiBold)
        p.setFont(font)
        p.setPen(QColor(t.speaker_ink))
        p.drawText(self.rect(), Qt.AlignCenter, self.text)


class ShareBar(QWidget):
    """How much of the meeting one person spoke."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(4)
        self.share = 0.0
        self.speaker = 1

    def set_share(self, speaker: int, share: float) -> None:
        self.speaker, self.share = speaker, share
        self.update()

    def paintEvent(self, event) -> None:
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        rect = QRectF(self.rect())
        p.setBrush(QColor(t.line))
        p.drawRoundedRect(rect, 2, 2)
        p.setBrush(QColor(t.speaker_color(self.speaker)))
        p.drawRoundedRect(QRectF(0, 0, max(4.0, rect.width() * self.share), rect.height()), 2, 2)


class Scrubber(QWidget):
    """The playback position along the recording. Click or drag to jump.
    With speakers, the track is coloured by who speaks when."""

    seeking = Signal(float)  # seconds, while dragging
    seeked = Signal(float)  # seconds, when let go
    KNOB = 14

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(22)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)
        self.setMouseTracking(True)
        self.duration = 0.0
        self.position = 0.0
        self.turns: list[tuple[float, float, int | None]] = []
        self.dragging = False
        self._hover = False

    def set_duration(self, seconds: float, turns: list[tuple[float, float, int | None]] | None = None) -> None:
        self.duration = max(0.0, seconds)
        self.turns = turns or []
        self.update()

    def set_position(self, seconds: float) -> None:
        if not self.dragging:
            self.position = seconds
            self.update()

    def _track(self) -> QRectF:
        inset = self.KNOB / 2
        return QRectF(inset, self.height() / 2 - 2.5, max(1.0, self.width() - 2 * inset), 5)

    def _seconds_at(self, x: float) -> float:
        track = self._track()
        return max(0.0, min(1.0, (x - track.left()) / track.width())) * self.duration

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.duration > 0:
            self.dragging = True
            self.position = self._seconds_at(event.position().x())
            self.update()
            self.seeking.emit(self.position)

    def mouseMoveEvent(self, event) -> None:
        if self.dragging:
            self.position = self._seconds_at(event.position().x())
            self.update()
            self.seeking.emit(self.position)

    def mouseReleaseEvent(self, event) -> None:
        if self.dragging and event.button() == Qt.LeftButton:
            self.dragging = False
            self.seeked.emit(self.position)

    def keyPressEvent(self, event) -> None:
        step = {Qt.Key_Left: -5.0, Qt.Key_Right: 5.0}.get(event.key())
        if step is None or self.duration <= 0:
            super().keyPressEvent(event)
            return
        self.position = max(0.0, min(self.duration, self.position + step))
        self.update()
        self.seeked.emit(self.position)

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()

    def paintEvent(self, event) -> None:
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setOpacity(1.0 if self.isEnabled() else 0.45)
        p.setPen(Qt.NoPen)
        track = self._track()
        radius = track.height() / 2
        clip = QPainterPath()
        clip.addRoundedRect(track, radius, radius)
        p.setClipPath(clip)
        p.fillRect(track, QColor(t.line))
        fraction = min(1.0, self.position / self.duration) if self.duration > 0 else 0.0
        played_x = track.left() + track.width() * fraction
        if self.turns and self.duration > 0:
            for start, end, speaker in self.turns:
                left = track.left() + track.width() * start / self.duration
                right = track.left() + track.width() * min(end, self.duration) / self.duration
                color = t.speaker_color(speaker)
                p.fillRect(QRectF(left, track.top(), max(1.0, right - left), track.height()),
                           theme.with_alpha(color, 0.3))
                if left < played_x:
                    p.fillRect(QRectF(left, track.top(), max(1.0, min(right, played_x) - left), track.height()),
                               QColor(color))
        else:
            p.fillRect(QRectF(track.left(), track.top(), played_x - track.left(), track.height()), QColor(t.brand))
        p.setClipping(False)

        knob = self.KNOB + (2 if self._hover or self.dragging else 0)
        center = QPointF(played_x, self.height() / 2)
        p.setBrush(QColor(t.surface))
        p.setPen(QPen(QColor(t.brand), 3))
        p.drawEllipse(center, knob / 2 - 1.5, knob / 2 - 1.5)
        if self.hasFocus():
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(theme.with_alpha(t.brand, 0.4), 2))
            p.drawEllipse(center, knob / 2 + 2, knob / 2 + 2)


# MARK: - Drag and drop

class DropZone(QFrame):
    """A dashed outline that lights up while a file is dragged over the window."""

    def __init__(self) -> None:
        super().__init__()
        self.targeted = False

    def set_targeted(self, targeted: bool) -> None:
        self.targeted = targeted
        self.update()

    def paintEvent(self, event) -> None:
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setBrush(QColor(t.brand_soft) if self.targeted else Qt.NoBrush)
        pen = QPen(QColor(t.brand if self.targeted else t.line_strong), 1.5)
        pen.setDashPattern([4, 4])
        pen.setCapStyle(Qt.FlatCap)
        p.setPen(pen)
        p.drawRoundedRect(rect, 16, 16)
