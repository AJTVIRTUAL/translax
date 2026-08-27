"""
Barre de titre stylisée, à la place de la barre native du système.

La fenêtre est créée avec `Qt.FramelessWindowHint` (voir `main_window.py`) :
l'OS ne dessine plus rien en haut de la fenêtre, ni icône, ni titre, ni
boutons Réduire/Agrandir/Fermer. Ce module recrée les trois, dans le style
QSS de TRANSLAX plutôt que le style natif du système — avec une présentation
différente selon l'OS (`sys.platform`), choisie une seule fois à la
construction :

  - **Windows** : icône à gauche, titre à côté, boutons Réduire/Agrandir/
    Fermer à droite, dessinés en traits fins façon Windows 11
    (`TitleBarButton`). Code inchangé depuis sa version d'origine.
  - **macOS** : pastilles rouge/jaune/vert à gauche (`MacTrafficLightButton`),
    titre centré, pas d'icône dans la barre — convention native Mac.

Les deux présentations exposent exactement la même interface publique
(signaux `minimize_clicked` / `maximize_clicked` / `close_clicked`,
méthodes `set_icon` / `set_maximized`) : `main_window.py` n'a besoin
d'aucun code spécifique à l'OS, il utilise `TitleBar` telle quelle.

Ce qu'on perd en enlevant le cadre natif, et qu'on recrée à la main (sur
les deux OS, via les API Qt multiplateformes) :
  - déplacer la fenêtre en glissant la barre -> `QWindow.startSystemMove()`
  - agrandir/restaurer en double-cliquant la barre
  - redimensionner depuis les bords -> `QWindow.startSystemResize()`, géré
    dans `MainWindow` (une fenêtre sans cadre n'a plus de bord à saisir par
    défaut, `MainWindow` en réserve un fin autour de tout le contenu).

Ce qu'on ne recrée PAS (limitation assumée, pas un oubli) :
  - le survol du bouton Agrandir qui propose les « Snap Layouts » de
    Windows 11 : lié au bouton natif lui-même (hit-test DWM spécial), pas
    reproductible sans crochets Win32 bas niveau ;
  - sur Mac, le survol du GROUPE des trois pastilles qui révèle les trois
    glyphes en même temps : ici chaque pastille ne révèle son glyphe que
    lorsqu'elle est survolée individuellement — visuellement très proche,
    plus simple à implémenter correctement ;
  - l'ombre portée automatique autour des fenêtres normales, sur les deux OS.

Non testé sur un vrai Mac au moment de l'écriture (voir MACOS_BUILD.md) :
le code utilise des API Qt documentées comme multiplateformes, mais seul un
test réel sur macOS confirmera le rendu et le comportement.
"""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

IS_MAC = sys.platform == "darwin"

TITLE_BAR_HEIGHT = 36
RESIZE_MARGIN = 6          # bord invisible autour de la fenêtre, pour la saisir au redimensionnement
ICON_COLOR = QColor("#c3ccdb")

# Couleurs des pastilles macOS (rouge/jaune/vert système), approximation des
# teintes réelles d'AppKit -- suffisamment proches pour être reconnues
# instantanément sans dépendre d'une ressource système.
MAC_DOT_COLORS = {
    "close": QColor("#FF5F57"),
    "minimize": QColor("#FEBC2E"),
    "maximize": QColor("#28C840"),
}
MAC_DOT_DIAMETER = 12
MAC_DOT_SPACING = 8
MAC_GROUP_LEFT_MARGIN = 12


