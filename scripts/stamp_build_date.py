"""
Régénère la date de build dans `core/version.py` avec la date réelle du
jour — à lancer avant chaque empaquetage (voir SPEC.md §8 et
MACOS_BUILD.md), pour que le numéro affiché en bas de la fenêtre reflète
toujours quand le `.exe`/`.app` a réellement été construit, pas seulement
quand quelqu'un l'utilise.

    python scripts/stamp_build_date.py                 # date du jour seulement
    python scripts/stamp_build_date.py --bump patch     # + incrémente 1.0.0 -> 1.0.1
    python scripts/stamp_build_date.py --bump minor     # 1.0.1 -> 1.1.0
    python scripts/stamp_build_date.py --bump major     # 1.1.0 -> 2.0.0
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

VERSION_FILE = Path(__file__).resolve().parent.parent / "core" / "version.py"


def _bump(version: str, part: str) -> str:
    major, minor, patch = (int(p) for p in version.split("."))
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bump", choices=["major", "minor", "patch"], default=None,
                         help="incrémente aussi le numéro de version (sinon seule la date change)")
    args = parser.parse_args()

    content = VERSION_FILE.read_text(encoding="utf-8")

    if args.bump:
        match = re.search(r'VERSION = "([\d.]+)"', content)
        if not match:
            print("Impossible de trouver VERSION dans core/version.py", file=sys.stderr)
            return 1
        current = match.group(1)
        new_version = _bump(current, args.bump)
        content = re.sub(r'VERSION = "[\d.]+"', f'VERSION = "{new_version}"', content)
        print(f"Version : {current} -> {new_version}")

    today = date.today().isoformat()
    content = re.sub(r'BUILD_DATE = "[\d-]+"', f'BUILD_DATE = "{today}"', content)
    VERSION_FILE.write_text(content, encoding="utf-8")
    print(f"Date de build : {today}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
