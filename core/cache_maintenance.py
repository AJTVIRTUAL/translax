"""
Gestion des dossiers de cache `.translax` (demande explicite de
l'utilisateur, 26/08/2026 : « permet moi de gérer les paths en lien avec
le ocr json ») -- c'est-à-dire les dossiers cachés que `core/state.py` et
`core/vision_ocr.py` créent à côté de chaque fichier de sortie pour
pouvoir reprendre une traduction interrompue sans tout refaire (texte déjà
extrait par OCR compris, voir `vision_cache` dans `core/pipeline.py`).

Ce module ne fait QUE retrouver et supprimer ces dossiers : il ne change
jamais leur emplacement (calculé par `state.work_dir`, toujours à côté du
fichier de sortie concerné -- une relocalisation globale casserait la
logique de reprise, qui cherche spécifiquement `.translax` à côté de
CHAQUE fichier). « Gérer » ici veut dire : voir où ils sont, combien de
place ils prennent, et pouvoir les vider une fois les traductions
terminées -- pas les déplacer.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from . import state as state_mod

WORK_DIR_NAME = state_mod.WORK_DIR_NAME  # ".translax" -- une seule source de vérité


@dataclass
class CacheScanResult:
    dirs: list[Path]
    total_bytes: int

    @property
    def count(self) -> int:
        return len(self.dirs)


def _dir_size(path: Path) -> int:
    total = 0
    for f in path.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            # Fichier supprimé/verrouillé entre le rglob et le stat --
            # ignoré plutôt que de faire échouer tout le calcul de taille.
            continue
    return total


def find_cache_dirs(root: Path) -> CacheScanResult:
    """
    Cherche récursivement tous les dossiers `.translax` sous `root` (le
    dossier de sortie par défaut, typiquement) -- un par fichier
    déjà traduit ou en cours. `root` inexistant ou illisible renvoie un
    résultat vide plutôt que de lever une exception.
    """
    dirs: list[Path] = []
    total_bytes = 0
    try:
        if not root.is_dir():
            return CacheScanResult(dirs=[], total_bytes=0)
        for candidate in root.rglob(WORK_DIR_NAME):
            if candidate.is_dir():
                dirs.append(candidate)
                total_bytes += _dir_size(candidate)
    except OSError:
        return CacheScanResult(dirs=dirs, total_bytes=total_bytes)
    return CacheScanResult(dirs=dirs, total_bytes=total_bytes)


def clear_cache_dirs(dirs: list[Path]) -> tuple[int, list[str]]:
    """
    Supprime chaque dossier de `dirs`. Ne s'arrête jamais au premier échec
    (fichier verrouillé par un job en cours, permissions...) -- continue
    sur les suivants et renvoie (nombre réellement supprimé, erreurs
    rencontrées) pour un affichage honnête plutôt qu'un simple succès/échec
    global.
    """
    removed = 0
    errors: list[str] = []
    for d in dirs:
        try:
            shutil.rmtree(d)
            removed += 1
        except OSError as exc:
            errors.append(f"{d} : {exc}")
    return removed, errors


def format_size(num_bytes: int) -> str:
    """Format lisible simple (Ko/Mo/Go) -- pas besoin de plus fin ici."""
    size = float(num_bytes)
    for unit in ("o", "Ko", "Mo", "Go"):
        if size < 1024 or unit == "Go":
            return f"{size:.1f} {unit}" if unit != "o" else f"{int(size)} {unit}"
        size /= 1024
    return f"{size:.1f} Go"
