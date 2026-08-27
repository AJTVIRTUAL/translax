"""
Test de `core/settings.py` -- isolé du vrai dossier de configuration de la
machine (un test ne doit jamais écrire pour de vrai dans
%APPDATA%\\TRANSLAX\\ / ~/Library/Application Support/TRANSLAX/), en
patchant `_settings_dir` vers un dossier temporaire.

    python tests/test_settings.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import settings  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK   {label}")
    else:
        print(f"  ECHEC {label} {detail}")
        failures.append(label)


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="translax_settings_"))
    real_settings_dir = settings._settings_dir
    settings._settings_dir = lambda: workdir / "TRANSLAX"  # noqa: SLF001 - isolation délibérée

    try:
        print("\n1. Rien de réglé au premier lancement")
        check("aucun dossier par défaut", settings.get_default_output_dir() is None)

        print("\n2. Enregistrer puis relire")
        target = workdir / "sortie"
        target.mkdir()
        settings.set_default_output_dir(target)
        check("relu correctement juste après", settings.get_default_output_dir() == target)

        print("\n3. Persisté sur disque, pas seulement en mémoire")
        check("settings.json réellement créé", settings.settings_path().exists())
        check("toujours là en relisant depuis zéro (simule un nouveau lancement)",
              settings.load_settings().get("default_output_dir") == str(target))

        print("\n4. Dossier disparu depuis -> retombe sur None sans planter")
        shutil.rmtree(target)
        check("dossier supprimé -> None", settings.get_default_output_dir() is None)
        target.mkdir()
        check("réapparaît tel quel si le dossier est recréé (réglage non effacé)",
              settings.get_default_output_dir() == target)

        print("\n5. Effacer le réglage (retour au comportement par défaut)")
        settings.set_default_output_dir(None)
        check("effacé", settings.get_default_output_dir() is None)
        check("clé absente du fichier, pas juste vidée",
              "default_output_dir" not in settings.load_settings())

        print("\n6. Fichier corrompu -> ignoré, jamais de plantage")
        settings.settings_path().write_text("{ceci n'est pas du JSON", encoding="utf-8")
        check("lecture corrompue -> aucun réglage, pas d'exception", settings.get_default_output_dir() is None)
        settings.set_default_output_dir(target)
        check("réécrire par-dessus un fichier corrompu fonctionne", settings.get_default_output_dir() == target)

        print("\n7. Clés API (Anthropic, xAI, OpenAI -- ces deux dernières préparées pour plus tard)")
        for name, get, set_ in (
            ("anthropic", settings.get_anthropic_api_key, settings.set_anthropic_api_key),
            ("xai", settings.get_xai_api_key, settings.set_xai_api_key),
            ("openai", settings.get_openai_api_key, settings.set_openai_api_key),
        ):
            check(f"{name} : absente au départ", get() is None)
            set_("  ma-cle-de-test  ")
            check(f"{name} : enregistrée, espaces retirés", get() == "ma-cle-de-test")
            set_("")
            check(f"{name} : chaîne vide efface", get() is None)
            set_(None)
            check(f"{name} : None efface aussi", get() is None)

        print("\n8. Traductions en attente (liste, pas un seul « dernier job » -- 26/08/2026)")
        check("liste vide au départ", settings.get_pending_jobs() == [])
        settings.add_pending_job({"output_path": "H:/sortie/livre1.md", "input_path": "livre1.pdf"})
        check("un job ajouté, bien relisible", len(settings.get_pending_jobs()) == 1)
        settings.add_pending_job({"output_path": "H:/sortie/livre2.md", "input_path": "livre2.pdf"})
        check("un deuxième job coexiste avec le premier -- pas remplacé", len(settings.get_pending_jobs()) == 2)

        settings.add_pending_job({"output_path": "H:/sortie/livre1.md", "input_path": "livre1.pdf", "src_lang": "fra_Latn"})
        jobs = settings.get_pending_jobs()
        check("réenregistrer le même output_path met à jour l'entrée, n'en crée pas une troisième",
              len(jobs) == 2, f"({len(jobs)})")
        updated = next(j for j in jobs if j["output_path"] == "H:/sortie/livre1.md")
        check("la mise à jour est bien reflétée dans l'entrée existante", updated.get("src_lang") == "fra_Latn")

        settings.remove_pending_job("H:/sortie/livre1.md")
        remaining = settings.get_pending_jobs()
        check("un seul job restant après retrait", len(remaining) == 1)
        check("c'est bien l'autre qui reste", remaining[0]["output_path"] == "H:/sortie/livre2.md")

        settings.remove_pending_job("H:/sortie/inexistant.md")
        check("retirer un job déjà absent ne plante pas et ne touche pas au reste",
              len(settings.get_pending_jobs()) == 1)

        settings.remove_pending_job("H:/sortie/livre2.md")
        check("liste vide une fois tout retiré", settings.get_pending_jobs() == [])

    finally:
        settings._settings_dir = real_settings_dir  # noqa: SLF001
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    if failures:
        print(f"{len(failures)} test(s) en échec : " + ", ".join(failures))
        return 1
    print("Tous les tests de réglages passent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
