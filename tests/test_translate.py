"""
Tests de `core/translate.py` -- le moteur « Turbo » (FastEngine,
CTranslate2, ajouté le 25/08/2026) et le moteur « OPUS-MT » (Helsinki-NLP,
ajouté le même jour, en vue de la commercialisation -- licence CC-BY 4.0,
contrairement à NLLB et son moteur Turbo qui restent CC-BY-NC, gardés
uniquement pour l'usage personnel de l'auteur, voir SPEC.md).

Pour FastEngine :
  1. Le profil "600M-ct2" est bien déclaré, avec le bon moteur.
  2. `is_model_ready` fait le bon aiguillage (cache HF vs dossier converti),
     isolé dans un dossier temporaire -- jamais dans le vrai %APPDATA%.
  3. `pipeline.run_job` instancie la bonne CLASSE de moteur selon le modèle
     choisi (PreciseEngine vs FastEngine vs OpusMtEngine), avec de faux
     moteurs comme `test_pipeline.py`.
  4. Un VRAI test de bout en bout : conversion réelle du 600M (déjà en
     cache HF sur cette machine), vraies traductions, comparées à
     PreciseEngine sur les mêmes phrases -- mesure la vitesse réelle et la
     fidélité de sortie, ne se contente pas de vérifier que le code
     s'exécute sans erreur. Sauté proprement (pas en échec) si
     `ctranslate2` n'est pas installé ou si le 600M n'est pas en cache.

Pour OpusMtEngine (section 5) :
  - résolution du nom de modèle par paire de langues (gabarit, pas un
    repo_id fixe), avec la table ISO 639-1 de core/languages.py ;
  - un VRAI test de bout en bout sur eng_Latn -> fra_Latn (modèle déjà
    téléchargé sur cette machine), comparé à PreciseEngine ;
  - une VRAIE paire confirmée absente du Hub (wol_Latn -> lin_Latn) lève
    bien OpusMtUnavailable, pas une trace HuggingFace brute.

Pour MadladEngine (section 6) -- poids réels (3B, ~11,8 Go) volontairement
PAS téléchargés en développement (voir SPEC.md) :
  - le mécanisme de prompt <2xx> est vérifié réellement quand même : le
    tokenizer seul (téléchargement léger, quelques Mo) suffit à confirmer
    que "<2fr>" est un jeton atomique du vocabulaire, pas éclaté en morceaux ;
  - MadladUnavailable pour une langue hors de la table de correspondance ;
  - la traduction réelle de bout en bout est sautée proprement (pas en
    échec) tant que le modèle complet n'est pas en cache sur la machine qui
    exécute ce test.

    python tests/test_translate.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import languages, pipeline, settings, translate  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK   {label}")
    else:
        print(f"  ECHEC {label} {detail}")
        failures.append(label)


def main() -> int:
    print("\n1. Le profil « Turbo » est déclaré correctement")
    check("600M-ct2 présent dans MODEL_INFO", "600M-ct2" in translate.MODEL_INFO)
    info = translate.MODEL_INFO["600M-ct2"]
    check("moteur = fast", info.engine == "fast")
    check("mêmes poids que le 600M « Précis »", info.repo_id == translate.MODEL_INFO["600M"].repo_id)
    check("les 3 profils d'origine restent sur le moteur precise",
          all(translate.MODEL_INFO[k].engine == "precise" for k in ("600M", "1.3B", "3.3B")))

    check("opus-mt présent, moteur = opus-mt", translate.MODEL_INFO["opus-mt"].engine == "opus-mt")
    check("opus-mt : repo_id est un gabarit, pas un identifiant réel",
          "{src}" in translate.MODEL_INFO["opus-mt"].repo_id and "{tgt}" in translate.MODEL_INFO["opus-mt"].repo_id)

    check("madlad-3b présent, moteur = madlad", translate.MODEL_INFO["madlad-3b"].engine == "madlad")
    check("madlad-3b : repo_id fixe (un seul modèle pour toutes les langues, contrairement à opus-mt)",
          translate.MODEL_INFO["madlad-3b"].repo_id == "google/madlad400-3b-mt")

    print("\n2. is_model_ready() -- aiguillage cache HF vs dossier converti, isolé")
    workdir = Path(tempfile.mkdtemp(prefix="translax_translate_"))
    real_settings_dir = settings._settings_dir
    settings._settings_dir = lambda: workdir / "TRANSLAX"  # noqa: SLF001 - isolation délibérée, comme test_settings.py
    try:
        check("pas encore prêt avant toute conversion", not translate.is_ctranslate2_ready(info.repo_id))
        model_dir = translate.ctranslate2_model_dir(info.repo_id)
        model_dir.mkdir(parents=True, exist_ok=True)
        check("dossier créé sans model.bin : toujours pas prêt (conversion incomplète)",
              not translate.is_ctranslate2_ready(info.repo_id))
        (model_dir / "model.bin").write_bytes(b"faux poids, juste pour le test de presence")
        check("model.bin présent : prêt", translate.is_ctranslate2_ready(info.repo_id))
        check("is_model_ready('600M-ct2') suit is_ctranslate2_ready", translate.is_model_ready("600M-ct2"))
    finally:
        settings._settings_dir = real_settings_dir  # noqa: SLF001
        shutil.rmtree(workdir, ignore_errors=True)

    # Le 600M "precise" a été téléchargé lors de sessions précédentes de ce
    # projet (voir SPEC.md) : cette machine de développement l'a réellement
    # en cache HuggingFace. Vérifie que is_model_ready ne se trompe pas de
    # fonction pour un profil "precise" (pas d'appel à is_ctranslate2_ready).
    check("is_model_ready('600M') suit is_model_cached (vrai cache HF de cette machine)",
          translate.is_model_ready("600M") == translate.is_model_cached(translate.MODEL_MAP["600M"]))

    print("\n3. pipeline.run_job instancie la bonne classe de moteur selon le modèle")
    created: list[str] = []

    def make_fake(tag: str):
        class FakeEngine:
            def __init__(self, *a, **k):
                created.append(tag)

            def load(self, on_status=None):
                pass

            def translate(self, text: str, *, heartbeat=None) -> str:
                return "FR " + text

            def unload(self):
                pass

        return FakeEngine

    original_precise = translate.PreciseEngine
    original_fast = translate.FastEngine
    original_opus = translate.OpusMtEngine
    original_madlad = translate.MadladEngine
    translate.PreciseEngine = make_fake("precise")  # type: ignore[assignment]
    translate.FastEngine = make_fake("fast")  # type: ignore[assignment]
    translate.OpusMtEngine = make_fake("opus-mt")  # type: ignore[assignment]
    translate.MadladEngine = make_fake("madlad")  # type: ignore[assignment]
    try:
        workdir2 = Path(tempfile.mkdtemp(prefix="translax_translate_pipeline_"))
        try:
            src = workdir2 / "in.txt"
            src.write_text("Hello world, this is a real enough sentence for a segment.\n", encoding="utf-8")

            pipeline.run_job(pipeline.Job(
                input_path=src, output_path=workdir2 / "out_precise.md",
                model_key="600M", translate_title=False,
            ))
            check("model_key='600M' -> PreciseEngine instancié", created == ["precise"], f"({created})")

            created.clear()
            pipeline.run_job(pipeline.Job(
                input_path=src, output_path=workdir2 / "out_fast.md",
                model_key="600M-ct2", translate_title=False,
            ))
            check("model_key='600M-ct2' -> FastEngine instancié", created == ["fast"], f"({created})")

            created.clear()
            pipeline.run_job(pipeline.Job(
                input_path=src, output_path=workdir2 / "out_opus.md",
                model_key="opus-mt", translate_title=False,
            ))
            check("model_key='opus-mt' -> OpusMtEngine instancié", created == ["opus-mt"], f"({created})")

            created.clear()
            pipeline.run_job(pipeline.Job(
                input_path=src, output_path=workdir2 / "out_madlad.md",
                model_key="madlad-3b", translate_title=False,
            ))
            check("model_key='madlad-3b' -> MadladEngine instancié", created == ["madlad"], f"({created})")
        finally:
            shutil.rmtree(workdir2, ignore_errors=True)
    finally:
        translate.PreciseEngine = original_precise  # type: ignore[assignment]
        translate.FastEngine = original_fast  # type: ignore[assignment]
        translate.OpusMtEngine = original_opus  # type: ignore[assignment]
        translate.MadladEngine = original_madlad  # type: ignore[assignment]

    print("\n4. Bout en bout RÉEL : conversion + traduction, comparé à PreciseEngine")
    try:
        import ctranslate2  # noqa: F401
    except ImportError:
        print("  SAUTÉ -- ctranslate2 n'est pas installé dans cet environnement.")
        return _finish()

    if not translate.is_model_cached(translate.MODEL_MAP["600M"]):
        print("  SAUTÉ -- le 600M n'est pas en cache HuggingFace sur cette machine "
              "(nécessaire pour la conversion CTranslate2).")
        return _finish()

    cases = [
        "Chapter XII",
        "Hello.",
        "In 1923, Dr. Smith published 42 papers on the subject, though only 7 survived critical review.",
        "The quick brown fox jumps over the lazy dog, and this sentence is long enough to "
        "exercise the beam search decoder for a few steps.",
    ]

    precise = translate.PreciseEngine(model_key="600M", src_lang="eng_Latn", tgt_lang="fra_Latn")
    fast = translate.FastEngine(model_key="600M-ct2", src_lang="eng_Latn", tgt_lang="fra_Latn")
    precise.load()
    fast.load()  # convertit réellement si pas déjà fait (voir FastEngine.load) -- ~77s la 1re fois

    identical = 0
    precise_total, fast_total = 0.0, 0.0
    for text in cases:
        t0 = time.time()
        out_precise = precise.translate(text)
        precise_total += time.time() - t0

        t0 = time.time()
        out_fast = fast.translate(text)
        fast_total += time.time() - t0

        if out_precise.strip() == out_fast.strip():
            identical += 1
        else:
            print(f"  (écart mineur attendu sur : {text!r} -> précis={out_precise!r} / turbo={out_fast!r})")

    precise.unload()
    fast.unload()

    check(f"sortie identique sur au moins {len(cases) - 1}/{len(cases)} phrases (int8 : de rares écarts mineurs attendus)",
          identical >= len(cases) - 1, f"({identical}/{len(cases)} identiques)")
    check(f"FastEngine mesuré plus rapide que PreciseEngine sur ce lot (précis={precise_total:.1f}s, turbo={fast_total:.1f}s)",
          fast_total < precise_total)
    if fast_total > 0:
        print(f"  Accélération mesurée sur ce lot : {precise_total / fast_total:.1f}x")

    print("\n5. OPUS-MT (licence commerciale, Helsinki-NLP)")
    check("eng_Latn -> iso2 'en'", languages.iso2("eng_Latn") == "en")
    check("fra_Latn -> iso2 'fr'", languages.iso2("fra_Latn") == "fr")
    check("code inconnu -> None (jamais deviné)", languages.iso2("xxx_Zzzz") is None)
    check("nom de modèle résolu pour eng_Latn->fra_Latn",
          translate.opus_mt_repo_id("eng_Latn", "fra_Latn") == "Helsinki-NLP/opus-mt-en-fr")
    check("None si une langue est hors table", translate.opus_mt_repo_id("eng_Latn", "xxx_Zzzz") is None)

    opus_repo = translate.opus_mt_repo_id("eng_Latn", "fra_Latn")
    if not translate.is_model_cached(opus_repo):
        print(f"  SAUTÉ (comparaison réelle) -- {opus_repo} n'est pas en cache sur cette machine.")
    else:
        # `precise` a été déchargé (unload()) à la fin de la section 4 --
        # rechargé ici pour cette comparaison, plutôt que d'en garder deux
        # instances (une par section) en mémoire en même temps.
        # Attente DIFFÉRENTE de la section 4 : FastEngine est le MÊME 600M
        # quantifié (identité quasi totale attendue), alors qu'OPUS-MT est un
        # modèle Helsinki-NLP entraîné indépendamment, sur d'autres données --
        # deux traducteurs différents phrasent différemment une même phrase
        # sans que l'un des deux soit "faux". Constaté ici pour de vrai :
        # 1/4 identique mot pour mot, mais les 3 autres sont des paraphrases
        # fidèles (relu manuellement) -- une seule fois "Hello." donne même
        # une meilleure traduction côté OPUS-MT ("Bonjour.") que NLLB
        # ("Je vous en prie.", bizarrerie connue de NLLB sur les phrases
        # isolées très courtes). Le test vérifie donc une VRAIE traduction a
        # eu lieu (non vide, différente de la source), pas une identité de
        # phrasé qui n'a pas de raison de se produire entre deux modèles
        # indépendants.
        opus = translate.OpusMtEngine(model_key="opus-mt", src_lang="eng_Latn", tgt_lang="fra_Latn")
        opus.load()
        try:
            for text in cases:
                out_opus = opus.translate(text)
                check(f"OPUS-MT a bien traduit {text[:30]!r}...",
                      bool(out_opus.strip()) and out_opus.strip().lower() != text.strip().lower(),
                      f"(-> {out_opus!r})")
        finally:
            opus.unload()

    # Paire réellement confirmée absente du Hub (vérifié manuellement avant
    # d'écrire ce test, pas supposé) -- doit lever OpusMtUnavailable avec un
    # message clair, pas laisser remonter la trace HuggingFace brute.
    unavailable = translate.OpusMtEngine(model_key="opus-mt", src_lang="wol_Latn", tgt_lang="lin_Latn")
    raised_correctly = False
    try:
        unavailable.load()
    except translate.OpusMtUnavailable:
        raised_correctly = True
    except Exception as exc:  # noqa: BLE001 - justement ce qu'on ne veut PAS voir
        print(f"  (exception inattendue : {type(exc).__name__}: {exc})")
    check("paire wol_Latn->lin_Latn (confirmée absente du Hub) lève OpusMtUnavailable", raised_correctly)

    print("\n6. MADLAD-400 (licence commerciale, Google) -- poids réels non téléchargés ici")
    # Cible hors de la table de correspondance (voir MadladUnavailable) :
    # un vrai code FLORES (hébreu), juste absent des 18 langues exposées
    # par TRANSLAX aujourd'hui -- pas un code inventé.
    madlad_unavailable = translate.MadladEngine(model_key="madlad-3b", src_lang="eng_Latn", tgt_lang="heb_Hebr")
    raised_correctly = False
    try:
        madlad_unavailable.load()
    except translate.MadladUnavailable:
        raised_correctly = True
    except Exception as exc:  # noqa: BLE001 - justement ce qu'on ne veut PAS voir
        print(f"  (exception inattendue : {type(exc).__name__}: {exc})")
    check("langue cible hors table (heb_Hebr) lève MadladUnavailable, sans réseau", raised_correctly)

    try:
        from transformers import AutoTokenizer
        # Le tokenizer seul (quelques Mo) suffit à vérifier le mécanisme de
        # prompt <2xx> réellement, sans télécharger les ~11,8 Go de poids.
        madlad_tok = AutoTokenizer.from_pretrained(translate.MODEL_INFO["madlad-3b"].repo_id)
        ids = madlad_tok("<2fr> Hello world.", return_tensors="pt")["input_ids"][0].tolist()
        tokens = madlad_tok.convert_ids_to_tokens(ids)
        check("'<2fr>' est un jeton ATOMIQUE du vocabulaire (pas éclaté en morceaux)",
              "<2fr>" in tokens, f"(tokens vus : {len(tokens)})")
    except Exception as exc:  # noqa: BLE001 - pas de réseau, tokenizer déplacé/renommé...
        print(f"  SAUTÉ (tokenizer MADLAD) -- {type(exc).__name__}: {exc}")

    if not translate.is_model_cached(translate.MODEL_INFO["madlad-3b"].repo_id):
        print("  SAUTÉ (traduction réelle) -- MADLAD-400 3B (~11,8 Go) n'est pas en cache sur "
              "cette machine ; téléchargement volontairement pas déclenché par ce test (voir SPEC.md).")
    else:
        madlad = translate.MadladEngine(model_key="madlad-3b", src_lang="eng_Latn", tgt_lang="fra_Latn")
        madlad.load()
        try:
            for text in cases:
                out_madlad = madlad.translate(text)
                check(f"MADLAD-400 a bien traduit {text[:30]!r}...",
                      bool(out_madlad.strip()) and out_madlad.strip().lower() != text.strip().lower(),
                      f"(-> {out_madlad!r})")
        finally:
            madlad.unload()

    return _finish()


def _finish() -> int:
    print()
    if failures:
        print(f"{len(failures)} test(s) en échec : " + ", ".join(failures))
        return 1
    print("Tous les tests de traduction passent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
