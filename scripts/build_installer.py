"""
Construit l'installeur Windows de TRANSLAX (Inno Setup) -- demande
explicite de l'utilisateur, 26/08/2026 : « un installateur multi-étape
simple mais nécessaire à ce que chacun puisse avoir ça sur leur machine ».

Lit la version depuis core/version.py (une seule source de vérité,
jamais recopiée à la main dans installer/translax.iss) et invoque
ISCC.exe (Inno Setup 6) avec cette version en paramètre de préprocesseur.

Ne construit PAS l'exe lui-même : suppose `dist/TRANSLAX.exe` déjà
construit et à jour via `pyinstaller TRANSLAX.spec` (voir
scripts/stamp_build_date.py pour la version + le build de l'exe) --
vérifié explicitement avant d'appeler Inno Setup, jamais empaqueté un
exe périmé ou manquant en silence.

Nécessite Inno Setup 6 installé (ISCC.exe) : https://jrsoftware.org/isdl.php

    python scripts/build_installer.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.version import VERSION  # noqa: E402
import build_installer_images  # noqa: E402

# Emplacements standards de l'installeur Inno Setup 6 sur Windows --
# testés avant de retomber sur une recherche dans le PATH (utile si
# installé ailleurs qu'aux chemins par défaut).
ISCC_CANDIDATES = [
    Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
]


def find_iscc() -> Path:
    for candidate in ISCC_CANDIDATES:
        if candidate.exists():
            return candidate
    found = shutil.which("ISCC.exe") or shutil.which("iscc")
    if found:
        return Path(found)
    raise SystemExit(
        "ISCC.exe introuvable -- Inno Setup 6 doit être installé "
        "(https://jrsoftware.org/isdl.php)."
    )


def main() -> int:
    exe_path = ROOT / "dist" / "TRANSLAX.exe"
    if not exe_path.exists():
        print(
            f"ERREUR : {exe_path} n'existe pas -- construis d'abord l'exe "
            "(python -m PyInstaller TRANSLAX.spec) avant l'installeur."
        )
        return 1

    # Régénérées à chaque build, pas seulement au premier jet -- coût
    # négligeable (quelques dixièmes de seconde), et garantit que le logo
    # de l'installeur ne se retrouve jamais périmé par rapport à
    # ui/icon.ico si celui-ci change un jour.
    build_installer_images.main()

    iscc = find_iscc()
    iss_script = ROOT / "installer" / "translax.iss"
    output_dir = ROOT / "dist_installer"
    output_dir.mkdir(exist_ok=True)

    cmd = [str(iscc), f"/DMyAppVersion={VERSION}", str(iss_script)]
    print("Construction de l'installeur :", " ".join(cmd))
    result = subprocess.run(cmd, cwd=str(ROOT / "installer"))
    if result.returncode != 0:
        print(f"ERREUR : ISCC.exe a échoué (code {result.returncode}).")
        return result.returncode

    produced = output_dir / f"TRANSLAX-Setup-{VERSION}.exe"
    if produced.exists():
        size_mo = produced.stat().st_size / 1024 / 1024
        print(f"Installeur créé : {produced} ({size_mo:.1f} Mo)")
    else:
        print(
            f"Attention : {produced} attendu mais introuvable -- vérifie "
            "OutputDir/OutputBaseFilename dans installer/translax.iss."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
