"""
Coins arrondis de la fenêtre quand elle n'est PAS en plein écran --
demande explicite de l'utilisateur (10/11/2026) : « arrondis les angles du
logiciel quand il n'est pas en grand écran, exactement comme pour les
logiciels Apple ». Carrés dès que la fenêtre est agrandie, comme sur Mac.

La fenêtre étant sans cadre (`Qt.FramelessWindowHint`, voir ui/titlebar.py),
l'OS ne l'arrondit pas de lui-même. Deux techniques, selon l'OS :

  - **Windows 11** : le compositeur du système (DWM) arrondit lui-même la
    fenêtre sur demande (`DWMWA_WINDOW_CORNER_PREFERENCE`) -- bord lissé
    parfait, aucun coût, et un fin liseré de la couleur du thème
    (`DWMWA_BORDER_COLOR`), comme le liseré des fenêtres sombres de macOS.
    Vérifié réellement sur une fenêtre Qt sans cadre (capture d'écran des
    pixels du coin : le bureau apparaît bien derrière l'arrondi). Rayon
    imposé par Windows (8 px), non réglable.
  - **macOS** : pas d'équivalent accessible sans Objective-C -- la fenêtre
    est rendue transparente (`WA_TranslucentBackground`) et c'est
    `MainWindow.paintEvent` qui dessine son fond en rectangle arrondi
    (`paint_rounded_background`). Le contenu est déjà en retrait de
    `RESIZE_MARGIN` (6 px) de tous les côtés, ce qui le garde à l'intérieur
    de l'arrondi pour tout rayon <= 20 px : aucun widget enfant ne déborde
    dans les coins. macOS dessine alors l'ombre en suivant cette forme.

  - Windows 10 : coins carrés (l'attribut DWM n'y existe pas -- et c'est de
    toute façon l'apparence de toutes les fenêtres de Windows 10).
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPen

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

WINDOW_RADIUS = 12.0                     # macOS (dessiné) -- Windows 11 impose 8 px
WINDOW_FILL = QColor("#12151c")          # = fond `QWidget` de ui/styles.qss
BORDER_COLOR = QColor("#2b3341")         # liseré discret, un ton au-dessus du fond
BORDER_COLOR_MAC = QColor(255, 255, 255, 30)

_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWA_BORDER_COLOR = 34
_DWMWCP_DONOTROUND = 1
_DWMWCP_ROUND = 2
_DWMWA_COLOR_NONE = 0xFFFFFFFE


def uses_painted_corners() -> bool:
    """True si la fenêtre doit dessiner elle-même ses coins arrondis (macOS)
    -- à savoir AVANT la création de la fenêtre native, pour poser
    `WA_TranslucentBackground` à temps."""
    return IS_MAC


def _colorref(color: QColor) -> int:
    """QColor -> COLORREF Win32 (0x00BBGGRR)."""
    return color.red() | (color.green() << 8) | (color.blue() << 16)


def apply_native_corners(widget, rounded: bool) -> bool:
    """
    Windows 11 : demande à DWM d'arrondir (ou non) la fenêtre native de
    `widget`. Retourne True si le système a accepté, False ailleurs (autre
    OS, Windows 10, plateforme Qt « offscreen » des tests…) -- jamais
    d'exception : des coins carrés ne doivent jamais empêcher l'appli de
    s'ouvrir.
    """
    if not IS_WINDOWS or QGuiApplication.platformName() != "windows":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        dwm = ctypes.windll.dwmapi
        dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        dwm.DwmSetWindowAttribute.restype = ctypes.c_long
        hwnd = wintypes.HWND(int(widget.winId()))

        preference = ctypes.c_int(_DWMWCP_ROUND if rounded else _DWMWCP_DONOTROUND)
        hr = dwm.DwmSetWindowAttribute(
            hwnd, _DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(preference), ctypes.sizeof(preference)
        )
        if hr != 0:
            return False  # Windows 10 : E_INVALIDARG, attribut inconnu

        border = ctypes.c_uint(_colorref(BORDER_COLOR) if rounded else _DWMWA_COLOR_NONE)
        dwm.DwmSetWindowAttribute(hwnd, _DWMWA_BORDER_COLOR, ctypes.byref(border), ctypes.sizeof(border))
        return True
    except Exception:  # noqa: BLE001 - voir la docstring
        return False


def paint_rounded_background(widget, rounded: bool, fill: QColor = WINDOW_FILL) -> None:
    """
    Fond de fenêtre en rectangle arrondi (macOS, voir le docstring du
    module), avec un liseré clair très discret comme les fenêtres sombres
    natives. `rounded=False` (fenêtre agrandie) : rectangle plein, sans
    liseré.
    """
    painter = QPainter(widget)
    painter.setRenderHint(QPainter.Antialiasing)
    rect = QRectF(widget.rect())
    if not rounded:
        painter.fillRect(rect, fill)
        return
    path = QPainterPath()
    path.addRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), WINDOW_RADIUS, WINDOW_RADIUS)
    painter.fillPath(path, fill)
    painter.setPen(QPen(BORDER_COLOR_MAC, 1.0))
    painter.drawPath(path)
