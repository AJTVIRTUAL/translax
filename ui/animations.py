"""
Animations de TRANSLAX -- demande explicite de l'utilisateur (11/11/2026) :
« plus d'animations ». Trois mécanismes, tous discrets et courts :

  1. **Transition entre écrans** (`play_page_transition`) : l'ancien écran
     s'efface en remontant légèrement, puis les blocs du nouvel écran
     (titre, cartes, boutons du hall) arrivent un par un, en cascade, en
     glissant de quelques pixels vers leur place. Remplace l'ancien fondu
     au noir (sortie PUIS entrée, 320 ms avec un instant d'écran vide).
     Joué aussi au lancement, sur le hall d'accueil.

     Technique « par instantanés » : les deux écrans sont photographiés
     (`QWidget.grab`), l'écran réel change IMMÉDIATEMENT, et un calque posé
     par-dessus anime les photos. Rien n'est déplacé dans les vrais
     layouts (aucun risque de mise en page cassée, aucun effet graphique
     coûteux sur toute la page), et l'écran réel est déjà actif dessous --
     un clic pendant l'animation tombe au bon endroit (le calque est
     transparent aux clics).

  2. **Lueur au survol des boutons** (`_HoverGlow`) : un voile clair qui
     apparaît en douceur au survol et s'efface en douceur à la sortie, au
     lieu du changement de couleur instantané de la feuille de style.

  3. **Menus déroulants et bulles d'aide arrondis** : même rayon que tout
     le reste (Windows 11, via ui/window_effects.py) -- ce sont de petites
     fenêtres à part, que la feuille de style ne sait pas arrondir.

Les deux derniers passent par un filtre d'évènements posé UNE fois sur
l'application (`install_app_effects`) : tout bouton, toute liste, toute
bulle, y compris ceux des boîtes de dialogue créées plus tard, en profite
sans qu'aucun code appelant n'ait à y penser.
"""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPointF, QRect, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QPainter, QRegion
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from ui import theme, window_effects


def _ease_out(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


# ---------------------------------------------------------------------------
# 1. Transition entre écrans
# ---------------------------------------------------------------------------

# Blocs qui arrivent un par un : titres de page, cartes, boutons du hall,
# boutons d'action principaux. Repérés par leur nom d'objet QSS -- déjà le
# vocabulaire de ui/styles.qss, aucun marquage supplémentaire nécessaire.
_PIECE_NAMES = {"appTitle", "appSubtitle", "card", "hubButton", "primary", "danger", "pauseButton"}
_MAX_PIECES = 12


def _pieces(page: QWidget, area: QWidget) -> list[QRect]:
    """Rectangles (dans le repère de `area`) des blocs visibles de `page`,
    dans l'ordre de lecture -- jamais un bloc contenu dans un autre bloc."""
    found: list[QWidget] = []
    for widget in page.findChildren(QWidget):
        if widget.objectName() not in _PIECE_NAMES or not widget.isVisibleTo(page):
            continue
        if any(other.isAncestorOf(widget) for other in found):
            continue
        found = [other for other in found if not widget.isAncestorOf(other)]
        found.append(widget)
    rects: list[QRect] = []
    bounds = area.rect()
    for widget in found:
        top_left = widget.mapTo(area, widget.rect().topLeft())
        rect = QRect(top_left, widget.size()).intersected(bounds)
        if rect.isValid() and not rect.isEmpty():
            rects.append(rect)
    rects.sort(key=lambda r: (r.top(), r.left()))
    return rects[:_MAX_PIECES]


class _TransitionOverlay(QWidget):
    def __init__(self, parent: QWidget, geometry: QRect, old, new, pieces: list[QRect]):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setGeometry(geometry)
        self._old = old
        self._new = new
        self._pieces = pieces
        self._progress = 0.0
        base = QRegion(self.rect())
        for rect in pieces:
            base -= QRegion(rect)
        self._base = base
        # Cascade resserrée quand il y a beaucoup de blocs : la dernière
        # arrivée ne doit jamais traîner après la fin de l'animation.
        self._stagger = min(0.07, 0.36 / max(1, len(pieces) - 1)) if pieces else 0.0

    def set_progress(self, value) -> None:
        self._progress = float(value)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - API Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor(theme.BACKGROUND))
        t = self._progress

        if self._old is not None:
            fade = 1 - _ease_out(t / 0.32)
            if fade > 0:
                painter.setOpacity(fade)
                painter.drawPixmap(QPointF(0, -10 * (1 - fade)), self._old)

        # Fond du nouvel écran (tout sauf les blocs, qui arrivent à part).
        base = _ease_out((t - 0.12) / 0.5)
        if base > 0:
            painter.save()
            painter.setOpacity(base)
            painter.translate(0, 10 * (1 - base))
            painter.setClipRegion(self._base)
            painter.drawPixmap(QPointF(0, 0), self._new)
            painter.restore()

        scale = self._new.devicePixelRatio()
        for index, rect in enumerate(self._pieces):
            local = _ease_out((t - 0.16 - index * self._stagger) / 0.42)
            if local <= 0:
                continue
            painter.setOpacity(local)
            target = QRectF(rect).translated(0, 18 * (1 - local))
            source = QRectF(rect.x() * scale, rect.y() * scale, rect.width() * scale, rect.height() * scale)
            painter.drawPixmap(target, self._new, source)


