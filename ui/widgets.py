"""
Composants animés de TRANSLAX (demande explicite de l'utilisateur,
11/11/2026 : « plus d'animations », coins arrondis harmonisés) :

  - `ToggleSwitch` : interrupteur façon macOS/iOS à la place des cases à
    cocher -- le bouton glisse d'un côté à l'autre, la piste passe du gris
    au bleu. Sous-classe de QCheckBox : même API (`isChecked`, `setChecked`,
    `toggled`…), aucun code appelant à changer ;
  - `AnimatedProgressBar` : barre en forme de pilule (arrondi complet), même
    dégradé qu'avant, avec un reflet qui la parcourt de temps en temps
    pendant une traduction, et un segment qui va et vient quand la durée est
    encore inconnue (chargement du modèle). Sous-classe de QProgressBar :
    `setRange`/`setValue`/l'animation existante de `value` inchangés.

Tout est dessiné à la main (QPainter) plutôt qu'en QSS : la feuille de style
ne sait ni animer, ni arrondir proprement le remplissage d'une barre
presque vide (le « chunk » QSS déborde en ovale quand il est plus étroit
que son propre rayon).
"""
from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QPauseAnimation,
    QPointF,
    QRectF,
    QSequentialAnimationGroup,
    QSize,
    Qt,
    QVariantAnimation,
)
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import QCheckBox, QProgressBar, QSizePolicy

from ui import theme


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    """Couleur à `t` (0..1) du chemin entre `a` et `b`."""
    t = max(0.0, min(1.0, t))
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
        round(a.alpha() + (b.alpha() - a.alpha()) * t),
    )


class ToggleSwitch(QCheckBox):
    """Interrupteur animé -- voir le docstring du module."""

    TRACK_W = 36
    TRACK_H = 20
    GAP = 10  # entre l'interrupteur et son libellé

    _TRACK_OFF = QColor("#2b3444")
    _TRACK_OFF_HOVER = QColor("#36404f")
    _TRACK_ON = QColor(theme.ACCENT)
    _TRACK_ON_HOVER = QColor(theme.ACCENT_LIGHT)
    _KNOB_OFF = QColor("#c3ccdb")
    _KNOB_ON = QColor("#ffffff")

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)
        # QCheckBox est « Minimum » en largeur (jamais plus étroite que tout
        # son texte) : ici « Preferred », pour que le libellé puisse se
        # raccourcir (« … ») au lieu d'élargir toute la page sur une petite
        # fenêtre -- voir minimumSizeHint.
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._position = 1.0 if self.isChecked() else 0.0
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(theme.TOGGLE_MS)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)
        self._animation.valueChanged.connect(self._set_position)
        self.toggled.connect(self._on_toggled)

    def _set_position(self, value) -> None:
        self._position = float(value)
        self.update()

    def _on_toggled(self, checked: bool) -> None:
        target = 1.0 if checked else 0.0
        if not self.isVisible():
            # Changement fait avant affichage (restauration d'un réglage…) :
            # rien à montrer, on se place directement au bon endroit.
            self._animation.stop()
            self._set_position(target)
            return
        self._animation.stop()
        self._animation.setStartValue(self._position)
        self._animation.setEndValue(target)
        self._animation.start()

    # Toute la surface (interrupteur ET libellé) est cliquable.
    def hitButton(self, pos) -> bool:  # noqa: N802 - API Qt
        return self.rect().contains(pos)

    def sizeHint(self) -> QSize:  # noqa: N802 - API Qt
        fm = self.fontMetrics()
        text_w = fm.horizontalAdvance(self.text()) if self.text() else 0
        width = self.TRACK_W + (self.GAP + text_w if text_w else 0) + 2
        return QSize(width, max(self.TRACK_H, fm.height()) + 6)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - API Qt
        # Le libellé se raccourcit (« … ») plutôt que d'élargir la fenêtre.
        return QSize(self.TRACK_W + 2, self.sizeHint().height())

    def enterEvent(self, event) -> None:  # noqa: N802 - API Qt
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - API Qt
        self.update()
        super().leaveEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802 - API Qt
        # Libellé raccourci faute de place : la bulle d'aide le montre en
        # entier -- sans jamais écraser une bulle choisie par l'appelant.
        super().resizeEvent(event)
        available = self.width() - self.TRACK_W - self.GAP - 2
        elided = bool(self.text()) and self.fontMetrics().horizontalAdvance(self.text()) > available
        if elided and not self.toolTip():
            self.setToolTip(self.text())
            self._auto_tooltip = True
        elif not elided and getattr(self, "_auto_tooltip", False):
            self.setToolTip("")
            self._auto_tooltip = False

    def paintEvent(self, event) -> None:  # noqa: N802 - API Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(0.4)
        hovered = self.underMouse() and self.isEnabled()
        t = self._position

        track = QRectF(1, (self.height() - self.TRACK_H) / 2, self.TRACK_W, self.TRACK_H)
        off = self._TRACK_OFF_HOVER if hovered else self._TRACK_OFF
        on = self._TRACK_ON_HOVER if hovered else self._TRACK_ON
        painter.setPen(Qt.NoPen)
        painter.setBrush(_mix(off, on, t))
        painter.drawRoundedRect(track, self.TRACK_H / 2, self.TRACK_H / 2)

        knob_d = self.TRACK_H - 6
        x = track.left() + 3 + t * (self.TRACK_W - knob_d - 6)
        knob = QRectF(x, track.top() + 3, knob_d, knob_d)
        painter.setBrush(QColor(0, 0, 0, 50))  # ombre portée discrète, comme iOS
        painter.drawEllipse(knob.translated(0, 1))
        painter.setBrush(_mix(self._KNOB_OFF, self._KNOB_ON, t))
        painter.drawEllipse(knob)

        if self.text():
            text_rect = QRectF(
                track.right() + self.GAP, 0, self.width() - track.right() - self.GAP, self.height()
            )
            painter.setPen(self.palette().color(QPalette.WindowText))
            elided = self.fontMetrics().elidedText(self.text(), Qt.ElideRight, int(text_rect.width()))
            painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, elided)


