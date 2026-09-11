"""
Mécanique réelle de l'installeur -- tout ce qui touche vraiment au disque,
au registre ou à des processus. `ui.py` ne fait qu'appeler ces fonctions et
afficher leur progression ; aucune décision "métier" n'est prise côté
interface.

Installation PAR UTILISATEUR (comme l'ancien installeur Inno Setup) :
aucun droit administrateur nécessaire.
  - Dossier : `%LOCALAPPDATA%\\Programs\\TRANSLAX` -- EXACTEMENT le même
    chemin que l'ancien installeur (`installer/translax.iss`,
    `DefaultDirName={localappdata}\\Programs\\{#MyAppName}`), pour qu'une
    mise à jour depuis une installation existante retombe sur les mêmes
    fichiers, pas un second dossier orphelin à côté.
  - Registre : `HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\
    Uninstall\\TRANSLAX` -- ce projet choisit son propre nom de clé
    (pas de GUID Inno à reproduire) ; `_remove_legacy_inno_entry` nettoie
    en plus, du mieux possible, l'ancienne entrée Inno si elle existe sur
    une machine qui avait l'ancien installeur.

Raccourcis créés via PowerShell + `WScript.Shell` (COM), PAS `pywin32` :
aucune dépendance Python supplémentaire à empaqueter dans l'exe gelé --
`powershell.exe` existe nativement sur tout Windows pris en charge par ce
projet.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import winreg
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "TRANSLAX"
EXE_NAME = "TRANSLAX.exe"
UNINSTALL_EXE_NAME = "TRANSLAX-Uninstall.exe"
PUBLISHER = "AJTVIRTUAL - AMILCAR JOAO"

UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\TRANSLAX"
# Clé de l'ANCIEN installeur Inno Setup (voir installer/translax.iss ::
# AppId) -- Inno écrit ses entrées "par utilisateur" sous ce même chemin,
# avec "{GUID}_is1" comme nom de sous-clé.
_LEGACY_INNO_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{58A331BB-FF3E-45BC-931E-B02E02038A70}_is1"

COPY_CHUNK_SIZE = 4 * 1024 * 1024  # 4 Mo -- assez gros pour ne pas ralentir la copie d'un fichier de ~500 Mo


class InstallError(Exception):
    """Erreur réelle rencontrée pendant l'installation/désinstallation --
    toujours un message déjà lisible, jamais une exception brute."""


@dataclass
class Paths:
    install_dir: Path
    exe_path: Path
    uninstall_exe_path: Path
    icon_path: Path


def default_install_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise InstallError("Variable d'environnement LOCALAPPDATA introuvable -- Windows requis.")
    return Path(local_app_data) / "Programs" / APP_NAME


def paths_for(install_dir: Path) -> Paths:
    return Paths(
        install_dir=install_dir,
        exe_path=install_dir / EXE_NAME,
        uninstall_exe_path=install_dir / UNINSTALL_EXE_NAME,
        icon_path=install_dir / "icon.ico",
    )


# ---------------------------------------------------------------------------
# Emplacement des données embarquées (le payload TRANSLAX.exe, l'icône, et
# -- pour le Setup seulement -- le petit désinstalleur déjà construit).
# ---------------------------------------------------------------------------


def _bundle_root() -> Path:
    """
    Dossier depuis lequel lire les fichiers embarqués.

    `sys._MEIPASS` existe seulement une fois l'exe gelé par PyInstaller
    (onefile) : c'est le dossier d'extraction temporaire créé au
    démarrage. En développement (pas encore gelé), on retombe sur le
    dossier du projet -- utile pour itérer sur l'interface sans attendre
    un build PyInstaller complet (plusieurs minutes, voir SPEC.md) à
    chaque essai.
    """
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root is not None:
        return Path(frozen_root)
    return Path(__file__).resolve().parent.parent


def bundled_app_exe_path() -> Path:
    root = _bundle_root()
    frozen = getattr(sys, "_MEIPASS", None)
    return (root / "payload" / EXE_NAME) if frozen is not None else (root / "dist" / EXE_NAME)


def bundled_uninstaller_path() -> Path | None:
    """None dans le désinstalleur lui-même (il n'embarque pas sa propre
    copie) -- seul le Setup embarque ce fichier."""
    root = _bundle_root()
    frozen = getattr(sys, "_MEIPASS", None)
    candidate = (root / "payload" / UNINSTALL_EXE_NAME) if frozen is not None \
        else (root / "dist" / UNINSTALL_EXE_NAME)
    return candidate if candidate.exists() else None


def bundled_icon_path() -> Path:
    root = _bundle_root()
    frozen = getattr(sys, "_MEIPASS", None)
    return (root / "icon.ico") if frozen is not None else (root / "ui" / "icon.ico")


def bundled_version() -> str:
    """
    Lit `version.txt`, écrit à côté du code source par
    `scripts/build_installer.py` juste avant de geler l'exe (voir ce
    script) -- une seule source de vérité (`core/version.py`), jamais
    recopiée à la main dans ce paquet.
    """
    root = _bundle_root()
    frozen = getattr(sys, "_MEIPASS", None)
    version_file = (root / "version.txt") if frozen is not None else (root / "installer_app" / "version.txt")
    if version_file.exists():
        return version_file.read_text(encoding="utf-8").strip()
    return "0.0.0"


# ---------------------------------------------------------------------------
# Processus TRANSLAX en cours
# ---------------------------------------------------------------------------


def is_translax_running() -> bool:
    """`tasklist`, pas de dépendance externe -- même principe que
    `scripts/release.py::_translax_is_running`."""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {EXE_NAME}"],
            capture_output=True, text=True, timeout=10,
        )
    except OSError:
        return False
    return EXE_NAME in result.stdout


def close_running_translax(timeout_s: float = 8.0) -> bool:
    """
    Ferme TRANSLAX s'il tourne encore -- filet de sécurité : la mise à
    jour intégrée (voir `core/updater.py`) ferme déjà l'application
    APPELANTE avant de lancer cet installeur ; ceci ne fait donc
    normalement rien en pratique, sauf si une AUTRE instance de TRANSLAX
    tournait en parallèle.

    D'abord une fermeture "propre" (`taskkill` sans `/F`, laisse
    l'application se terminer elle-même), puis, si elle tourne encore
    après `timeout_s`, une fermeture forcée. Retourne True si plus aucune
    instance ne tourne à la fin (qu'il y en ait eu une à fermer ou non).
    """
    if not is_translax_running():
        return True
    subprocess.run(["taskkill", "/IM", EXE_NAME], capture_output=True, timeout=10)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not is_translax_running():
            return True
        time.sleep(0.5)
    subprocess.run(["taskkill", "/F", "/IM", EXE_NAME], capture_output=True, timeout=10)
    time.sleep(0.5)
    return not is_translax_running()


# ---------------------------------------------------------------------------
# Copie du payload, avec progression réelle
# ---------------------------------------------------------------------------


def copy_with_progress(src: Path, dest: Path, on_progress=None, should_stop=None) -> None:
    """
    Copie `src` vers `dest` par blocs de 4 Mo, en rapportant `(fait,
    total)` -- le fichier fait environ 500 Mo (voir SPEC.md), une copie
    d'un coup (`shutil.copyfile`) laisserait la barre de progression
    figée pendant plusieurs secondes sans nouvelle donnée à afficher.

    Écrit d'abord dans un fichier temporaire puis renomme à la fin :
    une interruption (annulation, panne) ne laisse jamais un `.exe` à
    moitié copié sous son vrai nom.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_dest = dest.with_suffix(dest.suffix + ".part")
    total = src.stat().st_size
    done = 0
    try:
        with src.open("rb") as fsrc, tmp_dest.open("wb") as fdst:
            while True:
                if should_stop is not None and should_stop():
                    raise InstallError("Installation annulée.")
                chunk = fsrc.read(COPY_CHUNK_SIZE)
                if not chunk:
                    break
                fdst.write(chunk)
                done += len(chunk)
                if on_progress is not None:
                    on_progress(done, total)
    except InstallError:
        tmp_dest.unlink(missing_ok=True)
        raise
    except OSError as exc:
        tmp_dest.unlink(missing_ok=True)
        raise InstallError(f"Échec de la copie de {src.name} : {exc}") from exc
    # Remplace l'ancien exe (verrouillé jusqu'ici seulement s'il tournait
    # encore -- voir close_running_translax, appelé avant ceci).
    tmp_dest.replace(dest)


# ---------------------------------------------------------------------------
# Raccourcis (PowerShell + WScript.Shell, aucune dépendance Python de plus)
# ---------------------------------------------------------------------------


def _run_powershell(script: str) -> None:
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=20,
    )
    if result.returncode != 0:
        raise InstallError(f"Commande PowerShell échouée : {result.stderr.strip() or result.stdout.strip()}")


