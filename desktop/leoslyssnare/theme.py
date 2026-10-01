"""Design tokens, fonts and the application stylesheet.

One look on Windows and Linux: Fusion as the base style, Inter as the typeface,
and a light and a dark palette that follow the system setting. The macOS app uses
the same palette and layout (see Sources/LeosLyssnare/Theme.swift).
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

FAMILY = "Inter"


@dataclass(frozen=True)
class Theme:
    dark: bool
    canvas: str  # window background
    surface: str  # cards
    sunken: str  # inputs and hover fills
    ink: str  # primary text
    muted: str  # secondary text
    faint: str  # disabled and placeholder text
    line: str  # hairlines
    line_strong: str  # control borders
    brand: str
    brand_hover: str
    brand_ink: str  # text on brand
    brand_soft: str
    record: str
    record_hover: str
    record_soft: str
    ok: str
    ok_soft: str
    warn: str
    speakers: tuple[str, ...]
    speaker_ink: str  # text on a speaker colour

    def speaker_color(self, speaker: int | None) -> str:
        if speaker is None:
            return self.muted
        return self.speakers[(speaker - 1) % len(self.speakers)]


LIGHT = Theme(
    dark=False,
    canvas="#F1F3F8", surface="#FFFFFF", sunken="#F3F5F9",
    ink="#131A26", muted="#5F6B7F", faint="#A3ACBB",
    line="#E4E8F0", line_strong="#D3D9E4",
    brand="#2F5BD8", brand_hover="#2650C2", brand_ink="#FFFFFF", brand_soft="#E8EEFD",
    record="#E5484D", record_hover="#D23A3F", record_soft="#FDECEC",
    ok="#0F7B57", ok_soft="#E2F4EC", warn="#C77A0A",
    speakers=("#3D63DD", "#0E8F7E", "#C2610C", "#C03A8E", "#7B4FD9", "#CF3F3F", "#5B7083", "#2E8B3E"),
    speaker_ink="#FFFFFF",
)

DARK = Theme(
    dark=True,
    canvas="#0D1117", surface="#161B24", sunken="#1D2330",
    ink="#E8ECF4", muted="#98A2B5", faint="#5A6478",
    line="#242B38", line_strong="#313A4A",
    brand="#7C9BFF", brand_hover="#93AEFF", brand_ink="#0D1117", brand_soft="#1E2A4D",
    record="#FF6369", record_hover="#FF7B80", record_soft="#3A1D22",
    ok="#4CC79A", ok_soft="#16332A", warn="#F0A93B",
    speakers=("#7C9BFF", "#4CC7B5", "#F0A05A", "#E879B9", "#A98BFF", "#FF7B7B", "#9DB0C4", "#6FD17E"),
    speaker_ink="#0D1117",
)


class _Bus(QObject):
    changed = Signal()


bus = _Bus()
_current = LIGHT
_applied = False


def current() -> Theme:
    return _current


def resource_path(*parts: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", *parts)


# MARK: - Setup

def _system_is_dark(app: QApplication) -> bool:
    forced = os.environ.get("LEOSLYSSNARE_THEME", "").lower()  # for desktops that don't report a scheme
    if forced in ("dark", "light"):
        return forced == "dark"
    scheme = QGuiApplication.styleHints().colorScheme()
    if scheme == Qt.ColorScheme.Dark:
        return True
    if scheme == Qt.ColorScheme.Light:
        return False
    # Unknown (some Linux setups): fall back to how dark the system's own window colour is.
    return app.palette().color(QPalette.Window).lightness() < 128


def setup_application(app: QApplication) -> None:
    """Fonts, base style and theme. Safe to call more than once."""
    global _applied
    if _applied:
        return
    _applied = True

    for name in ("Inter-Regular.ttf", "Inter-Medium.ttf", "Inter-SemiBold.ttf"):
        QFontDatabase.addApplicationFont(resource_path("fonts", name))
    app.setStyle("Fusion")
    font = QFont(FAMILY)
    font.setPixelSize(14)
    font.setStyleStrategy(QFont.PreferAntialias)
    app.setFont(font)

    apply_scheme(app, _system_is_dark(app))
    QGuiApplication.styleHints().colorSchemeChanged.connect(lambda _: apply_scheme(app, _system_is_dark(app)))


def apply_scheme(app: QApplication, dark: bool) -> None:
    global _current
    _current = DARK if dark else LIGHT
    app.setPalette(_palette(_current))
    app.setStyleSheet(stylesheet(_current))
    bus.changed.emit()


def tabular(font: QFont) -> QFont:
    """Same-width digits, so a running timer doesn't jitter."""
    try:
        font.setFeature(QFont.Tag("tnum"), 1)
    except Exception:  # needs Qt 6.7
        pass
    return font


