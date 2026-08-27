"""
Tests du moteur SANS charger NLLB : le modèle est remplacé par un faux
traducteur. On vérifie ici la mécanique qui entoure la traduction — celle
qui a déjà causé des dégâts sur le pipeline d'origine :

  - l'écriture est bien incrémentale (le fichier grossit pendant le travail,
    il n'attend pas la fin) ;
  - l'arrêt demandé par l'utilisateur laisse un fichier exploitable ;
  - la reprise repart exactement au bon segment ;
  - la reprise refuse de repartir si le fichier source a changé ;
  - la passe de nettoyage s'applique.

    python tests/test_pipeline.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import pipeline, segment, state, translate  # noqa: E402

SEGMENT_COUNT = 25
STOP_AFTER = 10

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK   {label}")
    else:
        print(f"  ECHEC {label} {detail}")
        failures.append(label)


class FakeEngine:
    """Traducteur factice : instantané, déterministe, aucun modèle chargé."""

    def __init__(self, *args, **kwargs):
        self.calls: list[str] = []

    def load(self, on_status=None):
        if on_status:
            on_status("(faux moteur chargé)")

    def translate(self, text: str, *, heartbeat=None) -> str:
        self.calls.append(text)
        if heartbeat is not None:
            heartbeat.beat()
        return "FR " + text

    def unload(self):
        pass


def make_source(folder: Path, count: int = SEGMENT_COUNT) -> Path:
    """Source à paragraphes séparés par des lignes vides -> stratégie blocs."""
    paragraphs = [
        f"Paragraph number {i} of the test document. "
        "It is deliberately long enough to be classified as a paragraph and "
        "not as a heading by the segmentation heuristics."
        for i in range(count)
    ]
    path = folder / "document.txt"
    path.write_text("\n\n".join(paragraphs) + "\n", encoding="utf-8")
    return path


def count_blocks(md_path: Path) -> int:
    content = md_path.read_text(encoding="utf-8")
    return len([b for b in content.split("\n\n") if b.strip()])


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="translax_test_"))
    engines: list[FakeEngine] = []

    def engine_factory(*args, **kwargs):
        engine = FakeEngine()
        engines.append(engine)
        return engine

    original_engine = translate.PreciseEngine
    pipeline.translate.PreciseEngine = engine_factory  # type: ignore[assignment]

    try:
        source = make_source(workdir)
        out_dir = workdir / "out"

        print("\n1. Segmentation")
        segments = segment.segment_text(source.read_text(encoding="utf-8"))
        check("stratégie blocs détectée", segment.detect_strategy(source.read_text(encoding="utf-8")) == "blocks")
        check(f"{SEGMENT_COUNT} segments", len(segments) == SEGMENT_COUNT, f"(obtenu {len(segments)})")

        print("\n2. Nommage automatique de la sortie")
        expected = out_dir / "document.md"
        check("document.txt -> document.md", pipeline.default_output_path(source, out_dir) == expected)

        print("\n3. Arrêt en cours de route")
        sizes: list[int] = []

        def stop_after_ten():
            return len(engines[-1].calls) >= STOP_AFTER

        job = pipeline.Job(input_path=source, output_path=expected, translate_title=False)
        result = pipeline.run_job(
            job,
            should_stop=stop_after_ten,
            on_progress=lambda p: sizes.append(expected.stat().st_size),
        )
        check("job marqué interrompu", result.cancelled)
        check(f"{STOP_AFTER} segments écrits", count_blocks(expected) == STOP_AFTER, f"(obtenu {count_blocks(expected)})")
        check("écriture incrémentale (le fichier grossit pendant le travail)",
              len(sizes) == STOP_AFTER and sizes == sorted(sizes) and sizes[0] < sizes[-1])
        saved = state.load_state(expected)
        check("état sauvegardé à 10", saved is not None and saved.done == STOP_AFTER and not saved.finished)

        print("\n4. Reprise")
        check("reprise possible détectée", state.can_resume(expected, source) is not None)
        result2 = pipeline.run_job(pipeline.Job(input_path=source, output_path=expected, translate_title=False))
        check("reprise à partir du segment 10", result2.resumed_from == STOP_AFTER, f"(obtenu {result2.resumed_from})")
        check("seulement 15 segments retraduits", len(engines[-1].calls) == SEGMENT_COUNT - STOP_AFTER,
              f"(obtenu {len(engines[-1].calls)})")
        check(f"{SEGMENT_COUNT} blocs au total", count_blocks(expected) == SEGMENT_COUNT,
              f"(obtenu {count_blocks(expected)})")
        check("aucun segment perdu ni dupliqué",
              [line for line in expected.read_text(encoding="utf-8").splitlines() if line.strip()]
              == [f"FR {s['text']}" for s in segments])
        final = state.load_state(expected)
        check("état marqué terminé", final is not None and final.finished and final.done == SEGMENT_COUNT)

        print("\n5. Reprise refusée si la source a changé")
        source.write_text(source.read_text(encoding="utf-8") + "\n\nAn extra paragraph appended after the fact, long enough to count.\n",
                          encoding="utf-8")
        check("empreinte différente -> pas de reprise", state.can_resume(expected, source) is None)

        print("\n5 bis. Abandon (state.abandon) -- efface la reprise, jamais le fichier de sortie")
        abandon_source = workdir / "abandon_source.txt"
        abandon_source.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        abandon_out = out_dir / "abandon_source.md"
        abandon_job = pipeline.Job(input_path=abandon_source, output_path=abandon_out, translate_title=False)
        pipeline.run_job(
            abandon_job,
            should_stop=lambda: len(engines[-1].calls) >= STOP_AFTER,
        )
        check("état de reprise bien présent avant abandon",
              state.can_resume(abandon_out, abandon_source) is not None)
        check("fichier partiel bien écrit avant abandon",
              count_blocks(abandon_out) == STOP_AFTER, f"({count_blocks(abandon_out)})")

        state.abandon(abandon_out)
        check("plus aucune reprise possible après abandon",
              state.can_resume(abandon_out, abandon_source) is None)
        check("le fichier de sortie partiel, lui, existe toujours (jamais touché par abandon)",
              abandon_out.exists() and count_blocks(abandon_out) == STOP_AFTER)
        # PAS un simple "le dossier .translax a disparu" : il est PARTAGÉ
        # par tous les jobs de ce même dossier de sortie (voir 5 ter juste
        # après) -- des fichiers d'AUTRES jobs (sections précédentes de ce
        # test) y vivent encore, donc il reste normalement en place. Ce qui
        # compte : les fichiers propres à CE job précis, eux, ont disparu.
        check("les fichiers de reprise propres à ce job ont bien disparu (progression, segments)",
              not state.state_path(abandon_out).exists() and not state.segments_path(abandon_out).exists())

        # Idempotent : abandonner un job déjà abandonné (ou jamais commencé)
        # ne doit jamais planter -- scénario réaliste si l'utilisateur
        # clique deux fois "Abandonner" par accident.
        state.abandon(abandon_out)
        check("abandonner deux fois de suite ne plante pas", True)

        print("\n5 ter. Abandon n'efface QUE ce job -- pas les autres du même dossier de sortie")
        # Bug réel rencontré en écrivant ces tests : `.translax/` est
        # PARTAGÉ par tous les jobs d'un même dossier de sortie (voir
        # core/state.py::work_dir) -- un abandon doit effacer uniquement
        # les fichiers de CE job (préfixés par son nom), jamais tout le
        # dossier, sous peine d'abandonner par erreur des jobs voisins
        # (exactement le scénario que la liste de reprise multi-jobs rend
        # possible, voir ui/main_window.py::ResumeJobsDialog).
        neighbour_source = workdir / "neighbour_source.txt"
        neighbour_source.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        neighbour_out = out_dir / "neighbour_source.md"
        keep_source = workdir / "keep_source.txt"
        keep_source.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        keep_out = out_dir / "keep_source.md"
        for src, out in ((neighbour_source, neighbour_out), (keep_source, keep_out)):
            pipeline.run_job(
                pipeline.Job(input_path=src, output_path=out, translate_title=False),
                should_stop=lambda: len(engines[-1].calls) >= STOP_AFTER,
            )
        check("les deux jobs voisins sont bien reprenables avant l'abandon de l'un d'eux",
              state.can_resume(neighbour_out, neighbour_source) is not None
              and state.can_resume(keep_out, keep_source) is not None)

        state.abandon(neighbour_out, neighbour_source)
        check("le job abandonné n'est plus reprenable", state.can_resume(neighbour_out, neighbour_source) is None)
        check("le job VOISIN, dans le même dossier de sortie, reste reprenable intact",
              state.can_resume(keep_out, keep_source) is not None)

        print("\n6. Nettoyage")
        from core import postprocess
        segs = [{"type": "heading", "text": "This is a full sentence that should not be a heading."},
                {"type": "heading", "text": "Chapter 1. The real heading"}]
        content = "## Ceci est une phrase complète qui ne devrait pas être un titre.\n\n## Chapitre 1. Le vrai titre\n"
        cleaned, report = postprocess.cleanup_markdown(segs, content)
        check("faux titre rétrogradé", report.demoted == 1, f"(obtenu {report.demoted})")
        check("vrai titre conservé", "## Chapitre 1. Le vrai titre" in cleaned)
        fixed, report2 = postprocess.cleanup_markdown([], "nous- mêmes\n")
        check("trait d'union recollé", "nous-mêmes" in fixed and report2.hyphen_fixes == 1)
        check("désalignement signalé sans planter", not report2.aligned)

        print("\n6 bis. Annuler le nettoyage (demande explicite de l'utilisateur)")
        undo_path = out_dir / "undo_test.md"
        original_content = "## Ceci est une phrase complète qui ne devrait pas être un titre.\n\nnous- mêmes\n"
        undo_path.write_text(original_content, encoding="utf-8")
        check("pas de sauvegarde avant tout nettoyage", not postprocess.has_backup(undo_path))
        check("annuler sans sauvegarde ne fait rien et le dit", postprocess.undo_cleanup(undo_path) is False)

        undo_segs = [{"type": "heading", "text": "This is a full sentence that should not be a heading."}]
        postprocess.cleanup_file(undo_segs, undo_path, apply=True)
        check("sauvegarde créée après un vrai nettoyage", postprocess.has_backup(undo_path))
        check("le fichier a bien été modifié", undo_path.read_text(encoding="utf-8") != original_content)

        check("annulation réussie", postprocess.undo_cleanup(undo_path) is True)
        check("le contenu d'origine est restauré exactement",
              undo_path.read_text(encoding="utf-8") == original_content)
        check("la sauvegarde est nettoyée après restauration (pas de ré-annulation fantôme)",
              not postprocess.has_backup(undo_path))
        check("annuler une seconde fois ne fait plus rien", postprocess.undo_cleanup(undo_path) is False)

        print("\n6 ter. Une deuxième passe de nettoyage n'écrase pas la sauvegarde d'origine")
        second_path = out_dir / "undo_second.md"
        first_original = "## Ceci est une phrase complète qui ne devrait pas être un titre.\n"
        second_path.write_text(first_original, encoding="utf-8")
        postprocess.cleanup_file(undo_segs, second_path, apply=True)
        after_first = second_path.read_text(encoding="utf-8")
        postprocess.cleanup_file(undo_segs, second_path, apply=True)  # deuxième passe, ex. reprise d'un job
        check("sauvegarde toujours celle d'AVANT la toute première passe",
              postprocess.backup_path(second_path).read_text(encoding="utf-8") == first_original)
        check("annuler retrouve bien l'état d'origine, pas l'état intermédiaire",
              postprocess.undo_cleanup(second_path) and
              second_path.read_text(encoding="utf-8") == first_original)

        print("\n7. Nettoyage des pages, intégré au pipeline")
        # Un .txt qui contient de vrais sauts de page (\f) : c'est ainsi que
        # extract.py restitue un PDF, donc suffisant pour tester
        # l'intégration sans dépendre d'un vrai fichier PDF.
        paginated = workdir / "book.txt"
        pages = [
            f"Paragraph number {i} of the paginated test document, long enough to count on its own.\n\n\n{i} | P a g e"
            for i in range(1, 6)
        ]
        paginated.write_text("\f".join(pages), encoding="utf-8")

        out_clean = out_dir / "book_clean.md"
        result_clean = pipeline.run_job(
            # strategy="blocks" forcée : avec seulement 5 paragraphes, le
            # nombre de lignes vides reste sous le seuil de détection
            # automatique (pensé pour de vrais documents) -- pas ce qui est
            # testé ici, qui est le nettoyage des pages en amont.
            pipeline.Job(input_path=paginated, output_path=out_clean, translate_title=False, strategy="blocks"),
            on_page_cleanup=lambda report: "clean",
        )
        check("rapport de nettoyage de pages détecté",
              result_clean.page_cleanup_report is not None and bool(result_clean.page_cleanup_report.groups))
        check("le pied de page répété a disparu de la sortie",
              "P a g e" not in out_clean.read_text(encoding="utf-8"))
        check("les 5 vrais paragraphes sont bien tous là", count_blocks(out_clean) == 5,
              f"(obtenu {count_blocks(out_clean)})")

        out_original = out_dir / "book_original.md"
        result_original = pipeline.run_job(
            pipeline.Job(input_path=paginated, output_path=out_original, translate_title=False),
            on_page_cleanup=lambda report: "original",
        )
        check("« original » : le pied de page traverse jusqu'à la sortie",
              "P a g e" in out_original.read_text(encoding="utf-8"))

        out_cancel = out_dir / "book_cancel.md"
        result_cancel = pipeline.run_job(
            pipeline.Job(input_path=paginated, output_path=out_cancel, translate_title=False),
            on_page_cleanup=lambda report: "cancel",
        )
        check("« cancel » : job marqué annulé", result_cancel.cancelled)
        check("« cancel » : aucun segment traduit", result_cancel.translated_segments == 0)
        check("« cancel » : rien n'a été écrit", not out_cancel.exists())

        print("\n8. Titre traduit dès le début (avant même le premier segment)")
        title_source = make_source(workdir, count=3)
        out_titled = out_dir / "document_titre.md"
        # Renommer document.txt -> document_titre.txt pour un scénario isolé
        title_source = title_source.rename(workdir / "document_titre.txt")
        translated_name = out_dir / "FR document titre.md"

        early_check: dict[str, bool] = {}

        def capture_first_segment(p) -> None:
            if p.done == 1 and "seen" not in early_check:
                early_check["seen"] = True
                early_check["translated_name_exists"] = translated_name.exists()
                early_check["original_name_absent"] = not out_titled.exists()

        result_titled = pipeline.run_job(
            pipeline.Job(input_path=title_source, output_path=out_titled, translate_title=True, strategy="blocks"),
            on_progress=capture_first_segment,
        )
        check("le fichier porte déjà son titre traduit dès le premier segment écrit",
              early_check.get("translated_name_exists") is True)
        check("le fichier ne passe jamais par l'ancien nom (créé directement, pas renommé après coup)",
              early_check.get("original_name_absent") is True)
        check("le fichier final porte le titre traduit (faux moteur : préfixe « FR »)",
              result_titled.output_path.name == "FR document titre.md",
              f"(obtenu {result_titled.output_path.name})")
        check("renamed_from vide : rien n'a été renommé, le nom était le bon dès le départ",
              result_titled.renamed_from is None)
        check("l'ancien nom n'existe jamais", not out_titled.exists())
        check("le fichier existe bien et contient le résultat",
              result_titled.output_path.exists() and count_blocks(result_titled.output_path) == 3)

        print("\n9. Reprise après redémarrage d'un job déjà renommé (fichier pointeur)")
        resume_source = make_source(workdir, count=8)
        resume_source = resume_source.rename(workdir / "resume_titre.txt")
        # Nom que l'interface recalculerait au redémarrage -- toujours dérivé
        # du seul fichier source, jamais du titre traduit (voir pipeline.py).
        out_resume = out_dir / "resume_titre.md"
        translated_resume_name = out_dir / "FR resume titre.md"
        STOP_AT = 3

        def stop_after_three():
            return len(engines[-1].calls) >= STOP_AT + 1  # +1 : l'appel de traduction du titre

        result_stopped = pipeline.run_job(
            pipeline.Job(input_path=resume_source, output_path=out_resume, translate_title=True, strategy="blocks"),
            should_stop=stop_after_three,
        )
        check("job interrompu après renommage précoce", result_stopped.cancelled)
        check("le fichier interrompu est déjà sous le nom traduit",
              translated_resume_name.exists() and count_blocks(translated_resume_name) == STOP_AT)
        check("l'ancien nom n'a jamais existé, même interrompu", not out_resume.exists())

        # Deuxième appel = un vrai redémarrage de l'appli : mêmes chemins
        # d'entrée/sortie que la première fois (l'interface ne sait jamais
        # d'avance quel sera le titre traduit) -- sans le pointeur, ce
        # deuxième run chercherait un état sous `out_resume` et ne
        # trouverait rien, repartant de zéro sans le dire.
        result_resumed = pipeline.run_job(
            pipeline.Job(input_path=resume_source, output_path=out_resume, translate_title=True, strategy="blocks")
        )
        check("reprise retrouvée via le pointeur (pas repartie de zéro)",
              result_resumed.resumed_from == STOP_AT, f"(obtenu {result_resumed.resumed_from})")
        check("seuls les segments restants ont été retraduits, sans retraduire le titre déjà connu",
              len(engines[-1].calls) == 8 - STOP_AT, f"(obtenu {len(engines[-1].calls)})")
        check("le fichier final, sous le nom traduit, est complet",
              translated_resume_name.exists() and count_blocks(translated_resume_name) == 8)

        print("\n10. Extraction seulement (Extraire) : même nettoyage, sans traduction")
        pages_source = [
            f"Real paragraph number {i} of the extract-only test, long enough to count on its own.\n\n\n{i} | P a g e"
            for i in range(1, 5)
        ]
        # Une "phrase complète" isolée sur sa propre ligne : la segmentation
        # la détecte comme titre ("## ..."), le nettoyage doit la rétrograder
        # -- vérifie que postprocess.cleanup_file s'applique bien SANS traduction.
        pages_source[0] = "This is a full sentence that should not be a heading.\n\n" + pages_source[0]
        extract_source = workdir / "extract_only.txt"
        extract_source.write_text("\f".join(pages_source), encoding="utf-8")
        extract_out = out_dir / "extract_only.md"

        engines_before = len(engines)
        result_extract = pipeline.run_job(
            pipeline.Job(
                input_path=extract_source, output_path=extract_out,
                extract_only=True, translate_title=True, strategy="blocks",
            ),
            on_page_cleanup=lambda report: "clean",
        )
        check("aucun moteur NLLB créé (extraction seule, pas de traduction)",
              len(engines) == engines_before, f"({len(engines) - engines_before} créé(s))")
        check("job non annulé", not result_extract.cancelled)
        check("nom de fichier inchangé (pas de titre traduit en mode extraction)",
              result_extract.output_path == extract_out, f"(obtenu {result_extract.output_path.name})")
        extract_content = extract_out.read_text(encoding="utf-8")
        check("le pied de page répété a disparu (même nettoyage qu'une traduction)",
              "P a g e" not in extract_content)
        check("le texte reste dans la langue source (aucun préfixe « FR »)",
              "FR " not in extract_content)
        check("le vrai contenu (langue source) est bien là",
              "extract-only test, long enough to count on its own." in extract_content)
        check("le faux titre a été rétrogradé (nettoyage typographique appliqué sans traduction)",
              "## This is a full sentence" not in extract_content
              and "This is a full sentence that should not be a heading." in extract_content)
        check("rapport de nettoyage des pages toujours rempli en mode extraction",
              result_extract.page_cleanup_report is not None and bool(result_extract.page_cleanup_report.groups))

        print("\n11. Segment « restricted » (page bloquée par un filtre de contenu) : jamais traduit")
        restricted_segments = [
            {"type": "paragraph", "text": "Normal paragraph one, should be translated like usual."},
            {
                "type": "restricted",
                "text": (
                    "⛔ TRANSLAX — DÉBUT PAGE NON VÉRIFIÉE (page 12) : texte original conservé.\n"
                    "Original untranslated content of the blocked page.\n"
                    "⛔ TRANSLAX — FIN PAGE NON VÉRIFIÉE (page 12)"
                ),
            },
            {"type": "paragraph", "text": "Normal paragraph two, also translated like usual."},
        ]
        restricted_out = out_dir / "restricted.md"
        engine_r = FakeEngine()
        done_r = translate.translate_segments(restricted_segments, restricted_out, engine_r)
        check("les 3 segments comptent comme écrits", done_r == 3)
        check("seuls les 2 vrais paragraphes ont été envoyés au moteur (pas le segment restreint)",
              len(engine_r.calls) == 2, f"({len(engine_r.calls)})")
        restricted_content = restricted_out.read_text(encoding="utf-8")
        check("les vrais paragraphes sont bien traduits (préfixe « FR »)",
              "FR Normal paragraph one" in restricted_content and "FR Normal paragraph two" in restricted_content)
        check("le segment restreint reste dans sa langue d'origine, jamais préfixé",
              "Original untranslated content of the blocked page." in restricted_content
              and "FR Original untranslated" not in restricted_content)
        check("le segment restreint est rendu en citation Markdown, marqueurs conservés",
              "> ⛔ TRANSLAX — DÉBUT PAGE NON VÉRIFIÉE (page 12)" in restricted_content
              and "> ⛔ TRANSLAX — FIN PAGE NON VÉRIFIÉE (page 12)" in restricted_content)

        print("\n12. Reprise avec un moteur différent de celui d'origine (demande explicite de l'utilisateur)")
        # Deuxième faux moteur, distinct du premier -- pour prouver que la
        # SUITE d'un job interrompu passe bien par un moteur DIFFÉRENT une
        # fois repris avec un autre model_key, sans retoucher aux segments
        # déjà écrits (voir ui/main_window.py::_offer_resume_pending_jobs,
        # « Choisir un autre moteur… »).
        other_engines: list[FakeEngine] = []

        def other_engine_factory(*args, **kwargs):
            engine = FakeEngine()
            other_engines.append(engine)
            return engine

        original_fast_engine = translate.FastEngine
        pipeline.translate.FastEngine = other_engine_factory  # type: ignore[assignment]
        try:
            switch_source = make_source(workdir, count=12)
            switch_source = switch_source.rename(workdir / "switch_engine.txt")
            switch_out = out_dir / "switch_engine.md"

            def stop_after_five():
                return len(engines[-1].calls) >= 5

            pipeline.run_job(
                pipeline.Job(input_path=switch_source, output_path=switch_out,
                             model_key="600M", translate_title=False, strategy="blocks"),
                should_stop=stop_after_five,
            )
            first_engine_calls = len(engines[-1].calls)
            check("interrompu après 5 segments avec le moteur d'origine (precise)", first_engine_calls == 5)

            result_switched = pipeline.run_job(
                pipeline.Job(input_path=switch_source, output_path=switch_out,
                             model_key="600M-ct2", translate_title=False, strategy="blocks"),
            )
            check("reprise détectée à partir du bon segment", result_switched.resumed_from == 5,
                  f"(obtenu {result_switched.resumed_from})")
            check("aucun appel supplémentaire sur l'ANCIEN moteur (segments déjà écrits jamais retouchés)",
                  len(engines[-1].calls) == first_engine_calls, f"({len(engines[-1].calls)})")
            check("les segments restants sont passés par le NOUVEAU moteur",
                  len(other_engines) == 1 and len(other_engines[0].calls) == 12 - 5,
                  f"({len(other_engines[0].calls) if other_engines else 0})")
            check("12 blocs au total, rien perdu ni dupliqué au passage",
                  count_blocks(switch_out) == 12, f"(obtenu {count_blocks(switch_out)})")
            switched_state = state.load_state(switch_out)
            check("l'état persisté reflète le NOUVEAU moteur, pas l'ancien (métadonnée pas laissée périmée)",
                  switched_state is not None and switched_state.model == "600M-ct2",
                  f"({switched_state.model if switched_state else None})")
        finally:
            pipeline.translate.FastEngine = original_fast_engine  # type: ignore[assignment]

        print("\n13. Export PDF en plus du .md (demande explicite de l'utilisateur)")
        pdf_source = make_source(workdir, count=6)
        pdf_source = pdf_source.rename(workdir / "pdf_export_test.txt")
        pdf_out = out_dir / "pdf_export_test.md"
        result_pdf = pipeline.run_job(pipeline.Job(
            input_path=pdf_source, output_path=pdf_out, translate_title=False,
            strategy="blocks", output_format="pdf",
        ))
        check("job non annulé", not result_pdf.cancelled)
        check("le .md reste écrit normalement (jamais remplacé par le PDF)", pdf_out.exists())
        check("un chemin PDF est renvoyé", result_pdf.pdf_path is not None)
        check("le fichier PDF existe réellement sur le disque",
              result_pdf.pdf_path is not None and result_pdf.pdf_path.exists())
        check("le PDF est à côté du .md, même nom de base",
              result_pdf.pdf_path == pdf_out.with_suffix(".pdf"))

        import pymupdf
        pdf_doc = pymupdf.open(str(result_pdf.pdf_path))
        pdf_text = "\n".join(page.get_text() for page in pdf_doc)
        pdf_doc.close()
        check("le vrai texte traduit (préfixe « FR ») est bien dans le PDF rendu, pas juste dans le .md",
              "FR Paragraph number 0" in pdf_text, f"({pdf_text[:120]!r})")

        print("\n14. output_format par défaut (« md ») ne génère aucun PDF")
        no_pdf_source = workdir / "no_pdf_test.txt"
        no_pdf_source.write_text(pdf_source.read_text(encoding="utf-8"), encoding="utf-8")
        no_pdf_out = out_dir / "no_pdf_test.md"
        result_no_pdf = pipeline.run_job(pipeline.Job(
            input_path=no_pdf_source, output_path=no_pdf_out, translate_title=False, strategy="blocks",
        ))
        check("aucun chemin PDF renvoyé par défaut", result_no_pdf.pdf_path is None)
        check("aucun fichier .pdf créé par défaut", not no_pdf_out.with_suffix(".pdf").exists())

    finally:
        pipeline.translate.PreciseEngine = original_engine  # type: ignore[assignment]
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    if failures:
        print(f"{len(failures)} test(s) en échec : " + ", ".join(failures))
        return 1
    print("Tous les tests passent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