def play_page_transition(owner: QWidget, stack, index: int | None = None, *, intro: bool = False) -> None:
    """
    Change `stack` (un QStackedWidget, enfant direct de `owner`) vers
    `index` IMMÉDIATEMENT, puis anime le passage par-dessus. `intro=True` :
    pas d'ancien écran (lancement de l'appli), seulement l'arrivée en
    cascade de l'écran courant.
    """
    previous = getattr(owner, "_page_overlay", None)
    if previous is not None:
        previous[1].stop()
        previous[0].deleteLater()
        owner._page_overlay = None

    old = None if intro else stack.grab()
    if index is not None:
        stack.setCurrentIndex(index)
    page = stack.currentWidget()
    if page is None or stack.width() <= 0 or stack.height() <= 0:
        return
    # La page qui vient d'apparaître n'a peut-être encore jamais été mise
    # en page (premier affichage) : sans ceci, la photo serait vide.
    QApplication.sendPostedEvents(page, QEvent.LayoutRequest)
    if page.layout() is not None:
        page.layout().activate()
    new = stack.grab()

    overlay = _TransitionOverlay(owner, stack.geometry(), old, new, _pieces(page, stack))
    overlay.show()
    overlay.raise_()

    animation = QVariantAnimation(overlay)
    animation.setDuration(theme.PAGE_TRANSITION_MS)
    animation.setStartValue(0.0)
    animation.setEndValue(1.0)
    animation.valueChanged.connect(overlay.set_progress)

    def finish() -> None:
        overlay.deleteLater()
        if getattr(owner, "_page_overlay", None) is not None and owner._page_overlay[0] is overlay:
            owner._page_overlay = None

    animation.finished.connect(finish)
    owner._page_overlay = (overlay, animation)
    animation.start()


# ---------------------------------------------------------------------------
# 2. Lueur au survol des boutons
# ---------------------------------------------------------------------------

# Boutons qui gardent leur propre rendu de survol : ceux de la barre de
# titre (pastilles et boutons Windows, voir ui/titlebar.py) et le lien
# « ← Menu », qui n'a pas de fond.
_NO_GLOW_NAMES = {"titleBarButton", "titleBarClose", "backButton"}
_GLOW_ALPHA = 22  # sur 255 : un voile, pas un aplat