def _palette(t: Theme) -> QPalette:
    p = QPalette()
    for role, color in (
        (QPalette.Window, t.canvas), (QPalette.WindowText, t.ink),
        (QPalette.Base, t.surface), (QPalette.AlternateBase, t.sunken),
        (QPalette.Text, t.ink), (QPalette.Button, t.surface), (QPalette.ButtonText, t.ink),
        (QPalette.ToolTipBase, t.ink), (QPalette.ToolTipText, t.canvas),
        (QPalette.PlaceholderText, t.faint), (QPalette.Highlight, t.brand),
        (QPalette.HighlightedText, t.brand_ink), (QPalette.Link, t.brand),
        (QPalette.BrightText, t.record),
    ):
        p.setColor(role, QColor(color))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor(t.faint))
    return p


def with_alpha(color: str, alpha: float) -> QColor:
    c = QColor(color)
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


def mix(a: str, b: str, amount: float) -> QColor:
    """`amount` of colour b over colour a."""
    ca, cb = QColor(a), QColor(b)
    return QColor.fromRgbF(ca.redF() + (cb.redF() - ca.redF()) * amount,
                           ca.greenF() + (cb.greenF() - ca.greenF()) * amount,
                           ca.blueF() + (cb.blueF() - ca.blueF()) * amount)


def pulse(phase: float) -> float:
    """0.55 ... 1.0 for the blinking recording dot."""
    return 0.775 + 0.225 * math.sin(phase * math.tau)


# MARK: - Stylesheet

