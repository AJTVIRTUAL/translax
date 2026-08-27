"""
Tests de `core/updater.py` -- vérification et téléchargement des mises à
jour de TRANSLAX (demande explicite de l'utilisateur, 27/08/2026 :
« comme sur VS Code »), en s'appuyant sur les Releases du VRAI dépôt
GitHub public `AJTVIRTUAL/translax` -- pas un simulacre : ce dépôt et sa
première publication (v1.17.0) ont été créés spécifiquement pour cette
fonctionnalité, ils font partie de l'infrastructure réelle du projet, pas
d'un décor de test.

    python tests/test_updater.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import updater  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK   {label}")
    else:
        print(f"  ECHEC {label} {detail}")
        failures.append(label)


def main() -> int:
    print("\n1. Comparaison de versions (segment par segment, pas lexicographique)")
    check("version supérieure détectée", updater.is_newer("1.18.0", "1.17.0"))
    check("version identique -> pas plus récente", not updater.is_newer("1.17.0", "1.17.0"))
    check("version inférieure -> pas plus récente", not updater.is_newer("1.0.0", "1.17.0"))
    check("« v » du tag ignoré des deux côtés", updater.is_newer("v2.0.0", "1.17.0"))
    check("9 < 17 numériquement, pas lexicographiquement ('9' > '1' en chaîne)",
          not updater.is_newer("1.9.0", "1.17.0"))
    check("un patch plus récent suffit (1.17.1 > 1.17.0)", updater.is_newer("1.17.1", "1.17.0"))
    check("une étiquette non numérique ne plante jamais la comparaison",
          not updater.is_newer("nightly", "1.17.0"))

    print("\n2. Vraie vérification sur le vrai dépôt GitHub public")
    info = updater.check_latest_release()
    check("une version est bien renvoyée", bool(info.version), f"({info.version!r})")
    check("l'URL de téléchargement pointe vers github.com",
          "github.com" in info.download_url and info.download_url.endswith(".exe"),
          f"({info.download_url!r})")
    check("une taille de fichier réaliste est renvoyée (> 100 Mo, exe réellement volumineux)",
          info.asset_size > 100_000_000, f"({info.asset_size})")

    print("\n3. Dépôt inexistant -> erreur lisible, jamais une exception brute")
    real_api_url = updater.API_URL
    updater.API_URL = "https://api.github.com/repos/AJTVIRTUAL/ce-depot-n-existe-pas/releases/latest"
    try:
        try:
            updater.check_latest_release()
            check("une UpdateCheckError est bien levée sur un dépôt inexistant", False)
        except updater.UpdateCheckError as exc:
            check("une UpdateCheckError est bien levée sur un dépôt inexistant", True)
            check("le message reste lisible (pas une trace Python brute)",
                  "github" in str(exc).lower(), f"({exc!r})")
    finally:
        updater.API_URL = real_api_url

    print("\n4. Téléchargement réel par blocs, avec progression")
    import tempfile
    dest = Path(tempfile.gettempdir()) / "translax_test_updater_download.bin"
    dest.unlink(missing_ok=True)
    progress_calls: list[tuple[int, int]] = []
    try:
        # Un petit fichier réel du dépôt lui-même sert de "faux installeur"
        # -- valide le vrai mécanisme de téléchargement sans attendre le
        # téléchargement du vrai installeur (plusieurs centaines de Mo).
        small_url = "https://raw.githubusercontent.com/AJTVIRTUAL/translax/main/ui/icon.ico"
        updater.download_installer(small_url, dest, on_progress=lambda d, t: progress_calls.append((d, t)))
        check("le fichier téléchargé existe réellement sur le disque", dest.exists())
        check("le fichier téléchargé n'est pas vide", dest.exists() and dest.stat().st_size > 0)
        check("la progression a bien été rapportée au moins une fois", len(progress_calls) >= 1)
        check("aucun fichier .part résiduel une fois terminé",
              not dest.with_suffix(dest.suffix + ".part").exists())
    finally:
        dest.unlink(missing_ok=True)

    print("\n5. Annulation d'un téléchargement en cours (should_stop)")
    dest2 = Path(tempfile.gettempdir()) / "translax_test_updater_cancel.bin"
    dest2.unlink(missing_ok=True)
    try:
        try:
            updater.download_installer(small_url, dest2, should_stop=lambda: True)
            check("annuler dès le premier bloc lève bien une UpdateCheckError", False)
        except updater.UpdateCheckError:
            check("annuler dès le premier bloc lève bien une UpdateCheckError", True)
        check("aucun fichier final n'est laissé après une annulation", not dest2.exists())
        check("aucun fichier .part résiduel après une annulation",
              not dest2.with_suffix(dest2.suffix + ".part").exists())
    finally:
        dest2.unlink(missing_ok=True)

    print()
    if failures:
        print(f"{len(failures)} test(s) en échec : " + ", ".join(failures))
        return 1
    print("Tous les tests de mise à jour passent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