def create_shortcut(shortcut_path: Path, target: Path, icon_path: Path, description: str) -> None:
    shortcut_path.parent.mkdir(parents=True, exist_ok=True)
    # Guillemets simples PowerShell (littéraux) : les chemins Windows
    # contiennent des antislashs, jamais de guillemets -- pas de risque
    # d'injection à échapper ici au-delà de l'apostrophe elle-même.
    script = (
        "$shell = New-Object -ComObject WScript.Shell; "
        f"$sc = $shell.CreateShortcut('{shortcut_path}'); "
        f"$sc.TargetPath = '{target}'; "
        f"$sc.WorkingDirectory = '{target.parent}'; "
        f"$sc.IconLocation = '{icon_path}'; "
        f"$sc.Description = '{description}'; "
        "$sc.Save()"
    )
    _run_powershell(script)


def start_menu_shortcut_path() -> Path:
    app_data = os.environ.get("APPDATA")
    if not app_data:
        raise InstallError("Variable d'environnement APPDATA introuvable.")
    return Path(app_data) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / f"{APP_NAME}.lnk"


def desktop_shortcut_path() -> Path:
    # `USERPROFILE\Desktop` reste correct même si l'utilisateur a redirigé
    # son Bureau vers OneDrive -- Windows garde ce nom de dossier soit à
    # l'un soit à l'autre emplacement, jamais les deux à la fois.
    user_profile = os.environ.get("USERPROFILE")
    if not user_profile:
        raise InstallError("Variable d'environnement USERPROFILE introuvable.")
    onedrive_desktop = Path(os.environ.get("OneDrive", "")) / "Desktop" if os.environ.get("OneDrive") else None
    if onedrive_desktop is not None and onedrive_desktop.exists():
        return onedrive_desktop / f"{APP_NAME}.lnk"
    return Path(user_profile) / "Desktop" / f"{APP_NAME}.lnk"


