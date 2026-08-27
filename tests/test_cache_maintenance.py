"""
Tests de `core/cache_maintenance.py` -- retrouver et vider les dossiers de
cache `.translax` (demande explicite de l'utilisateur, 26/08/2026 :
« permet moi de gérer les paths en lien avec le ocr json »). Tout est fait
sur de vrais dossiers/fichiers dans un répertoire temporaire, jamais un
simulacre.

    python tests/test_cache_maintenance.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import cache_maintenance, state as state_mod  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK   {label}")
    else:
        print(f"  ECHEC {label} {detail}")
        failures.append(label)


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="translax_cache_maint_"))
    try:
        print("\n1. find_cache_dirs() sur une arborescence réelle")
        root = workdir / "sortie"
        cache1 = root / "livre1" / state_mod.WORK_DIR_NAME
        cache2 = root / "sous_dossier" / "livre2" / state_mod.WORK_DIR_NAME
        cache1.mkdir(parents=True)
        cache2.mkdir(parents=True)
        (cache1 / "a.progress.json").write_text("{}", encoding="utf-8")
        (cache2 / "b.vision_cache.jsonl").write_text('{"x": 1}\n', encoding="utf-8")

        result = cache_maintenance.find_cache_dirs(root)
        check("les deux dossiers .translax (imbriqués ou non) sont trouvés", result.count == 2,
              f"({[str(d) for d in result.dirs]!r})")
        check("la taille totale reflète les vrais fichiers écrits", result.total_bytes > 0,
              f"({result.total_bytes})")

        print("\n2. Robustesse : dossier racine inexistant ou vide")
        empty_result = cache_maintenance.find_cache_dirs(workdir / "n_existe_pas")
        check("un dossier racine inexistant ne plante pas et renvoie un résultat vide",
              empty_result.count == 0 and empty_result.total_bytes == 0)

        no_cache_root = workdir / "rien_a_trouver"
        no_cache_root.mkdir()
        (no_cache_root / "juste_un_fichier.txt").write_text("rien ici", encoding="utf-8")
        no_cache_result = cache_maintenance.find_cache_dirs(no_cache_root)
        check("un dossier sans aucun .translax renvoie bien un résultat vide, pas une erreur",
              no_cache_result.count == 0)

        print("\n3. clear_cache_dirs() supprime réellement, et continue après un échec partiel")
        removed, errors = cache_maintenance.clear_cache_dirs(result.dirs)
        check("les deux dossiers sont bien supprimés", removed == 2, f"(removed={removed})")
        check("aucune erreur sur un cas normal", errors == [], f"({errors!r})")
        check("le dossier livre1/.translax n'existe plus sur le disque", not cache1.exists())
        check("le dossier sous_dossier/livre2/.translax n'existe plus sur le disque", not cache2.exists())

        # Un dossier déjà supprimé (ou jamais créé) dans la liste ne doit
        # jamais bloquer les autres suppressions -- scénario réaliste si
        # l'utilisateur relance "Vider" sur un résultat d'analyse périmé.
        removed2, errors2 = cache_maintenance.clear_cache_dirs([cache1, cache2])
        check("supprimer des dossiers déjà absents ne plante pas", removed2 == 0)
        check("chaque échec est bien remonté, pas juste avalé silencieusement", len(errors2) == 2,
              f"({errors2!r})")

        print("\n4. format_size() -- lisible, jamais de crash sur un cas limite")
        check("0 octet formaté sans crash", cache_maintenance.format_size(0) == "0 o",
              f"({cache_maintenance.format_size(0)!r})")
        check("quelques Ko affichés en Ko", "Ko" in cache_maintenance.format_size(5000))
        check("quelques Mo affichés en Mo", "Mo" in cache_maintenance.format_size(5_000_000))
        check("un très gros volume retombe en Go sans boucle infinie",
              "Go" in cache_maintenance.format_size(5_000_000_000))

        print()
        if failures:
            print(f"{len(failures)} test(s) en échec : " + ", ".join(failures))
            return 1
        print("Tous les tests de maintenance du cache passent.")
        return 0
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
