"""
Tests de l'harmonie visuelle et des animations (demande explicite de
l'utilisateur, 11/11/2026 : « applique le rayon des coins de la fenêtre
dans toute l'application », « plus d'ordre visuel », « plus d'animations »).

    python tests/test_theme.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

# Vrai moteur d'affichage (fenêtre jamais montrée, voir WA_DontShowOnScreen
# plus bas) plutôt que « offscreen » : ce dernier n'a pas de vraies polices,
# et ses largeurs de texte gonflées faussent les contrôles de débordement.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QEvent, QPointF, Qt, qInstallMessageHandler  # noqa: E402
from PySide6.QtGui import QEnterEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from core import settings  # noqa: E402
from ui import animations, theme  # noqa: E402
from ui.widgets import AnimatedProgressBar, ToggleSwitch  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK   {label}")
    else:
        print(f"  ECHEC {label} {detail}")
        failures.append(label)


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="translax_theme_"))
    real_settings_dir = settings._settings_dir  # noqa: SLF001
    settings._settings_dir = lambda: workdir / "settings"  # noqa: SLF001 - jamais les vrais réglages

    qss = (ROOT / "ui" / "styles.qss").read_text(encoding="utf-8")

    print("\n1. Un seul rayon dans toute la feuille de style")
    offenders = []
    for match in re.finditer(r"([^{}]+)\{([^}]*)\}", qss):
        selector, body = match.group(1).strip(), match.group(2)
        for value in re.findall(r"border-radius:\s*(\d+)px", body):
            value = int(value)
            if value in (theme.RADIUS, theme.RADIUS_SMALL):
                continue
            if value == 0 and "titleBar" in selector:
                continue  # boutons de la barre de titre, collés au bord : seule exception
            offenders.append(f"{selector.splitlines()[-1]} = {value}px")
    check(f"chaque border-radius vaut {theme.RADIUS}px (ou {theme.RADIUS_SMALL}px pour les petits éléments)",
          not offenders, f"({offenders})")
    check("le rayon commun est celui de la fenêtre sous Windows 11 (8 px)", theme.RADIUS == 8)

    warnings: list[str] = []
    qInstallMessageHandler(lambda _mode, _ctx, msg: warnings.append(msg))
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(qss)
    app.processEvents()
    check("la feuille de style est lue sans erreur par Qt",
          not any("stylesheet" in w.lower() for w in warnings), f"({warnings})")

    from ui.main_window import PAGE_SETTINGS, PAGE_TOOLS, PAGE_TRANSLATE, MainWindow  # noqa: E402

    window = MainWindow()
    window.setAttribute(Qt.WA_DontShowOnScreen)
    window.resize(760, 900)
    window.show()
    QTest.qWait(50)

    print("\n1b. Boutons de fenêtre imposés par le système")
    expected_style = "mac" if sys.platform == "darwin" else "windows"
    check(f"présentation « {expected_style} » sur ce système (pastilles à gauche seulement sur Mac)",
          window.title_bar.button_style == expected_style, f"({window.title_bar.button_style})")
    check("plus aucun réglage pour en changer (page Paramètres)", not hasattr(window, "window_style_combo"))

    print("\n2. Ordre visuel : une seule colonne de champs, d'une carte à l'autre")
    window._navigate_to(PAGE_TRANSLATE)
    QTest.qWait(theme.PAGE_TRANSITION_MS + 150)
    fields = {
        "langue source": window.src_combo,
        "modèle": window.model_combo,
        "dossier": window.output_edit,
        "format": window.output_format_combo,
        "modèle OCR": window.vision_model_combo,
        "clé API": window.api_key_edit,
    }
    xs = {name: w.mapTo(window, w.rect().topLeft()).x() for name, w in fields.items()}
    check("tous les champs de la page Traduire commencent au même x", len(set(xs.values())) == 1, f"({xs})")
    notes = [window.model_info_label, window.output_preview]
    note_xs = {n.mapTo(window, n.rect().topLeft()).x() for n in notes}
    check("les encadrés d'information sont alignés sur cette colonne", note_xs == set(xs.values()),
          f"({note_xs} vs {set(xs.values())})")
    check("les options sont des interrupteurs animés (même API que les cases à cocher)",
          all(isinstance(c, ToggleSwitch) for c in (window.cleanup_check, window.split_check, window.extract_only_check)))
    check("la page Traduire porte un vrai titre d'écran", window.page_title.text() == "Traduire")
    window.extract_only_check.setChecked(True)
    check("… qui devient « Extraire » en mode extraction seule", window.page_title.text() == "Extraire")
    window.extract_only_check.setChecked(False)

    print("\n3. Aucun écran ne déborde à droite, même à la largeur minimale")
    window.resize(0, 900)  # clampé par setMinimumSize
    for index, name in ((PAGE_TRANSLATE, "Traduire"), (PAGE_TOOLS, "Outils"), (PAGE_SETTINGS, "Paramètres")):
        window._navigate_to(index)
        QTest.qWait(theme.PAGE_TRANSITION_MS + 150)
        scroll = window.pages.currentWidget()
        content = scroll.widget()
        check(f"{name} : pas de défilement horizontal",
              scroll.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff
              and content.width() <= scroll.viewport().width(),
              f"(contenu {content.width()} px pour {scroll.viewport().width()} px visibles)")
    window.resize(760, 900)

    print("\n4. Transition entre écrans")
    window._navigate_to(PAGE_TOOLS)
    check("l'écran change IMMÉDIATEMENT (l'animation se joue par-dessus)",
          window.pages.currentIndex() == PAGE_TOOLS)
    check("un calque d'animation est posé pendant la transition", window._page_overlay is not None)
    QTest.qWait(theme.PAGE_TRANSITION_MS + 200)
    check("le calque disparaît une fois l'animation finie", window._page_overlay is None)
    window._navigate_to(PAGE_TRANSLATE)
    window._navigate_to(PAGE_SETTINGS)  # interrompt la précédente
    QTest.qWait(theme.PAGE_TRANSITION_MS + 200)
    check("deux transitions enchaînées : aucun calque oublié",
          window._page_overlay is None and window.pages.currentIndex() == PAGE_SETTINGS)

    print("\n5. Lueur au survol des boutons")
    window._navigate_to(PAGE_TRANSLATE)
    QTest.qWait(theme.PAGE_TRANSITION_MS + 150)
    button = window.translate_button
    glow = button.findChild(animations._HoverGlow, "_hoverGlow", Qt.FindDirectChildrenOnly)
    check("chaque bouton reçoit sa lueur automatiquement", glow is not None)
    check("… sauf le lien « ← Menu » (sans fond)",
          window.back_to_hub_button.findChild(animations._HoverGlow) is None)
    check("… et sauf les boutons de la barre de titre",
          window.title_bar.close_button.findChild(animations._HoverGlow) is None)
    app.sendEvent(button, QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
    QTest.qWait(theme.HOVER_IN_MS + 120)
    check("survol : la lueur monte en douceur jusqu'au bout", glow is not None and glow._level == 1.0)
    app.sendEvent(button, QEvent(QEvent.Leave))
    QTest.qWait(theme.HOVER_OUT_MS + 120)
    check("sortie : la lueur redescend à zéro", glow is not None and glow._level == 0.0)
    app.sendEvent(button, QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
    QTest.qWait(theme.HOVER_IN_MS + 60)
    button.hide()
    check("bouton masqué sous le curseur : lueur remise à zéro (jamais bloquée)", glow._level == 0.0)
    button.show()
    animations.install_app_effects()
    check("installer les effets deux fois ne double pas les lueurs",
          len(button.findChildren(animations._HoverGlow)) == 1)
    extra = QPushButton("Bouton créé après coup")
    extra.show()
    QTest.qWait(20)
    check("un bouton créé plus tard (boîte de dialogue…) reçoit aussi sa lueur",
          extra.findChild(animations._HoverGlow) is not None)
    extra.close()

    print("\n6. Interrupteur et barre de progression")
    switch = ToggleSwitch("Option")
    switch.show()
    switch.setChecked(True)
    check("l'interrupteur glisse (pas de saut instantané)", 0.0 <= switch._position < 1.0)
    QTest.qWait(theme.TOGGLE_MS + 120)
    check("… et arrive au bout", switch._position == 1.0)
    check("tout l'interrupteur, libellé compris, est cliquable", switch.hitButton(switch.rect().center()))
    hidden = ToggleSwitch("Caché")
    hidden.setChecked(True)
    check("changé avant affichage : directement à la bonne place", hidden._position == 1.0)
    switch.close()

    bar = AnimatedProgressBar()
    bar.resize(300, bar.HEIGHT)
    bar.show()
    for label, setup in (
        ("vide", lambda: (bar.setRange(0, 100), bar.setValue(0))),
        ("presque vide (pilule, jamais un ovale écrasé)", lambda: bar.setValue(1)),
        ("pleine", lambda: bar.setValue(100)),
        ("durée inconnue (va-et-vient)", lambda: bar.setRange(0, 0)),
    ):
        setup()
        image = bar.grab()
        check(f"barre {label} : rendu sans erreur", not image.isNull())
    check("durée inconnue : l'animation de va-et-vient tourne",
          bar._busy_animation.state() == bar._busy_animation.State.Running)
    bar.setRange(0, 100)
    bar.grab()
    check("durée connue : le va-et-vient s'arrête",
          bar._busy_animation.state() != bar._busy_animation.State.Running)
    bar.set_active(True)
    check("reflet actif pendant le travail", bar._shine_loop.state() == bar._shine_loop.State.Running)
    bar.set_active(False)
    check("reflet arrêté ensuite", bar._shine_loop.state() != bar._shine_loop.State.Running)
    bar.close()

    window.close()
    settings._settings_dir = real_settings_dir  # noqa: SLF001
    import shutil
    shutil.rmtree(workdir, ignore_errors=True)

    print()
    if failures:
        print(f"{len(failures)} test(s) en échec : " + ", ".join(failures))
        return 1
    print("Tous les tests d'harmonie visuelle et d'animation passent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