def stylesheet(t: Theme) -> str:
    return f"""
QWidget {{ color: {t.ink}; }}
QMainWindow, QWidget#root {{ background: {t.canvas}; }}
QDialog, QMessageBox {{ background: {t.surface}; }}
QLabel {{ background: transparent; }}
QLabel[role="muted"] {{ color: {t.muted}; font-size: 13px; }}
QLabel[role="caption"] {{ color: {t.muted}; font-size: 12px; }}
QLabel[role="cardTitle"] {{ font-size: 15px; font-weight: 600; }}
QLabel[role="appTitle"] {{ font-size: 17px; font-weight: 600; }}
QLabel[role="emptyTitle"] {{ font-size: 18px; font-weight: 600; }}
QLabel[role="timer"] {{ font-size: 42px; font-weight: 500; }}
QLabel[role="status"] {{ color: {t.muted}; font-size: 13px; }}
QLabel[role="rowTitle"] {{ font-size: 14px; font-weight: 500; }}
QLabel#chip {{ background: {t.sunken}; color: {t.muted}; border-radius: 9px; padding: 3px 10px; font-size: 12px; }}

QFrame#card {{ background: {t.surface}; border: 1px solid {t.line}; border-radius: 16px; }}
QFrame#statusCard {{ background: {t.brand_soft}; border: 1px solid {t.brand_soft}; border-radius: 14px; }}
QFrame#tile {{ background: {t.brand_soft}; border: none; border-radius: 12px; }}
QFrame#panel {{ background: {t.sunken}; border: none; border-radius: 12px; }}
QFrame#rule {{ background: {t.line}; border: none; max-height: 1px; min-height: 1px; }}
QFrame#undoBar {{ background: {t.brand_soft}; border: none; border-radius: 9px; }}
QFrame#pillOk {{ background: {t.ok_soft}; border: none; border-radius: 15px; }}
QFrame#pillOk QLabel {{ color: {t.ok}; font-size: 13px; font-weight: 500; }}

QPushButton {{
    background: {t.surface}; color: {t.ink}; border: 1px solid {t.line_strong};
    border-radius: 10px; padding: 0 14px; min-height: 34px; font-weight: 500;
}}
QPushButton:hover {{ background: {t.sunken}; }}
QPushButton:pressed {{ background: {t.line}; }}
QPushButton:disabled {{ color: {t.faint}; background: transparent; border-color: {t.line}; }}
QPushButton:focus {{ border: 2px solid {t.brand}; padding: 0 13px; }}
QPushButton[variant="primary"] {{ background: {t.brand}; color: {t.brand_ink}; border-color: {t.brand}; }}
QPushButton[variant="primary"]:hover {{ background: {t.brand_hover}; border-color: {t.brand_hover}; }}
QPushButton[variant="primary"]:disabled {{ background: {t.brand_soft}; color: {t.faint}; border-color: {t.brand_soft}; }}
QPushButton[variant="record"] {{ background: {t.record}; color: #FFFFFF; border-color: {t.record}; }}
QPushButton[variant="record"]:hover {{ background: {t.record_hover}; border-color: {t.record_hover}; }}
QPushButton[variant="record"]:disabled {{ background: {t.record_soft}; color: {t.faint}; border-color: {t.record_soft}; }}
QPushButton[variant="dark"] {{ background: {t.ink}; color: {t.canvas}; border-color: {t.ink}; }}
QPushButton[variant="dark"]:hover {{ background: {t.muted}; border-color: {t.muted}; }}
QPushButton[variant="ghost"] {{ background: transparent; border-color: transparent; color: {t.muted}; }}
QPushButton[variant="ghost"]:hover {{ background: {t.sunken}; color: {t.ink}; }}
QPushButton[variant="ghost"]:pressed {{ background: {t.line}; }}
QPushButton[variant="link"] {{ background: transparent; border: none; color: {t.brand}; padding: 0; min-height: 24px; }}
QPushButton[variant="link"]:hover {{ color: {t.brand_hover}; text-decoration: underline; }}
QPushButton[size="lg"] {{ min-height: 46px; border-radius: 12px; font-size: 15px; padding: 0 18px; }}
QPushButton[size="lg"]:focus {{ padding: 0 17px; }}
QPushButton[checkable="true"]:checked {{ background: {t.brand_soft}; border-color: {t.brand_soft}; color: {t.brand}; }}
QPushButton[shape="round"], QPushButton[shape="round"]:focus {{
    padding: 0; min-width: 38px; max-width: 38px; min-height: 38px; max-height: 38px; border-radius: 19px;
}}
QPushButton[shape="square"], QPushButton[shape="square"]:focus {{
    padding: 0; min-width: 34px; max-width: 34px; min-height: 34px; max-height: 34px;
}}
QPushButton:default {{ background: {t.brand}; color: {t.brand_ink}; border-color: {t.brand}; }}
QPushButton:default:hover {{ background: {t.brand_hover}; }}

QLineEdit {{
    background: {t.sunken}; border: 1px solid {t.line}; border-radius: 9px;
    padding: 0 10px; min-height: 34px; selection-background-color: {t.brand}; selection-color: {t.brand_ink};
}}
QLineEdit:hover {{ border-color: {t.line_strong}; }}
QLineEdit:focus {{ background: {t.surface}; border: 2px solid {t.brand}; padding: 0 9px; }}
QLineEdit:disabled {{ color: {t.faint}; }}

QComboBox {{
    background: {t.sunken}; border: 1px solid {t.line}; border-radius: 9px;
    padding: 0 32px 0 12px; min-height: 34px;
}}
QComboBox:hover {{ border-color: {t.line_strong}; }}
QComboBox:focus, QComboBox:on {{ background: {t.surface}; border: 2px solid {t.brand}; padding: 0 31px 0 11px; }}
QComboBox:disabled {{ color: {t.faint}; background: transparent; }}
QComboBox::drop-down {{ border: none; width: 0px; }}
QComboBox::down-arrow {{ image: none; width: 0px; }}
QComboBox QAbstractItemView {{
    background: {t.surface}; color: {t.ink}; border: 1px solid {t.line_strong}; border-radius: 10px;
    padding: 4px; outline: 0; selection-background-color: {t.brand_soft}; selection-color: {t.ink};
}}
QComboBox QAbstractItemView::item {{ min-height: 32px; padding: 0 10px; border-radius: 6px; }}

QTextBrowser {{
    background: transparent; border: none; selection-background-color: {t.brand}; selection-color: {t.brand_ink};
}}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t.line_strong}; border-radius: 4px; min-height: 32px; margin: 0 2px; }}
QScrollBar::handle:vertical:hover {{ background: {t.faint}; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {t.line_strong}; border-radius: 4px; min-width: 32px; margin: 2px 0; }}
QScrollBar::handle:horizontal:hover {{ background: {t.faint}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QMenu {{
    background: {t.surface}; color: {t.ink}; border: 1px solid {t.line_strong}; padding: 4px;
    menu-scrollable: 1;
}}
QMenu::item:disabled {{ color: {t.muted}; }}
QMenu::item {{ padding: 7px 16px 7px 10px; border-radius: 6px; }}
QMenu::item:selected {{ background: {t.brand_soft}; color: {t.ink}; }}
QMenu::icon {{ padding-left: 8px; }}
QMenu::separator {{ height: 1px; background: {t.line}; margin: 4px 6px; }}

QToolTip {{
    background: {t.ink}; color: {t.canvas}; border: 1px solid {t.ink}; border-radius: 6px;
    padding: 5px 8px; font-size: 12px;
}}
QMessageBox QLabel {{ font-size: 14px; }}
"""
