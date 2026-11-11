"""
Barre de titre stylisée, à la place de la barre native du système.

La fenêtre est créée avec `Qt.FramelessWindowHint` (voir `main_window.py`) :
l'OS ne dessine plus rien en haut de la fenêtre, ni icône, ni titre, ni
boutons Réduire/Agrandir/Fermer. Ce module recrée les trois, dans le style
QSS de TRANSLAX plutôt que le style natif du système, selon deux
présentations, imposées par le système (demande explicite de l'utilisateur,
11/11/2026 : « sur Windows, garde les 3 boutons à droite ; les 3 boutons à
gauche seulement sur Mac ») -- plus de réglage pour en changer :

  - **"mac"** (macOS uniquement) : pastilles rouge/jaune/verte à gauche
    (`MacTrafficLights`), titre centré, pas d'icône dans la barre. Comme
    sur macOS (« le survol doit faire apparaître les x, - ») :
      * survoler le GROUPE révèle les trois glyphes ensemble (×, −, et les
        deux flèches du vert -- tournées vers l'intérieur quand la fenêtre
        est agrandie) ;
      * fenêtre inactive : pastilles grises, recolorées dès qu'on les survole ;
      * pastille enfoncée : teinte assombrie tant que le clic est maintenu.
  - **"windows"** (Windows) : icône à gauche, titre à côté, boutons Réduire/Agrandir/
    Fermer à droite, dessinés en traits fins façon Windows 11
    (`TitleBarButton`) ; Fermer passe au rouge avec une croix blanche au
    survol, comme le vrai bouton de Windows.

Les deux présentations exposent exactement la même interface publique
(signaux `minimize_clicked` / `maximize_clicked` / `close_clicked`,
méthodes `set_icon` / `set_maximized` / `set_window_active` /
`refresh_hover`) : `main_window.py` n'a besoin d'aucun code spécifique.

**Survol « bloqué »** (corrigé le 10/11/2026) : Qt ne reçoit pas toujours
l'évènement « la souris est sortie » quand la fenêtre change d'état SOUS le
curseur -- clic sur Réduire (la fenêtre disparaît alors que la souris est
encore sur le bouton), sur Agrandir (le bouton se déplace sous le curseur),
ou bascule vers une autre fenêtre. Le bouton restait alors affiché
« survolé » au retour. `refresh_hover()` recalcule l'état de survol à partir
de la position RÉELLE du curseur ; `MainWindow` l'appelle à chaque
changement d'état ou d'activation de la fenêtre.

Ce qu'on perd en enlevant le cadre natif, et qu'on recrée à la main (sur
les deux OS, via les API Qt multiplateformes) :
  - déplacer la fenêtre en glissant la barre -> `QWindow.startSystemMove()`
  - agrandir/restaurer en double-cliquant la barre
  - redimensionner depuis les bords -> `QWindow.startSystemResize()`, géré
    dans `MainWindow` (une fenêtre sans cadre n'a plus de bord à saisir par
    défaut, `MainWindow` en réserve un fin autour de tout le contenu).
  - les coins arrondis -> `ui/window_effects.py`.

Ce qu'on ne recrée PAS (limitation assumée, pas un oubli) :
  - le survol du bouton Agrandir qui propose les « Snap Layouts » de
    Windows 11 : lié au bouton natif lui-même (hit-test DWM spécial), pas
    reproductible sans crochets Win32 bas niveau ;
  - l'ombre portée automatique autour des fenêtres normales sous Windows.
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QCursor, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QWidget

IS_MAC = sys.platform == "darwin"

STYLE_MAC = "mac"
STYLE_WINDOWS = "windows"
STYLES = (STYLE_MAC, STYLE_WINDOWS)
# Imposé par le système (demande explicite de l'utilisateur, 11/11/2026) :
# pastilles à gauche UNIQUEMENT sur Mac, boutons classiques à droite sous
# Windows -- plus de réglage pour changer ça.
DEFAULT_STYLE = STYLE_MAC if IS_MAC else STYLE_WINDOWS

TITLE_BAR_HEIGHT = 36
RESIZE_MARGIN = 6          # bord invisible autour de la fenêtre, pour la saisir au redimensionnement
ICON_COLOR = QColor("#c3ccdb")
ICON_COLOR_INACTIVE = QColor("#5d6677")
CLOSE_HOVER_ICON_COLOR = QColor("#ffffff")

# Teintes des pastilles macOS (rouge/jaune/vert système, version actuelle
# d'AppKit) : remplissage, liseré un ton plus sombre, et couleur du glyphe
# révélé au survol -- reprises au plus près du rendu natif.
MAC_DOT_COLORS = {
    "close": QColor("#FF5F57"),
    "minimize": QColor("#FEBC2E"),
    "maximize": QColor("#28C840"),
}
MAC_DOT_BORDERS = {
    "close": QColor("#E2463F"),
    "minimize": QColor("#E1A116"),
    "maximize": QColor("#14AE2E"),
}
MAC_GLYPH_COLORS = {
    "close": QColor("#4D0000"),
    "minimize": QColor("#995700"),
    "maximize": QColor("#006500"),
}
# Fenêtre inactive : pastilles grises (thème sombre), comme sur macOS.
MAC_DOT_INACTIVE = QColor("#363c48")
MAC_DOT_INACTIVE_BORDER = QColor("#454c5a")

MAC_DOT_DIAMETER = 12
MAC_DOT_PAD = 1            # marge autour du cercle, pour que son bord lissé ne soit jamais rogné
MAC_DOT_SPACING = 8        # écart visible entre deux pastilles (comme macOS)
MAC_GROUP_LEFT_MARGIN = 8  # + RESIZE_MARGIN de la fenêtre = 14 px du bord, comme macOS


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    """Couleur à `t` (0..1) du chemin entre `a` et `b`."""
    t = max(0.0, min(1.0, t))
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
    )


def _cursor_inside(widget: QWidget) -> bool:
    """La souris est-elle RÉELLEMENT sur `widget`, d'après la position du
    curseur à l'instant -- et pas d'après les évènements Entrée/Sortie,
    qui peuvent manquer (voir « Survol bloqué » en tête de fichier) ?"""
    window = widget.window()
    if not widget.isVisible() or window.isMinimized():
        return False
    pos = QCursor.pos()
    if not widget.rect().contains(widget.mapFromGlobal(pos)):
        return False
    # Une autre fenêtre (boîte de dialogue, autre logiciel…) par-dessus ?
    top = QApplication.widgetAt(pos)
    return top is not None and (top is widget or widget.isAncestorOf(top) or top.window() is window)


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
        self._window_active = True
        self.setObjectName("titleBarClose" if kind == "close" else "titleBarButton")
        self.setFixedSize(46, TITLE_BAR_HEIGHT)
        self.setCursor(Qt.ArrowCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAttribute(Qt.WA_Hover, True)  # :hover du QSS, même sans feuille de style chargée
        self._set_tooltip()

    def set_kind(self, kind: str) -> None:
        self.kind = kind
        self._set_tooltip()
        self.update()

    def set_window_active(self, active: bool) -> None:
        self._window_active = active
        self.update()

    def refresh_hover(self) -> None:
        self.setAttribute(Qt.WA_UnderMouse, _cursor_inside(self))
        self.update()

    def _set_tooltip(self) -> None:
        self.setToolTip(
            {"minimize": "Réduire", "maximize": "Agrandir", "restore": "Restaurer", "close": "Fermer"}[self.kind]
        )

    def paintEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().paintEvent(event)  # fond + survol viennent du QSS
        hovered = self.underMouse() or self.isDown()
        if self.kind == "close" and hovered:
            color = CLOSE_HOVER_ICON_COLOR  # croix blanche sur fond rouge, comme Windows
        elif self._window_active or hovered:
            color = ICON_COLOR
        else:
            color = ICON_COLOR_INACTIVE  # fenêtre inactive : glyphes atténués, comme Windows

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(color)
        pen.setWidthF(1.2)
        painter.setPen(pen)

        cx, cy = self.width() / 2, self.height() / 2
        half = 4.5  # demi-taille du glyphe, en pixels

        if self.kind == "minimize":
            # Aligné sur un demi-pixel : un trait horizontal fin tracé pile
            # entre deux rangées de pixels serait étalé sur les deux, donc flou.
            y = int(cy) + 0.5
            painter.drawLine(QPointF(cx - half, y), QPointF(cx + half, y))
        elif self.kind == "maximize":
            painter.drawRect(QRectF(cx - half, cy - half, half * 2, half * 2))
        elif self.kind == "restore":
            # Deux carrés décalés, comme l'icône Windows : le carré avant en
            # entier, du carré arrière seulement ce qui dépasse (haut, droite
            # et deux petites amorces).
            size = half * 2 - 2.5
            off = 2.0
            front = QRectF(cx - half, cy - half + off, size, size)
            bx0, by0 = front.left() + off, front.top() - off
            bx1, by1 = bx0 + size, by0 + size
            painter.drawRect(front)
            painter.drawLine(QPointF(bx0, by0), QPointF(bx1, by0))
            painter.drawLine(QPointF(bx1, by0), QPointF(bx1, by1))
            painter.drawLine(QPointF(bx0, by0), QPointF(bx0, front.top()))
            painter.drawLine(QPointF(bx1, by1), QPointF(front.right(), by1))
        elif self.kind == "close":
            painter.drawLine(QPointF(cx - half, cy - half), QPointF(cx + half, cy + half))
            painter.drawLine(QPointF(cx - half, cy + half), QPointF(cx + half, cy - half))


class MacTrafficLightButton(QPushButton):
    """
    Une pastille rouge/jaune/verte — présentation macOS.

    Son apparence dépend de son GROUPE (`MacTrafficLights`) : glyphe visible
    dès que le groupe est survolé (pas seulement cette pastille), gris quand
    la fenêtre est inactive -- voir le docstring du module.
    """

    def __init__(self, kind: str, group: "MacTrafficLights"):
        super().__init__(group)
        self.kind = kind  # "close" | "minimize" | "maximize"
        self._group = group
        self._maximized = False
        size = MAC_DOT_DIAMETER + 2 * MAC_DOT_PAD
        self.setFixedSize(size, size)
        self.setCursor(Qt.ArrowCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFlat(True)
        self.setStyleSheet("background: transparent; border: none; padding: 0px;")
        self._set_tooltip()

    def set_maximized(self, maximized: bool) -> None:
        self._maximized = maximized
        self._set_tooltip()
        self.update()

    def _set_tooltip(self) -> None:
        if self.kind == "maximize":
            self.setToolTip("Restaurer" if self._maximized else "Agrandir")
        else:
            self.setToolTip({"close": "Fermer", "minimize": "Réduire"}[self.kind])

    def enterEvent(self, event) -> None:  # noqa: N802 - API Qt
        # Filet de sécurité : le groupe reçoit normalement son propre
        # évènement d'entrée, mais arriver directement sur une pastille ne
        # doit jamais laisser les glyphes cachés.
        self._group.set_hovered(True)
        super().enterEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 - API Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Survol animé (11/11/2026) : glyphe en fondu, et pastille grise
        # (fenêtre inactive) qui reprend sa couleur en douceur.
        hover = self._group.hover_progress()
        lit = 1.0 if self._group.is_window_active() else hover
        fill = _mix(MAC_DOT_INACTIVE, MAC_DOT_COLORS[self.kind], lit)
        border = _mix(MAC_DOT_INACTIVE_BORDER, MAC_DOT_BORDERS[self.kind], lit)
        if self.isDown():
            fill = fill.darker(125)
            border = border.darker(125)

        rect = QRectF(MAC_DOT_PAD, MAC_DOT_PAD, MAC_DOT_DIAMETER, MAC_DOT_DIAMETER)
        painter.setPen(QPen(border, 0.8))
        painter.setBrush(fill)
        painter.drawEllipse(rect.adjusted(0.4, 0.4, -0.4, -0.4))

        if hover > 0:
            self._draw_glyph(painter, rect.center(), hover)

    def _draw_glyph(self, painter: QPainter, center: QPointF, opacity: float = 1.0) -> None:
        color = QColor(MAC_GLYPH_COLORS[self.kind])
        if self.isDown():
            color = color.darker(140)
        color.setAlphaF(max(0.0, min(1.0, opacity)))
        cx, cy = center.x(), center.y()
        pen = QPen(color)
        pen.setCapStyle(Qt.RoundCap)

        if self.kind == "close":
            pen.setWidthF(1.3)
            painter.setPen(pen)
            half = 2.9
            painter.drawLine(QPointF(cx - half, cy - half), QPointF(cx + half, cy + half))
            painter.drawLine(QPointF(cx - half, cy + half), QPointF(cx + half, cy - half))
        elif self.kind == "minimize":
            pen.setWidthF(1.5)
            painter.setPen(pen)
            half = 3.6
            painter.drawLine(QPointF(cx - half, cy), QPointF(cx + half, cy))
        elif self.kind == "maximize":
            # Deux triangles en diagonale, comme le bouton vert de macOS :
            # pointes vers l'extérieur pour agrandir, vers l'intérieur pour
            # restaurer une fenêtre déjà agrandie.
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            if self._maximized:
                a, b = 0.6, 4.0
                painter.drawPolygon(QPolygonF([QPointF(cx - a, cy - a), QPointF(cx - a, cy - b), QPointF(cx - b, cy - a)]))
                painter.drawPolygon(QPolygonF([QPointF(cx + a, cy + a), QPointF(cx + a, cy + b), QPointF(cx + b, cy + a)]))
            else:
                a, b = 3.3, 1.5
                painter.drawPolygon(QPolygonF([QPointF(cx - a, cy - a), QPointF(cx + b, cy - a), QPointF(cx - a, cy + b)]))
                painter.drawPolygon(QPolygonF([QPointF(cx + a, cy + a), QPointF(cx - b, cy + a), QPointF(cx + a, cy - b)]))


class MacTrafficLights(QWidget):
    """
    Le groupe des trois pastilles, dans l'ordre natif Mac (fermer, réduire,
    agrandir). C'est LUI qui porte l'état de survol : entrer n'importe où
    dans le groupe (pastilles ou petits espaces entre elles) révèle les
    trois glyphes à la fois, comme sur macOS.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hovered = False
        self._window_active = True
        self._hover_progress = 0.0
        self._hover_animation = QVariantAnimation(self)
        self._hover_animation.setEasingCurve(QEasingCurve.OutCubic)
        self._hover_animation.valueChanged.connect(self._set_hover_progress)
        self.setFixedHeight(TITLE_BAR_HEIGHT)

        layout = QHBoxLayout(self)
        pad = MAC_DOT_PAD
        layout.setContentsMargins(MAC_GROUP_LEFT_MARGIN - pad, 0, MAC_DOT_SPACING - pad, 0)
        layout.setSpacing(MAC_DOT_SPACING - 2 * pad)

        self.close_button = MacTrafficLightButton("close", self)
        self.minimize_button = MacTrafficLightButton("minimize", self)
        self.maximize_button = MacTrafficLightButton("maximize", self)
        for button in self.buttons():
            layout.addWidget(button, 0, Qt.AlignVCenter)

    def buttons(self) -> tuple[MacTrafficLightButton, ...]:
        return (self.close_button, self.minimize_button, self.maximize_button)

    def is_hovered(self) -> bool:
        return self._hovered

    def hover_progress(self) -> float:
        """0 (pas survolé) -> 1 (survolé), animé : sert au fondu des glyphes."""
        return self._hover_progress

    def is_window_active(self) -> bool:
        return self._window_active

    def _set_hover_progress(self, value) -> None:
        self._hover_progress = float(value)
        for button in self.buttons():
            button.update()

    def set_hovered(self, hovered: bool) -> None:
        if hovered != self._hovered:
            self._hovered = hovered
            self._hover_animation.stop()
            self._hover_animation.setDuration(110 if hovered else 180)
            self._hover_animation.setStartValue(self._hover_progress)
            self._hover_animation.setEndValue(1.0 if hovered else 0.0)
            self._hover_animation.start()

    def set_window_active(self, active: bool) -> None:
        if active != self._window_active:
            self._window_active = active
            for button in self.buttons():
                button.update()

    def set_maximized(self, maximized: bool) -> None:
        self.maximize_button.set_maximized(maximized)

    def refresh_hover(self) -> None:
        self.set_hovered(_cursor_inside(self))

    def enterEvent(self, event) -> None:  # noqa: N802 - API Qt
        self.set_hovered(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - API Qt
        self.set_hovered(False)
        super().leaveEvent(event)


class TitleBar(QWidget):
    """
    Titre + boutons de fenêtre, avec déplacement au glisser.

    Présentation choisie à la construction (`button_style`, voir le
    docstring du module) ; interface publique identique des deux côtés.
    """

    minimize_clicked = Signal()
    maximize_clicked = Signal()
    close_clicked = Signal()

    def __init__(self, window: QWidget, title: str = "TRANSLAX", button_style: str = DEFAULT_STYLE):
        super().__init__(window)
        self._window = window
        self.setObjectName("titleBar")
        self.setFixedHeight(TITLE_BAR_HEIGHT)
        self.icon_label: QLabel | None = None
        self.traffic_lights: MacTrafficLights | None = None
        self.button_style = button_style if button_style in STYLES else DEFAULT_STYLE

        if self.button_style == STYLE_MAC:
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
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.traffic_lights = MacTrafficLights(self)
        self.close_button = self.traffic_lights.close_button
        self.minimize_button = self.traffic_lights.minimize_button
        self.maximize_button = self.traffic_lights.maximize_button
        self.close_button.clicked.connect(self.close_clicked)
        self.minimize_button.clicked.connect(self.minimize_clicked)
        self.maximize_button.clicked.connect(self.maximize_clicked)
        layout.addWidget(self.traffic_lights)

        layout.addStretch()
        self.title_label = QLabel(title)
        self.title_label.setObjectName("titleBarText")
        self.title_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(self.title_label)
        layout.addStretch()

        # Espace vide de la largeur du groupe de pastilles, à droite : sans
        # lui, le titre « centré » paraîtrait décalé vers la droite (le centre
        # de l'espace restant n'est pas le centre de la barre). Un simple
        # espacement de layout, pas un widget : un QWidget vide recevait le
        # fond général du QSS et dessinait un bloc plus clair au bout de la barre.
        layout.addSpacing(self.traffic_lights.sizeHint().width())

    # ------------------------------------------------------------- public
    def set_icon(self, pixmap: QPixmap) -> None:
        """Sans effet en présentation Mac : la convention native n'affiche
        pas d'icône dans la barre de titre (le Dock / la barre des tâches
        en tient déjà lieu)."""
        if self.icon_label is not None:
            self.icon_label.setPixmap(pixmap)

    def set_maximized(self, maximized: bool) -> None:
        """
        Reflète l'état réel de la fenêtre (double-clic, bouton, ou -- sous
        Windows -- Win+Haut/snap) : icône Agrandir/Restaurer en présentation
        Windows, flèches du vert tournées vers l'intérieur/l'extérieur en
        présentation Mac.
        """
        if self.traffic_lights is not None:
            self.traffic_lights.set_maximized(maximized)
        else:
            self.maximize_button.set_kind("restore" if maximized else "maximize")

    def set_window_active(self, active: bool) -> None:
        """Fenêtre au premier plan ou non : pastilles grises / glyphes et
        titre atténués quand elle ne l'est pas, comme les fenêtres natives."""
        if self.traffic_lights is not None:
            self.traffic_lights.set_window_active(active)
        else:
            for button in (self.minimize_button, self.maximize_button, self.close_button):
                button.set_window_active(active)
        self.title_label.setProperty("inactive", not active)
        self.title_label.style().unpolish(self.title_label)
        self.title_label.style().polish(self.title_label)

    def refresh_hover(self) -> None:
        """Recalcule le survol d'après la position réelle du curseur (voir
        « Survol bloqué » en tête de fichier)."""
        if self.traffic_lights is not None:
            self.traffic_lights.refresh_hover()
        else:
            for button in (self.minimize_button, self.maximize_button, self.close_button):
                button.refresh_hover()

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
