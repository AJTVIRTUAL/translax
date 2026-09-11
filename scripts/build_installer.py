"""
Construit l'installeur Windows de TRANSLAX -- entièrement codé pour ce
projet (PySide6, voir `installer_app/`), pas la fenêtre native de
Windows -- demande explicite de l'utilisateur (11/09/2026). Remplace la
version précédente de ce script, qui invoquait Inno Setup (ISCC.exe) ;
`installer/translax.iss` reste dans le dépôt pour mémoire, mais n'est
plus utilisé.

Deux exécutables construits dans le bon ordre, chacun avec son propre
fichier `.spec` :
  1. `TRANSLAX-Uninstall.exe` (uninstaller_app.spec) -- SANS le payload
     de l'application, juste la mécanique de désinstallation ;
  2. `TRANSLAX-Setup-{VERSION}.exe` (installer_app.spec) -- embarque
     `dist/TRANSLAX.exe` ET le désinstalleur construit à l'étape 1 comme
     données, pour pouvoir copier ce dernier dans le dossier
     d'installation au moment de l'installation.

Lit la version depuis `core/version.py` (une seule source de vérité) et
l'écrit dans `installer_app/version.txt` juste avant chaque build --
c'est ce fichier, embarqué comme donnée par les deux `.spec`, que
`installer_app/operations.py::bundled_version()` relit une fois gelé.

Ne construit PAS `dist/TRANSLAX.exe` lui-même : suppose l'exe principal
déjà construit et à jour via `pyinstaller TRANSLAX.spec` (voir
`scripts/stamp_build_date.py` pour la version + le build de l'exe) --
vérifié explicitement avant de continuer, jamais empaqueté un exe périmé
ou manquant en silence.

    python scripts/build_installer.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.version import VERSION  # noqa: E402


def _run_pyinstaller(spec_name: str) -> None:
    cmd = [sys.executable, "-m", "PyInstaller", spec_name, "--noconfirm"]
    print("Construction :", " ".join(cmd))
    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        raise SystemExit(f"ERREUR : PyInstaller a échoué sur {spec_name} (code {result.returncode}).")


def main() -> int:
    exe_path = ROOT / "dist" / "TRANSLAX.exe"
    if not exe_path.exists():
        print(
            f"ERREUR : {exe_path} n'existe pas -- construis d'abord l'exe principal "
            "(python -m PyInstaller TRANSLAX.spec) avant l'installeur."
        )
        return 1

    # Régénéré à chaque build, jamais recopié à la main dans installer_app/ :
    # les deux .spec l'embarquent comme donnée, relue par
    # operations.bundled_version() une fois gelé.
    (ROOT / "installer_app" / "version.txt").write_text(VERSION, encoding="utf-8")

    print("=== 1. Désinstalleur (TRANSLAX-Uninstall.exe, sans le payload) ===")
    _run_pyinstaller("uninstaller_app.spec")
    uninstaller_path = ROOT / "dist" / "TRANSLAX-Uninstall.exe"
    if not uninstaller_path.exists():
        print(f"ERREUR : {uninstaller_path} attendu mais introuvable après la construction.")
        return 1

    print("\n=== 2. Installeur (TRANSLAX-Setup, avec le payload + le désinstalleur) ===")
    _run_pyinstaller("installer_app.spec")

    raw_setup_path = ROOT / "dist" / "TRANSLAX-Setup.exe"
    if not raw_setup_path.exists():
        print(f"ERREUR : {raw_setup_path} attendu mais introuvable après la construction.")
        return 1

    output_dir = ROOT / "dist_installer"
    output_dir.mkdir(exist_ok=True)
    versioned_path = output_dir / f"TRANSLAX-Setup-{VERSION}.exe"
    versioned_path.write_bytes(raw_setup_path.read_bytes())

    size_mo = versioned_path.stat().st_size / 1024 / 1024
    print(f"\nInstalleur créé : {versioned_path} ({size_mo:.1f} Mo)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
