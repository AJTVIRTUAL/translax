"""
Palette et feuille de style de l'installeur -- les VRAIES couleurs de
TRANSLAX (`ui/styles.qss`), pas une approximation. Reprises en dur ici
plutôt qu'en chargeant `ui/styles.qss` à l'exécution : ce fichier utilise
des sélecteurs (`QFrame#dropZone`, `QComboBox`...) qui n'existent pas dans
l'installeur, et le dupliquer en dur évite qu'un futur changement du thème
principal ne casse silencieusement le rendu de l'installeur -- les deux
partagent la même PALETTE par construction, pas le même fichier.

Coins carrés partout (`border-radius: 0px`), aucune ombre : c'est la
signature visuelle de TRANSLAX, pas un oubli de style.
"""
from __future__ import annotations

BG = "#12151c"
TEXT = "#e6eaf2"
TEXT_MUTED = "#8b96a8"
TEXT_FAINT = "#4a5265"

CARD_BG = "#1a1f2a"
CARD_BORDER = "#262d3a"

FIELD_BG = "#10141b"
FIELD_BORDER = "#2b3444"

ACCENT = "#3b7dfb"
ACCENT_HOVER = "#4f8cff"
ACCENT_PRESSED = "#2f6ad8"
ACCENT_TEAL = "#59d4c4"

SUCCESS = "#7fdb8f"
SUCCESS_BG = "#1b2a1f"
SUCCESS_BORDER = "#3a6b45"

DANGER = "#ec6a63"
DANGER_BG = "#2a1d20"
DANGER_BORDER = "#5c2f2f"

FONT_FAMILY = '"Segoe UI", "Inter", sans-serif'

STYLESHEET = f"""
QWidget {{
    background-color: {BG};
    color: {TEXT};
    font-family: {FONT_FAMILY};
    font-size: 13px;
}}

QLabel, QCheckBox {{
    background: transparent;
}}

QLabel#wizardTitle {{
    font-size: 22px;
    font-weight: 700;
    letter-spacing: 1px;
    color: #ffffff;
}}

QLabel#wizardSubtitle {{
    color: {TEXT_MUTED};
    font-size: 12.5px;
}}

QLabel#wizardBody {{
    color: #c3ccdb;
    font-size: 13px;
}}

QLabel#sectionLabel {{
    color: {TEXT_MUTED};
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1px;
    text-transform: uppercase;
}}

QLabel#statusLabel {{
    color: #9aa5b7;
    font-size: 12.5px;
}}

QLabel#versionLabel {{
    color: {TEXT_FAINT};
    font-size: 10.5px;
}}

QFrame#panel {{
    background-color: {CARD_BG};
    border: 1px solid {CARD_BORDER};
    border-radius: 0px;
}}

QLineEdit {{
    background-color: {FIELD_BG};
    border: 1px solid {FIELD_BORDER};
    border-radius: 0px;
    padding: 7px 10px;
    color: {TEXT};
    selection-background-color: {ACCENT};
}}

QLineEdit:disabled {{
    color: #5b6474;
    background-color: #141821;
}}

QCheckBox {{
    spacing: 8px;
    color: #c3ccdb;
}}

QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {FIELD_BORDER};
    border-radius: 0px;
    background-color: {FIELD_BG};
}}

QCheckBox::indicator:checked {{
    background-color: {ACCENT};
    border-color: {ACCENT};
}}

QPushButton {{
    background-color: #232b38;
    border: 1px solid #313b4b;
    border-radius: 0px;
    padding: 9px 18px;
    color: #dbe2ee;
    font-weight: 600;
}}

QPushButton:hover {{
    background-color: #2b3543;
    border-color: #3d4959;
}}

QPushButton:pressed {{
    background-color: #1e2531;
}}

QPushButton:disabled {{
    background-color: #191e27;
    border-color: #242c39;
    color: #545d6d;
}}

QPushButton#primary {{
    background-color: {ACCENT};
    border: none;
    color: #ffffff;
    padding: 10px 22px;
    font-size: 13.5px;
}}

QPushButton#primary:hover {{
    background-color: {ACCENT_HOVER};
}}

QPushButton#primary:pressed {{
    background-color: {ACCENT_PRESSED};
}}

QPushButton#primary:disabled {{
    background-color: #22334f;
    color: #6b7a94;
}}

QPushButton#danger {{
    background-color: {DANGER_BG};
    border: 1px solid {DANGER_BORDER};
    color: {DANGER};
    padding: 10px 22px;
    font-size: 13.5px;
}}

QPushButton#danger:hover {{
    background-color: #3a2427;
    border-color: #7c3a38;
}}

QPushButton#browseButton {{
    padding: 7px 14px;
}}

QProgressBar {{
    background-color: {FIELD_BG};
    border: 1px solid #232b38;
    border-radius: 0px;
    height: 14px;
    text-align: center;
    color: {TEXT_MUTED};
}}

QProgressBar::chunk {{
    border-radius: 0px;
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                      stop:0 {ACCENT}, stop:1 {ACCENT_TEAL});
}}

/* --- Barre de titre (remplace la barre native Windows, voir ui/titlebar.py) --- */

QWidget#titleBar {{
    background-color: #0d1117;
    border-bottom: 1px solid #1c222c;
}}

QLabel#titleBarText {{
    color: {TEXT_MUTED};
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 1px;
}}

QPushButton#titleBarButton,
QPushButton#titleBarClose {{
    background-color: transparent;
    border: none;
    border-radius: 0px;
    padding: 0px;
}}

QPushButton#titleBarButton:hover {{
    background-color: #232b38;
}}

QPushButton#titleBarButton:pressed {{
    background-color: #2b3543;
}}

QPushButton#titleBarClose:hover {{
    background-color: #e5484d;
}}

QPushButton#titleBarClose:pressed {{
    background-color: #c93a3e;
}}
"""
