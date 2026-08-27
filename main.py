"""
TRANSLAX — point d'entrée de l'application de bureau.

    python main.py

Une fois packagé (voir SPEC.md §8), c'est ce fichier que PyInstaller
transforme en TRANSLAX.exe.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QSplashScreen

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ui.main_window import MainWindow  # noqa: E402

# Éditeur affiché sur l'écran de démarrage (demande explicite de
# l'utilisateur, 25/08/2026) -- pas ailleurs dans l'appli pour l'instant.
PUBLISHER = "AJTWS — Amilcar Joao"


def resource_path(relative: str) -> Path:
    """
    Chemin d'une ressource, que l'app tourne depuis les sources ou depuis
    l'exécutable PyInstaller (qui déplie ses fichiers dans un dossier
    temporaire exposé via sys._MEIPASS).
    """
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative


def _build_splash_pixmap(icon_path: Path) -> QPixmap:
    """
    Petite zone rectangulaire dessinée à la volée (pas un fichier image à
    part) : logo centré, nom du logiciel, éditeur -- demande explicite de
    l'utilisateur. Couleurs reprises telles quelles de `ui/styles.qss`
    (fond `#12151c`, bordure de carte `#262d3a`, texte `#e6eaf2`) pour que
    ce premier écran ait déjà le thème de l'appli, avant même que la
    feuille de style ne soit chargée.
    """
    width, height = 420, 260
    pixmap = QPixmap(width, height)
    pixmap.fill(QColor("#12151c"))

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor("#262d3a"), 1))
    painter.drawRect(0, 0, width - 1, height - 1)

    if icon_path.exists():
        logo = QIcon(str(icon_path)).pixmap(72, 72)
        painter.drawPixmap((width - 72) // 2, 38, logo)

    painter.setPen(QColor("#ffffff"))
    title_font = QFont("Segoe UI", 20, QFont.Bold)
    title_font.setLetterSpacing(QFont.AbsoluteSpacing, 3)
    painter.setFont(title_font)
    painter.drawText(QRect(0, 122, width, 36), Qt.AlignCenter, "TRANSLAX")

    painter.setPen(QColor("#8b96a8"))
    painter.setFont(QFont("Segoe UI", 10))
    painter.drawText(QRect(0, 160, width, 20), Qt.AlignCenter, "Chargement…")

    painter.setPen(QColor("#4a5265"))
    painter.setFont(QFont("Segoe UI", 9))
    painter.drawText(QRect(0, height - 32, width, 20), Qt.AlignCenter, PUBLISHER)

    painter.end()
    return pixmap


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("TRANSLAX")
    app.setApplicationDisplayName("TRANSLAX")

    icon_path = resource_path("ui/icon.ico")

    # Affiché en tout premier, avant même la feuille de style et la
    # construction de la fenêtre principale -- c'est justement l'attente
    # pendant CES étapes qu'il sert à couvrir. `processEvents()` force son
    # affichage réel immédiatement : sans lui, Qt ne peindrait le splash
    # qu'au prochain passage dans la boucle d'évènements, potentiellement
    # après que `MainWindow()` ait déjà fini de se construire.
    splash = QSplashScreen(_build_splash_pixmap(icon_path))
    splash.show()
    app.processEvents()

    stylesheet = resource_path("ui/styles.qss")
    if stylesheet.exists():
        app.setStyleSheet(stylesheet.read_text(encoding="utf-8"))

    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))  # icône de la barre des tâches

    window = MainWindow()
    window.setWindowIcon(app.windowIcon())  # icône du coin haut-gauche de la fenêtre
    window.show()
    splash.finish(window)  # referme le splash dès que la vraie fenêtre est affichée
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
