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

# Anciens indicateurs Inno Setup (voir l'ex- installer/translax.iss et
# l'ex- core/updater.py::launch_installer_and_quit) -- une installation
# DÉJÀ EN PLACE sur la machine d'un utilisateur, construite avant ce
# nouvel installeur, lance encore sa mise à jour avec CES arguments-là :
# ce module doit continuer à les comprendre (au minimum ne pas planter,
# et déclencher le mode silencieux) tant qu'une telle version reste
# installée quelque part. Sans ce filet, une mise à jour automatique
# depuis une ancienne version échouerait en silence (argparse rejetterait
# "/SILENT" comme argument inconnu et quitterait aussitôt).
_LEGACY_SILENT_FLAGS = {"/silent", "/verysilent", "-silent", "-verysilent"}
_LEGACY_IGNORED_FLAGS = {
    "/suppressmsgboxes", "-suppressmsgboxes",
    "/norestart", "-norestart",
    "/closeapplications", "-closeapplications",
    "/nocancel", "-nocancel",
}


def _normalize_legacy_argv(argv: list[str]) -> list[str]:
    normalized = []
    for arg in argv:
        lowered = arg.lower()
        if lowered in _LEGACY_SILENT_FLAGS:
            normalized.append("--silent")
        elif lowered in _LEGACY_IGNORED_FLAGS:
            continue
        else:
            normalized.append(arg)
    return normalized


def _install_dir_for_uninstall() -> Path:
    frozen_path = getattr(sys, "frozen", False)
    if frozen_path:
        return Path(sys.executable).resolve().parent
    return operations.default_install_dir()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Installeur / désinstalleur TRANSLAX")
    parser.add_argument("--silent", action="store_true", help="aucune interaction, tout se fait automatiquement")
    parser.add_argument("--uninstall", action="store_true", help="désinstalle TRANSLAX au lieu de l'installer")
    return parser.parse_args(_normalize_legacy_argv(argv))


def should_uninstall(args: argparse.Namespace) -> bool:
    """
    Faut-il désinstaller plutôt qu'installer ?

    Deux façons d'arriver à "oui", et les DEUX comptent, séparément
    testées : `--uninstall` explicite, OU ce build précis n'a de toute
    façon jamais de payload à installer (`TRANSLAX-Uninstall.exe`,
    construit par `uninstaller_app.spec`, SANS `dist/TRANSLAX.exe`
    embarqué). Ce filet de sécurité a été ajouté après un bug réel
    constaté en testant ceci pour de vrai : l'entrée de registre écrite
    par `operations.register_uninstall_entry` ne passait d'abord pas
    `--uninstall` du tout -- "Désinstaller" depuis Windows tentait alors
    une INSTALLATION, qui échouait aussitôt (ou restait bloquée sur un
    écran d'erreur invisible) faute de payload. Corrigé aux DEUX endroits
    à la fois : la chaîne de registre inclut maintenant `--uninstall`, et
    ce filet fait qu'un oubli similaire ne casserait plus rien.
    """
    return args.uninstall or not operations.bundled_app_exe_path().exists()


def build_window(args: argparse.Namespace):
    # Import différé : évite de charger PySide6 avant que QApplication
    # existe (certains widgets construits au niveau module échoueraient
    # sinon en environnement gelé).
    from installer_app.ui import SetupWindow, UninstallWindow

    if should_uninstall(args):
        return UninstallWindow(_install_dir_for_uninstall(), silent=args.silent)
    return SetupWindow(silent=args.silent)


def main() -> int:
    args = parse_args(sys.argv[1:])

    app = QApplication(sys.argv)
    app.setStyleSheet(theme.STYLESHEET)
    app.setQuitOnLastWindowClosed(True)

    window = build_window(args)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
