"""
Point d'entrée de l'installeur/désinstalleur TRANSLAX.

Un seul code source, deux exécutables gelés (voir
`scripts/build_installer.py`) et quatre modes selon les arguments :

    TRANSLAX-Setup-X.Y.Z.exe                  installation interactive
    TRANSLAX-Setup-X.Y.Z.exe --silent         mise à jour intégrée (core/updater.py)
    TRANSLAX-Uninstall.exe                    désinstallation interactive
    TRANSLAX-Uninstall.exe --silent           désinstallation silencieuse (QuietUninstallString)

Le dossier d'installation à désinstaller est déduit de l'emplacement réel
du désinstalleur lui-même (il vit dans `{app}`, copié là par le Setup) --
jamais recalculé depuis le chemin par défaut, qui pourrait différer si
quelqu'un a choisi un autre dossier à l'installation.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from installer_app import operations, theme


def _install_dir_for_uninstall() -> Path:
    frozen_path = getattr(sys, "frozen", False)
    if frozen_path:
        return Path(sys.executable).resolve().parent
    return operations.default_install_dir()


def main() -> int:
    parser = argparse.ArgumentParser(description="Installeur / désinstalleur TRANSLAX")
    parser.add_argument("--silent", action="store_true", help="aucune interaction, tout se fait automatiquement")
    parser.add_argument("--uninstall", action="store_true", help="désinstalle TRANSLAX au lieu de l'installer")
    args = parser.parse_args()

    app = QApplication(sys.argv)
    app.setStyleSheet(theme.STYLESHEET)
    app.setQuitOnLastWindowClosed(True)

    # Import différé : évite de charger PySide6 avant que QApplication
    # existe (certains widgets construits au niveau module échoueraient
    # sinon en environnement gelé).
    from installer_app.ui import SetupWindow, UninstallWindow

    if args.uninstall:
        window = UninstallWindow(_install_dir_for_uninstall(), silent=args.silent)
    else:
        window = SetupWindow(silent=args.silent)

    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