class AnimatedProgressBar(QProgressBar):
    """Barre de progression en pilule, animée -- voir le docstring du module."""

    HEIGHT = 10
    _TRACK = QColor(theme.FIELD)
    _TRACK_BORDER = QColor(theme.BORDER_SOFT)
    _START = QColor(theme.ACCENT)
    _END = QColor(theme.ACCENT_END)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTextVisible(False)
        self.setFixedHeight(self.HEIGHT)

        # Reflet : un balayage de 1,1 s, puis une pause -- assez rare pour ne
        # jamais distraire pendant des heures de traduction, assez présent
        # pour montrer que le travail avance.
        self._shine = 0.0
        sweep = QVariantAnimation(self)
        sweep.setDuration(1100)
        sweep.setStartValue(0.0)
        sweep.setEndValue(1.0)
        sweep.setEasingCurve(QEasingCurve.InOutSine)
        sweep.valueChanged.connect(self._set_shine)
        self._shine_loop = QSequentialAnimationGroup(self)
        self._shine_loop.addAnimation(sweep)
        self._shine_loop.addAnimation(QPauseAnimation(2200, self))
        self._shine_loop.setLoopCount(-1)

        # Durée inconnue (`setRange(0, 0)`) : un segment qui va et vient.
        self._busy = 0.0
        self._busy_animation = QVariantAnimation(self)
        self._busy_animation.setDuration(1300)
        self._busy_animation.setStartValue(0.0)
        self._busy_animation.setEndValue(1.0)
        self._busy_animation.setLoopCount(-1)
        self._busy_animation.valueChanged.connect(self._set_busy)

    def set_active(self, active: bool) -> None:
        """Reflet animé pendant qu'un travail tourne, immobile sinon."""
        if active and self._shine_loop.state() != QSequentialAnimationGroup.Running:
            self._shine_loop.start()
        elif not active:
            self._shine_loop.stop()
            self._set_shine(0.0)

    def _set_shine(self, value) -> None:
        self._shine = float(value)
        self.update()

    def _set_busy(self, value) -> None:
        self._busy = float(value)
        self.update()

    def _indeterminate(self) -> bool:
        return self.maximum() == self.minimum() == 0

    def hideEvent(self, event) -> None:  # noqa: N802 - API Qt
        self._busy_animation.stop()
        super().hideEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 - API Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = rect.height() / 2

        painter.setPen(QPen(self._TRACK_BORDER, 1.0))
        painter.setBrush(self._TRACK)
        painter.drawRoundedRect(rect, radius, radius)

        inner = rect.adjusted(1, 1, -1, -1)
        inner_radius = inner.height() / 2

        if self._indeterminate():
            if self._busy_animation.state() != QVariantAnimation.Running:
                self._busy_animation.start()
            segment_w = max(inner.height() * 3, inner.width() * 0.28)
            # Aller-retour adouci : 0 -> 1 -> 0 sur une boucle.
            phase = self._busy * 2 if self._busy < 0.5 else (1 - self._busy) * 2
            eased = QEasingCurve(QEasingCurve.InOutSine).valueForProgress(phase)
            x = inner.left() + (inner.width() - segment_w) * eased
            chunk = QRectF(x, inner.top(), segment_w, inner.height())
        else:
            if self._busy_animation.state() == QVariantAnimation.Running:
                self._busy_animation.stop()
            span = self.maximum() - self.minimum()
            ratio = 0.0 if span <= 0 else (self.value() - self.minimum()) / span
            ratio = max(0.0, min(1.0, ratio))
            if ratio <= 0:
                return
            # Jamais plus étroit que sa propre hauteur : une pilule, pas un ovale écrasé.
            width = max(inner.height(), inner.width() * ratio)
            chunk = QRectF(inner.left(), inner.top(), width, inner.height())

        path = QPainterPath()
        path.addRoundedRect(chunk, inner_radius, inner_radius)
        gradient = QLinearGradient(inner.topLeft(), inner.topRight())
        gradient.setColorAt(0.0, self._START)
        gradient.setColorAt(1.0, self._END)
        painter.setPen(Qt.NoPen)
        painter.fillPath(path, gradient)

        if 0.0 < self._shine < 1.0 and not self._indeterminate():
            band = max(40.0, chunk.width() * 0.25)
            center = chunk.left() - band + (chunk.width() + 2 * band) * self._shine
            shine = QLinearGradient(QPointF(center - band, 0), QPointF(center + band, 0))
            shine.setColorAt(0.0, QColor(255, 255, 255, 0))
            shine.setColorAt(0.5, QColor(255, 255, 255, 90))
            shine.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.fillPath(path, shine)