# ---------------------------------------------------------------------------
# Registre : entrée « Applications » / « Programmes et fonctionnalités »
# ---------------------------------------------------------------------------


def register_uninstall_entry(version: str, install_dir: Path, icon_path: Path, uninstall_exe: Path) -> None:
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
        def s(name: str, value: str) -> None:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)

        s("DisplayName", APP_NAME)
        s("DisplayVersion", version)
        s("Publisher", PUBLISHER)
        s("DisplayIcon", str(icon_path))
        s("InstallLocation", str(install_dir))
        # --uninstall est INDISPENSABLE ici : TRANSLAX-Uninstall.exe partage
        # son code (installer_app/main.py) avec le Setup, et ne sait laquelle
        # des deux choses faire qu'en lisant cet argument -- un vrai bug
        # rencontré en testant ceci pour de vrai : sans lui, "Désinstaller"
        # depuis Windows tentait une INSTALLATION (le seul comportement par
        # défaut de main.py), qui échouait aussitôt faute de payload
        # embarqué dans ce petit exécutable-là.
        s("UninstallString", f'"{uninstall_exe}" --uninstall')
        s("QuietUninstallString", f'"{uninstall_exe}" --uninstall --silent')
        winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)
        size_kb = 0
        try:
            size_kb = sum(f.stat().st_size for f in install_dir.rglob("*") if f.is_file()) // 1024
        except OSError:
            pass
        if size_kb:
            winreg.SetValueEx(key, "EstimatedSize", 0, winreg.REG_DWORD, size_kb)