class TitleBarButton(QPushButton):
    """
    Bouton Réduire / Agrandir / Restaurer / Fermer — présentation Windows.

    L'icône est dessinée à la main (petits traits/carrés/croix) plutôt que
    prise dans une police : rendu identique quelle que soit la police
    installée, et look cohérent avec les icônes natives de Windows 11 sans
    en dépendre.
    """

    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self.kind = kind  # "minimize" | "maximize" | "restore" | "close"
        self.setObjectName("titleBarClose" if kind == "close" else "titleBarButton")
        self.setFixedSize(46, TITLE_BAR_HEIGHT)
        self.setCursor(Qt.ArrowCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self._set_tooltip()

    def set_kind(self, kind: str) -> None:
        self.kind = kind
        self._set_tooltip()
        self.update()

    def _set_tooltip(self) -> None:
        self.setToolTip(
            {"minimize": "Réduire", "maximize": "Agrandir", "restore": "Restaurer", "close": "Fermer"}[self.kind]
        )

    def paintEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().paintEvent(event)  # fond + survol viennent du QSS
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = painter.pen()
        pen.setColor(ICON_COLOR)
        pen.setWidthF(1.2)
        painter.setPen(pen)

        cx, cy = self.width() / 2, self.height() / 2
        half = 4.5  # demi-taille du glyphe, en pixels

        if self.kind == "minimize":
            painter.drawLine(int(cx - half), int(cy), int(cx + half), int(cy))
        elif self.kind == "maximize":
            painter.drawRect(int(cx - half), int(cy - half), int(half * 2), int(half * 2))
        elif self.kind == "restore":
            offset = 2.5
            back = half - 1.5
            painter.drawRect(int(cx - back + offset), int(cy - back - offset), int(back * 2), int(back * 2))
            painter.drawRect(int(cx - back - offset), int(cy - back + offset), int(back * 2), int(back * 2))
        elif self.kind == "close":
            painter.drawLine(int(cx - half), int(cy - half), int(cx + half), int(cy + half))
            painter.drawLine(int(cx - half), int(cy + half), int(cx + half), int(cy - half))


class MacTrafficLightButton(QPushButton):
    """
    Pastille rouge/jaune/verte — présentation macOS.

    Toujours dessinée en couleur pleine ; le glyphe (×, −, +) n'apparaît
    qu'au survol de CETTE pastille précise, comme une approximation simple
    du comportement natif (qui révèle les trois glyphes ensemble dès qu'on
    survole le groupe -- voir la note en tête de fichier).
    """

    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self.kind = kind  # "close" | "minimize" | "maximize"
        self.setFixedSize(MAC_DOT_DIAMETER, MAC_DOT_DIAMETER)
        self.setCursor(Qt.ArrowCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFlat(True)
        self.setStyleSheet("background: transparent; border: none;")
        self.setToolTip({"close": "Fermer", "minimize": "Réduire", "maximize": "Agrandir/Restaurer"}[self.kind])

    def enterEvent(self, event) -> None:  # noqa: N802 - API Qt
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - API Qt
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 - API Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(MAC_DOT_COLORS[self.kind])
        painter.drawEllipse(0, 0, MAC_DOT_DIAMETER, MAC_DOT_DIAMETER)

        if self.underMouse():
            pen = painter.pen()
            pen.setColor(QColor(0, 0, 0, 130))
            pen.setWidthF(1.2)
            painter.setPen(pen)
            cx = cy = MAC_DOT_DIAMETER / 2
            half = 2.6
            if self.kind == "close":
                painter.drawLine(int(cx - half), int(cy - half), int(cx + half), int(cy + half))
                painter.drawLine(int(cx - half), int(cy + half), int(cx + half), int(cy - half))
            elif self.kind == "minimize":
                painter.drawLine(int(cx - half), int(cy), int(cx + half), int(cy))
            elif self.kind == "maximize":
                painter.drawLine(int(cx - half), int(cy), int(cx + half), int(cy))
                painter.drawLine(int(cx), int(cy - half), int(cx), int(cy + half))


class TitleBar(QWidget):
    """
    Icône + titre + boutons de fenêtre, avec déplacement au glisser.

    Présentation choisie à la construction selon `sys.platform` (voir le
    docstring du module) ; interface publique identique des deux côtés.
    """

    minimize_clicked = Signal()
    maximize_clicked = Signal()
    close_clicked = Signal()

    def __init__(self, window: QWidget, title: str = "TRANSLAX"):
        super().__init__(window)
        self._window = window
        self.setObjectName("titleBar")
        self.setFixedHeight(TITLE_BAR_HEIGHT)
        self.icon_label: QLabel | None = None

        if IS_MAC:
            self._build_mac(title)
        else:
            self._build_windows(title)

    # ------------------------------------------------------------ Windows
    def _build_windows(self, title: str) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 0, 0)
        layout.setSpacing(0)

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(18, 18)
        self.icon_label.setScaledContents(True)
        # Transparent aux clics : sans ça, cliquer-glisser sur l'icône ne
        # ferait rien plutôt que déplacer la fenêtre (l'icône capterait
        # l'évènement souris sans le transmettre au parent).
        self.icon_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(self.icon_label)
        layout.addSpacing(8)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("titleBarText")
        self.title_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(self.title_label)
        layout.addStretch()

        self.minimize_button = TitleBarButton("minimize")
        self.maximize_button = TitleBarButton("maximize")
        self.close_button = TitleBarButton("close")
        self.minimize_button.clicked.connect(self.minimize_clicked)
        self.maximize_button.clicked.connect(self.maximize_clicked)
        self.close_button.clicked.connect(self.close_clicked)
        for button in (self.minimize_button, self.maximize_button, self.close_button):
            layout.addWidget(button)

    # ---------------------------------------------------------------- Mac
    def _build_mac(self, title: str) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(MAC_GROUP_LEFT_MARGIN, 0, MAC_GROUP_LEFT_MARGIN, 0)
        layout.setSpacing(MAC_DOT_SPACING)

        # Ordre natif Mac : fermer, réduire, agrandir -- de gauche à droite.
        self.close_button = MacTrafficLightButton("close")
        self.minimize_button = MacTrafficLightButton("minimize")
        self.maximize_button = MacTrafficLightButton("maximize")
        self.close_button.clicked.connect(self.close_clicked)
        self.minimize_button.clicked.connect(self.minimize_clicked)
        self.maximize_button.clicked.connect(self.maximize_clicked)
        for button in (self.close_button, self.minimize_button, self.maximize_button):
            layout.addWidget(button)

        # Largeur du groupe de pastilles, pour équilibrer un espaceur
        # invisible du même poids à droite -- sans ça, le titre "centré"
        # paraîtrait décalé vers la droite (le centre du widget entier
        # n'est pas le centre de l'espace qui reste une fois les pastilles
        # posées à gauche).
        group_width = 3 * MAC_DOT_DIAMETER + 2 * MAC_DOT_SPACING

        layout.addStretch()
        self.title_label = QLabel(title)
        self.title_label.setObjectName("titleBarText")
        self.title_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(self.title_label)
        layout.addStretch()

        spacer = QWidget()
        spacer.setFixedWidth(group_width)
        spacer.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(spacer)

    # ------------------------------------------------------------- public
    def set_icon(self, pixmap: QPixmap) -> None:
        """Sans effet sur Mac : la convention native n'affiche pas d'icône
        dans la barre de titre (le Dock en tient déjà lieu)."""
        if self.icon_label is not None:
            self.icon_label.setPixmap(pixmap)

    def set_maximized(self, maximized: bool) -> None:
        """
        Reflète l'état réel de la fenêtre (double-clic, bouton, ou -- sous
        Windows -- Win+Haut/snap).

        Sous Windows, bascule l'icône du bouton Agrandir vers l'icône
        Restaurer. Sous Mac, la pastille verte ne change pas d'apparence
        (elle ne le fait pas non plus nativement) -- rien à faire.
        """
        if not IS_MAC:
            self.maximize_button.set_kind("restore" if maximized else "maximize")

    # ---------------------------------------------------------- souris
    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - API Qt
        if event.button() == Qt.LeftButton:
            self.maximize_clicked.emit()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - API Qt
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        # Faire glisser une fenêtre maximisée la restaure d'abord — c'est le
        # comportement natif attendu (« dé-snapper »/annuler le zoom en tirant).
        if self._window.isMaximized():
            self._window.showNormal()
        handle = self._window.windowHandle()
        if handle is not None:
            handle.startSystemMove()
        event.accept()