class _HoverGlow(QWidget):
    def __init__(self, button: QPushButton):
        super().__init__(button)
        self.setObjectName("_hoverGlow")
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._button = button
        self._level = 0.0
        self._animation = QVariantAnimation(self)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)
        self._animation.valueChanged.connect(self._set_level)
        self.setGeometry(button.rect())
        self.hide()
        button.installEventFilter(self)

    def _set_level(self, value) -> None:
        self._level = float(value)
        self.setVisible(self._level > 0.001)
        self.update()

    def _go(self, target: float, duration: int) -> None:
        self._animation.stop()
        self._animation.setDuration(duration)
        self._animation.setStartValue(self._level)
        self._animation.setEndValue(target)
        self._animation.start()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - API Qt
        kind = event.type()
        if kind == QEvent.Enter:
            if self._button.isEnabled():
                self._go(1.0, theme.HOVER_IN_MS)
        elif kind == QEvent.Leave:
            self._go(0.0, theme.HOVER_OUT_MS)
        elif kind in (QEvent.Hide, QEvent.EnabledChange):
            # Bouton masqué (changement d'écran) ou désactivé SOUS le
            # curseur : la sortie de la souris n'arrivera peut-être jamais.
            if kind == QEvent.Hide or not self._button.isEnabled():
                self._animation.stop()
                self._set_level(0.0)
        elif kind == QEvent.Resize:
            self.setGeometry(self._button.rect())
        return False

    def paintEvent(self, event) -> None:  # noqa: N802 - API Qt
        if self._level <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, round(_GLOW_ALPHA * self._level)))
        radius = max(0, theme.RADIUS - 1)
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), radius, radius)


def _attach_glow(button: QPushButton) -> None:
    if button.objectName() in _NO_GLOW_NAMES or button.isFlat():
        return
    if button.findChild(_HoverGlow, "_hoverGlow", Qt.FindDirectChildrenOnly) is not None:
        return
    _HoverGlow(button)


# ---------------------------------------------------------------------------
# 3. Filtre d'application : lueur + petites fenêtres arrondies
# ---------------------------------------------------------------------------

class _AppEffects(QObject):
    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - API Qt
        kind = event.type()
        if kind == QEvent.Polish:
            if isinstance(obj, QPushButton):
                _attach_glow(obj)
        elif kind == QEvent.Show:
            if isinstance(obj, QWidget) and obj.isWindow() and obj.windowType() in (Qt.Popup, Qt.ToolTip):
                window_effects.apply_native_corners(obj, rounded=True)
        return False


def install_app_effects(app: QApplication | None = None) -> None:
    """Pose le filtre une seule fois par application (appel répété sans effet)."""
    app = app or QApplication.instance()
    if app is None or getattr(app, "_translax_effects", None) is not None:
        return
    effects = _AppEffects(app)
    app.installEventFilter(effects)
    app._translax_effects = effects  # garde une référence Python vivante
    # Les boutons déjà existants n'auront plus d'évènement Polish.
    for widget in app.allWidgets():
        if isinstance(widget, QPushButton) and widget.testAttribute(Qt.WA_WState_Polished):
            _attach_glow(widget)


def reveal(widget: QWidget, duration: int = 220) -> None:
    """Fait apparaître `widget` en déroulant sa hauteur (bandeau, message…)."""
    if widget.isVisible():
        return
    target = widget.sizeHint().height()
    widget.setMaximumHeight(0)
    widget.setVisible(True)
    animation = QVariantAnimation(widget)
    animation.setDuration(duration)
    animation.setStartValue(0)
    animation.setEndValue(max(1, target))
    animation.setEasingCurve(QEasingCurve.OutCubic)
    animation.valueChanged.connect(lambda v: widget.setMaximumHeight(int(v)))
    animation.finished.connect(lambda: widget.setMaximumHeight(16777215))
    animation.start(QVariantAnimation.DeleteWhenStopped)


__all__ = ["install_app_effects", "play_page_transition", "reveal"]