def unregister_uninstall_entry() -> None:
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except FileNotFoundError:
        pass


def cleanup_legacy_inno_entry() -> None:
    """
    Retire l'entrée de l'ANCIEN installeur (Inno Setup) si elle existe --
    best-effort, jamais bloquant : sur une machine qui avait l'ancien
    installeur, évite une entrée fantôme en double dans « Programmes et
    fonctionnalités » une fois passé au nouveau. N'efface aucun FICHIER
    (les fichiers de l'ancien installeur sont de toute façon écrasés au
    même endroit par cette même installation), seulement cette clé de
    registre.
    """
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, _LEGACY_INNO_KEY)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Lancer / auto-suppression différée (désinstallation)
# ---------------------------------------------------------------------------


def launch(exe_path: Path) -> None:
    subprocess.Popen([str(exe_path)], close_fds=True, cwd=str(exe_path.parent))


def remove_shortcuts() -> None:
    for path in (desktop_shortcut_path(), start_menu_shortcut_path()):
        path.unlink(missing_ok=True)


def remove_directory_deferred(install_dir: Path) -> None:
    """
    Supprime `install_dir` -- y compris ce désinstalleur lui-même, qui y
    vit -- APRÈS que ce processus se soit terminé.

    Un exe ne peut pas se supprimer lui-même pendant qu'il tourne (Windows
    verrouille le fichier tant qu'un processus l'exécute). Technique
    standard, la même que celle des désinstalleurs Inno Setup : un
    processus détaché (`cmd.exe`, indépendant de celui-ci) attend
    brièvement que ce processus-ci se termine (`ping` vers la boucle
    locale, façon universelle de patienter sans dépendre d'un terminal
    interactif -- contrairement à `timeout`), puis supprime le dossier.

    Passe par un vrai petit SCRIPT .bat temporaire plutôt qu'une ligne
    `cmd /c "a & b"` construite à la main -- constaté réellement en testant
    ceci : `subprocess.Popen(["cmd.exe", "/c", commande])` ré-échappe la
    commande via `list2cmdline`, et les guillemets d'un chemin (déjà lui-
    même entre guillemets pour tolérer les espaces) entrent en conflit avec
    ceux ajoutés autour de la commande entière -- cmd.exe refusait alors
    tout, avec « la syntaxe du nom de fichier... est incorrecte », et NE
    SUPPRIMAIT RIEN. Un fichier .bat n'a pas ce problème : chaque ligne est
    lue par cmd.exe directement, sans ré-échappement Python entre les deux.
    Le script écrit dans %TEMP% (jamais dans `install_dir` lui-même,
    sans quoi il tenterait de se supprimer avant d'avoir fini) et se
    supprime lui-même une fois le ménage terminé (`del "%~f0"`).
    """
    script_path = Path(tempfile.gettempdir()) / f"translax_uninstall_{os.getpid()}.bat"
    script_path.write_text(
        "@echo off\r\n"
        "ping -n 2 127.0.0.1 >nul\r\n"
        f'rmdir /s /q "{install_dir}"\r\n'
        'del "%~f0"\r\n',
        encoding="utf-8",
    )
    subprocess.Popen(
        ["cmd.exe", "/c", str(script_path)],
        creationflags=subprocess.CREATE_NO_WINDOW,
        close_fds=True,
    )
