"""
Automatise une publication complète de TRANSLAX -- demande explicite de
l'utilisateur, 27/08/2026 : « un système de mise à jour bien rodé dès le
départ afin que mes futures changements se fassent confortablement ».

Enchaîne, dans l'ordre, tout ce qu'il faut pour qu'une nouvelle version
soit réellement disponible via « Chercher une mise à jour » dans l'appli
elle-même (voir core/updater.py) :

    1. Bump de version (core/version.py, voir scripts/stamp_build_date.py)
    2. Construction de l'exe (PyInstaller)
    3. Construction de l'installeur (Inno Setup, voir scripts/build_installer.py)
    4. Commit + tag Git + push vers GitHub
    5. Publication d'une GitHub Release avec l'installeur joint (CLI `gh`)

S'arrête au premier échec plutôt que de continuer sur un état incohérent
(ex. publier une release GitHub sans avoir vraiment reconstruit l'exe, ou
pousser un tag qui ne correspond à aucun exe réel).

Nécessite : Inno Setup 6 installé (voir INSTALLER_BUILD.md), le CLI `gh`
déjà authentifié (`gh auth status`) avec accès en écriture au dépôt, et
TRANSLAX FERMÉ (l'exe en cours d'exécution ne peut pas être reconstruit).

    python scripts/release.py --bump minor
    python scripts/release.py --bump patch --dry-run
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))


def _translax_is_running() -> bool:
    """
    `tasklist` (pas de dépendance externe comme `psutil`) -- vrai
    seulement si un processus nommé TRANSLAX.exe tourne réellement,
    jamais une supposition.
    """
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq TRANSLAX.exe"],
            capture_output=True, text=True, timeout=10,
        )
    except OSError:
        return False  # tasklist introuvable (jamais le cas sur Windows) -- ne bloque pas la publication
    return "TRANSLAX.exe" in result.stdout


def _run(cmd: list[str], *, cwd: Path | None = None, dry_run: bool = False) -> None:
    printable = " ".join(cmd)
    print(f"$ {printable}")
    if dry_run:
        return
    result = subprocess.run(cmd, cwd=str(cwd or ROOT))
    if result.returncode != 0:
        raise SystemExit(f"ERREUR : commande échouée (code {result.returncode}) : {printable}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bump", choices=["patch", "minor", "major"], default="patch",
                         help="type d'incrément de version (voir scripts/stamp_build_date.py)")
    parser.add_argument("--dry-run", action="store_true",
                         help="affiche les commandes sans les exécuter (sauf la lecture de la version)")
    parser.add_argument("--notes", default="",
                         help="notes de publication pour la GitHub Release (sinon message générique)")
    args = parser.parse_args()

    print("=== 0. Vérifications préalables ===")
    if _translax_is_running() and not args.dry_run:
        raise SystemExit(
            "ERREUR : TRANSLAX est actuellement en cours d'exécution -- ferme-le d'abord "
            "(l'exe en cours ne peut pas être reconstruit, il est verrouillé par Windows)."
        )

    print("\n=== 1. Bump de version ===")
    _run([sys.executable, "scripts/stamp_build_date.py", "--bump", args.bump], dry_run=args.dry_run)

    # Relit la version fraîchement écrite -- jamais mise en cache d'avant
    # le bump, sinon tout le reste de ce script publierait sous l'ancien
    # numéro.
    if args.dry_run:
        from core.version import VERSION  # lecture seule, la vraie version reste inchangée en dry-run
    else:
        import importlib
        import core.version as version_mod
        importlib.reload(version_mod)
        VERSION = version_mod.VERSION
    print(f"Version : {VERSION}")

    print("\n=== 2. Construction de l'exe (PyInstaller) ===")
    _run([sys.executable, "-m", "PyInstaller", "TRANSLAX.spec", "--noconfirm"], dry_run=args.dry_run)

    print("\n=== 3. Construction de l'installeur (Inno Setup) ===")
    _run([sys.executable, "scripts/build_installer.py"], dry_run=args.dry_run)

    installer_path = ROOT / "dist_installer" / f"TRANSLAX-Setup-{VERSION}.exe"
    if not args.dry_run and not installer_path.exists():
        raise SystemExit(f"ERREUR : {installer_path} attendu mais introuvable après la construction.")

    print("\n=== 4. Commit + tag Git + push ===")
    _run(["git", "add", "-A"], dry_run=args.dry_run)
    _run(["git", "commit", "-m", f"Version {VERSION}"], dry_run=args.dry_run)
    _run(["git", "tag", f"v{VERSION}"], dry_run=args.dry_run)
    _run(["git", "push"], dry_run=args.dry_run)
    _run(["git", "push", "origin", f"v{VERSION}"], dry_run=args.dry_run)

    print("\n=== 5. Publication de la GitHub Release ===")
    notes = args.notes or f"Voir SPEC.md pour le détail des changements de la version {VERSION}."
    _run(
        [
            "gh", "release", "create", f"v{VERSION}", str(installer_path),
            "--title", f"TRANSLAX v{VERSION}",
            "--notes", notes,
        ],
        dry_run=args.dry_run,
    )

    print(f"\nPublication terminée : TRANSLAX v{VERSION} est maintenant disponible via "
          "« Chercher une mise à jour » dans l'appli.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
