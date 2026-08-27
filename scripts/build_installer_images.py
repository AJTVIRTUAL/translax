"""
Génère les images de personnalisation de l'installeur Windows (Inno
Setup) -- demande explicite de l'utilisateur, 27/08/2026 : « styliser...
et le personnaliser au maximum... afin de se sentir immergé dans
l'application/logiciel ». Réutilise la palette et le logo de TRANSLAX
(fond sombre, accent bleu -- voir ui/styles.qss) plutôt que les images
grises par défaut d'Inno Setup.

Utilise Qt (déjà une dépendance du projet) pour dessiner, pas Pillow --
une dépendance de moins à gérer. Produit deux fichiers `.bmp` (format
exigé par Inno Setup) directement dans `installer/`, référencés tels
quels par `installer/translax.iss` (`WizardImageFile`/`WizardSmallImageFile`).

    python scripts/build_installer_images.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "installer"

# Même famille de couleurs que ui/styles.qss -- l'installeur ne doit pas
# ressembler à un logiciel générique, mais annoncer TRANSLAX dès le
# premier écran.
BG_DARK = QColor("#0d1117")
ACCENT = QColor("#3b7dfb")
TEXT_LIGHT = QColor("#e6eaf2")
TEXT_MUTED = QColor("#8b96a8")

# Tailles recommandées par Inno Setup (WizardImageFile / WizardSmallImageFile).
WIZARD_IMAGE_SIZE = (164, 314)
WIZARD_SMALL_SIZE = (55, 58)


def _draw_banner(size: tuple[int, int], icon_path: Path) -> QImage:
    width, height = size
    image = QImage(width, height, QImage.Format_RGB32)
    image.fill(BG_DARK)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)

    # Bande d'accent verticale à gauche -- rappel discret du bleu de
    # l'appli, sans surcharger une image somme toute petite et haute.
    painter.fillRect(QRect(0, 0, 4, height), ACCENT)

    icon = QIcon(str(icon_path))
    icon_size = min(width - 40, 88)
    pixmap = icon.pixmap(icon_size, icon_size)
    x = (width - icon_size) // 2
    painter.drawPixmap(x, 44, pixmap)

    painter.setPen(TEXT_LIGHT)
    painter.setFont(QFont("Segoe UI", 15, QFont.Bold))
    painter.drawText(QRect(0, 44 + icon_size + 18, width, 30), Qt.AlignCenter, "TRANSLAX")

    painter.setPen(TEXT_MUTED)
    painter.setFont(QFont("Segoe UI", 8))
    painter.drawText(
        QRect(10, height - 70, width - 20, 60),
        Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap,
        "Traduction locale de documents volumineux",
    )
    painter.end()
    return image


def _draw_small_logo(size: tuple[int, int], icon_path: Path) -> QImage:
    width, height = size
    image = QImage(width, height, QImage.Format_RGB32)
    image.fill(BG_DARK)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    icon = QIcon(str(icon_path))
    icon_size = min(width, height) - 12
    pixmap = icon.pixmap(icon_size, icon_size)
    x = (width - icon_size) // 2
    y = (height - icon_size) // 2
    painter.drawPixmap(x, y, pixmap)
    painter.end()
    return image


def main() -> int:
    app = QApplication.instance() or QApplication([])  # noqa: F841 - requis pour QPainter hors GUI
    icon_path = ROOT / "ui" / "icon.ico"
    if not icon_path.exists():
        print(f"ERREUR : {icon_path} introuvable.")
        return 1

    banner_path = OUT_DIR / "wizard_image.bmp"
    _draw_banner(WIZARD_IMAGE_SIZE, icon_path).save(str(banner_path), "BMP")
    print(f"Créé : {banner_path}")

    small_path = OUT_DIR / "wizard_small_image.bmp"
    _draw_small_logo(WIZARD_SMALL_SIZE, icon_path).save(str(small_path), "BMP")
    print(f"Créé : {small_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
