"""
Tests de `core/md_cleanup.py` -- nettoyage avancé d'un .md importé
(demande explicite de l'utilisateur, 19/11/2026).

    python tests/test_md_cleanup.py
"""
from __future__ import annotations

import re
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import md_cleanup  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK   {label}")
    else:
        print(f"  ECHEC {label} {detail}")
        failures.append(label)


BOOK = "LE COTÉ CACHÉ DES CHOSES"
CHAPTER = "PAR NOS HABITUDES"

FILLER = (
    "Le chercheur attentif remarque bien des choses que les autres ignorent, et il en tire "
    "des leçons utiles pour sa vie de tous les jours. "
)


def fake_book() -> str:
    """Un faux livre qui reproduit les pièges d'un vrai : en-têtes pairs
    (numéro + titre du livre) et impairs (titre du chapitre, numéro avant OU
    après), en pleine phrase ; mot coupé collé au numéro ; numéro de cahier ;
    table des matières ; coquille d'OCR ; début de chapitre et sections
    collés au texte ; texte manquant dans la source."""
    blocks = [
        "## Le côté caché des Choses",
        f"TABLE DES MATIÈRES CHAPITRE XIV. — {CHAPTER} 7 Nourriture. — Le tabac. "
        "CHAPITRE XV. — PAR NOTRE ENTOURAGE 35 Les habitations.",
        f"{BOOK} CHAPITRE XIV {CHAPTER} NOURRITURE Une sentence attribuée au Christ est souvent citée. "
        + FILLER * 3,
        # L'exemple exact de l'utilisateur
        f"Tout ceci sans préjudice de la détérioration apportée 10 {BOOK} au corps mental et astral "
        "par l’habitude de ces liqueurs qui enivrent. Celui qui cherche à justifier ses ignobles appétits "
        "affirme souvent que les aliments et la boisson appartenant comme ils le font, purement au monde "
        "physique, ne peuvent avoir que de faibles effets sur le développement intérieur de l'homme.",
        FILLER + f"Il en est surtout ainsi pour 8 {BOOK} la pâtisserie et le pain. " + FILLER,
        FILLER + f"Le premier pas est que l'homme apprenne complète11 {CHAPTER} ment à contrôler ses corps. "
        "Il doit le faire complètement, et sans attendre. " + FILLER,
        FILLER + f"Il se nourrit d'ali28 {BOOK} ments très épicés, comme tant d'autres aliments. " + FILLER,
        FILLER + f"Pendant 2 18 {BOOK} une longue période, l'homme demeure comme paralysé. " + FILLER,
        FILLER + f"Il apprend 12 {BOOK} la patience, jour après jour. " + FILLER,
        FILLER + f"car ses aînés s'y sont {CHAPTER} 17 habitués depuis longtemps. " + FILLER,
        FILLER + f"Il a fait son devoir. {CHAPTER} 19 LA PROPRETÉ Les médecins sont d'accord sur ce point. " + FILLER,
        FILLER + f"Une parole 13 {CHAPTER} prononcée au bon moment suffit. " + FILLER,
        # Texte manquant dans la source (vu sur un vrai livre) : « parfaite »
        # et « ce » sont deux vrais mots du livre -- jamais soudés.
        FILLER + "Sa santé reste parfaite malgré les années. " + FILLER,
        FILLER + f"Il voulait faire son devoir aussi parfaite14 {BOOK} ce point de vue aussi. " + FILLER,
        FILLER + f"Une coquille d'OCR traîne 16 LF COTÉ CACHÉ DES CHOSES ici aussi. " + FILLER,
        # Vu sur le vrai livre : coquille avec un point parasite, ET numéro de
        # cahier collé au mot coupé (« jus14 210 … qu'à » = « jusqu'à »).
        FILLER + "Il faut suivre jus14 210 LE COTÉ CACHÉ DES. GHOSES qu'à une conclusion logique. "
        "Il ira jusqu'à la fin. " + FILLER,
        FILLER + "Fin de la première partie. MÉTHODE ET SINCÉRITÉ L'étudiant occultiste agit avec soin. " + FILLER,
        "C. W. LEADBEATER Le côté caché des Choses, édition de 1928.",
    ]
    return "\n\n".join(blocks) + "\n"


EN_BOOK = "THE HIDDEN SIDE OF THINGS"
EN_CHAPTER = "BY OUR HABITS"
EN_FILLER = (
    "The careful student notices many things that others ignore, and draws useful "
    "lessons from them for daily life. "
)


def english_paragraphs() -> list[str]:
    """Le même genre de pièges, dans un texte SOURCE anglais (avant traduction)."""
    return [
        f"CHAPTER XIV {EN_CHAPTER} NOURISHMENT A saying attributed to the Christ is often quoted. "
        + EN_FILLER * 2,
        EN_FILLER + f"All this without prejudice to the harm done 10 {EN_BOOK} to the mental and astral bodies. "
        + EN_FILLER,
        EN_FILLER + f"He feeds on highly spi28 {EN_BOOK} ced foods, like so many others. " + EN_FILLER,
        EN_FILLER + f"He learns 12 {EN_BOOK} patience, day after day. " + EN_FILLER,
        EN_FILLER + f"His elders are long used {EN_CHAPTER} 17 to it. " + EN_FILLER,
        EN_FILLER + f"A word 13 {EN_CHAPTER} spoken at the right time is enough. " + EN_FILLER,
        EN_FILLER + f"Another line 19 {EN_CHAPTER} follows here. " + EN_FILLER,
        EN_FILLER + f"Some text 16 {EN_BOOK} goes on here. " + EN_FILLER,
    ]


def english_segments() -> list[dict]:
    paragraphs = english_paragraphs()
    segments = [{"type": "heading", "text": "The Hidden Side of Things"}]
    segments += [{"type": "paragraph", "text": p} for p in paragraphs[:2]]
    # Bloc restreint (jamais traduit) : intouchable, même s'il contient un en-tête.
    segments.append({"type": "restricted", "text": f"⛔ TRANSLAX bloc\nTexte original 30 {EN_BOOK} intact"})
    segments += [{"type": "paragraph", "text": p} for p in paragraphs[2:]]
    segments.append({"type": "heading", "text": f"20 {EN_BOOK}"})  # un en-tête seul sur sa ligne
    segments.append({"type": "bullet", "text": "first item of the list"})
    segments.append({"type": "paragraph", "text": "- Hello, he said, with a dialogue dash."})
    return segments


def lowercase_words(text: str) -> Counter:
    return Counter(re.findall(r"(?<![\w'’])[a-zà-öø-ÿœæ][\w'’-]*", text))


def main() -> int:
    source = fake_book()
    cleaned, report = md_cleanup.clean_markdown(source, extract_headings=True, aerate=False)

    print("\n1. En-têtes appris dans le document lui-même")
    found = {h.text for h in report.headers}
    check("titre du livre (pages paires) reconnu", BOOK in found, f"({found})")
    check("titre du chapitre (pages impaires, numéro avant OU après) reconnu", CHAPTER in found, f"({found})")
    check("aucun faux en-tête", found <= {BOOK, CHAPTER}, f"({found})")
    variants = {v for h in report.headers for v in h.variants}
    check("coquille d'OCR rattachée à son en-tête (« LF COTÉ… »)", "LF COTÉ CACHÉ DES CHOSES" in variants)
    check("… même avec un point parasite (« DES. GHOSES »)", "LE COTÉ CACHÉ DES. GHOSES" in variants, f"({variants})")

    print("\n2. Retrait en pleine phrase -- l'exemple de l'utilisateur")
    expected = (
        "Tout ceci sans préjudice de la détérioration apportée au corps mental et astral par l’habitude "
        "de ces liqueurs qui enivrent. Celui qui cherche à justifier ses ignobles appétits affirme souvent "
        "que les aliments et la boisson appartenant comme ils le font, purement au monde physique, ne "
        "peuvent avoir que de faibles effets sur le développement intérieur de l'homme."
    )
    check("phrase rendue exactement comme demandé", expected in cleaned)
    check("plus aucune trace de l'en-tête du livre (ni de sa coquille)",
          "COTÉ CACHÉ DES CHOSES" not in cleaned, f"({cleaned.count('COTÉ CACHÉ')})")
    check("« pour 8 LE COTÉ… la pâtisserie » -> « pour la pâtisserie »", "pour la pâtisserie" in cleaned)
    check("numéro APRÈS l'en-tête aussi (« PAR NOS HABITUDES 17 »)", "sont habitués" in cleaned)

    print("\n3. Réparations au passage")
    check("mot coupé recollé (« complète11 … ment » -> « complètement »)", "apprenne complètement à" in cleaned)
    check("mot coupé recollé (« d'ali28 … ments » -> « d'aliments »)", "d'aliments très" in cleaned)
    check("numéro de cahier retiré (« Pendant 2 18 … » -> « Pendant une »)", "Pendant une longue" in cleaned)
    check("numéro de cahier COLLÉ au mot coupé (« jus14 210 … qu'à » -> « jusqu'à »)",
          "suivre jusqu'à une conclusion" in cleaned)
    check("… et comptés", report.signature_marks_removed == 2, f"({report.signature_marks_removed})")
    check("texte manquant dans la source : deux vrais mots jamais soudés (« parfaite ce »)",
          "parfaite ce point" in cleaned and "parfaitece" not in cleaned)
    check("table des matières intacte (« — PAR NOS HABITUDES 7 »)",
          f"CHAPITRE XIV. — {CHAPTER} 7 Nourriture" in cleaned)

    print("\n4. Titres remis à leur place")
    lines = cleaned.split("\n")
    check("début de chapitre dégagé", f"# CHAPITRE XIV — {CHAPTER}" in lines, f"({[l for l in lines if l.startswith('#')]})")
    check("titre de section qui suivait le chapitre", "## NOURRITURE" in lines)
    check("section collée après une fin de phrase", "## LA PROPRETÉ" in lines and "## MÉTHODE ET SINCÉRITÉ" in lines)
    check("le paragraphe reprend juste après le titre",
          any(l.startswith("Une sentence attribuée") for l in lines))
    check("une initiale (« C. W. LEADBEATER ») n'est jamais prise pour un titre",
          "## LEADBEATER" not in lines and "C. W. LEADBEATER Le côté" in cleaned)
    check("titre de livre déjà en Markdown conservé", "## Le côté caché des Choses" in lines)

    print("\n5. Aucun mot d'auteur perdu")
    before, after = lowercase_words(source), lowercase_words(cleaned)
    lost = before - after
    # Seuls peuvent manquer les morceaux de mots recollés : celui collé au
    # numéro de page (« complète11 ») et sa fin, reprise après l'en-tête (« ment »).
    # Aussi « qu'à », fin de « jus14 210 LE COTÉ… qu'à » recollée en « jusqu'à ».
    unexpected = {
        w: n for w, n in lost.items() if not any(c.isdigit() for c in w) and w not in {"ment", "ments", "qu'à"}
    }
    check("seuls des morceaux de mots recollés manquent", not unexpected, f"({unexpected})")
    again, _ = md_cleanup.clean_markdown(cleaned, extract_headings=True, aerate=False)
    check("nettoyer deux fois ne change plus le texte (hors titres déjà dégagés)",
          lowercase_words(again) == lowercase_words(cleaned))

    print("\n6. Document sans en-têtes : rien n'est inventé")
    plain = "## Titre\n\nUne phrase normale. " + FILLER + "\n\nUn second paragraphe, en 2024, page 12.\n"
    plain_out, plain_report = md_cleanup.clean_markdown(plain, extract_headings=True, aerate=False)
    check("aucun en-tête détecté", not plain_report.headers)
    check("texte intact", lowercase_words(plain_out) == lowercase_words(plain))

    print("\n7. Fichier : un NOUVEAU fichier à côté, l'original jamais modifié")
    workdir = Path(tempfile.mkdtemp(prefix="translax_md_cleanup_"))
    try:
        original = workdir / "livre.md"
        original.write_text(source, encoding="utf-8")
        destination, pdf_path, file_report = md_cleanup.clean_file(original, make_pdf=True)
        check("original inchangé", original.read_text(encoding="utf-8") == source)
        check("résultat écrit sous « livre (nettoyé).md »", destination.name == "livre (nettoyé).md" and destination.exists())
        check("PDF généré à la demande", pdf_path is not None and pdf_path.exists())
        check("aération des paragraphes faite par défaut", file_report.paragraph_report is not None)
        second, _, _ = md_cleanup.clean_file(original)
        check("un second nettoyage n'écrase jamais le premier", second.name == "livre (nettoyé 2).md")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print("\n8. Par défaut à la fin d'une traduction / extraction (pipeline, sans modèle)")
    from core import pipeline, postprocess  # noqa: E402

    workdir = Path(tempfile.mkdtemp(prefix="translax_md_cleanup_job_"))
    try:
        book = workdir / "livre.txt"
        book.write_text(source, encoding="utf-8")
        check("activé par défaut sur un job", pipeline.Job(input_path=book).advanced_cleanup is True)
        result = pipeline.run_job(pipeline.Job(
            input_path=book, output_path=workdir / "extrait.md", extract_only=True,
            strategy="blocks", cleanup=False, split_paragraphs=False, output_format="pdf",
        ))
        written = result.output_path.read_text(encoding="utf-8")
        # Depuis le nettoyage du texte SOURCE (§10), le travail est fait avant
        # l'écriture ; la vérification finale ne trouve plus rien.
        check("rapport du nettoyage du texte source dans le résultat",
              result.source_cleanup_report is not None and result.source_cleanup_report.occurrences_removed > 0,
              f"({result.source_cleanup_report})")
        check("vérification finale passée, plus rien à retirer",
              result.md_cleanup_report is not None and result.md_cleanup_report.occurrences_removed == 0,
              f"({result.md_cleanup_report})")
        check("en-têtes retirés du .md final", "COTÉ CACHÉ DES CHOSES" not in written)
        check("l'exemple de l'utilisateur ressort corrigé", expected in written)
        check("titres dégagés dans le .md final", f"# CHAPITRE XIV — {CHAPTER}" in written.split("\n"))
        check("le PDF hérite du .md nettoyé", result.pdf_path is not None and result.pdf_path.exists())

        # Filet de sécurité seul (un .md dont la source n'a pas été nettoyée) :
        # réversible par « Annuler le nettoyage », même sauvegarde que le reste.
        md_path = workdir / "deja_traduit.md"
        md_path.write_text(source, encoding="utf-8")
        final_report = pipeline._maybe_advanced_cleanup(True, md_path, lambda _m: None, [])  # noqa: SLF001
        check("vérification finale : retire ce qui a échappé à la source",
              final_report is not None and final_report.occurrences_removed > 0
              and "COTÉ CACHÉ DES CHOSES" not in md_path.read_text(encoding="utf-8"))
        check("réversible : sauvegarde d'avant post-traitement", postprocess.has_backup(md_path))
        postprocess.undo_cleanup(md_path)
        check("« Annuler le nettoyage » rend le texte d'avant", md_path.read_text(encoding="utf-8") == source)

        with_split = pipeline.run_job(pipeline.Job(
            input_path=book, output_path=workdir / "extrait_aere.md", extract_only=True,
            strategy="blocks", cleanup=True,
        ))
        check("enchaîné avec le nettoyage typographique et l'aération",
              with_split.md_cleanup_report is not None and with_split.paragraph_report is not None
              and "COTÉ CACHÉ DES CHOSES" not in with_split.output_path.read_text(encoding="utf-8"))

        off = pipeline.run_job(pipeline.Job(
            input_path=book, output_path=workdir / "extrait_brut.md", extract_only=True,
            strategy="blocks", cleanup=False, split_paragraphs=False, advanced_cleanup=False,
        ))
        check("option décochée : texte laissé tel quel",
              off.md_cleanup_report is None and off.source_cleanup_report is None
              and "10 LE COTÉ CACHÉ DES CHOSES" in off.output_path.read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print("\n9. Texte SOURCE avant traduction : segments (livre anglais)")
    segments = english_segments()
    restricted = next(s for s in segments if s["type"] == "restricted")
    cleaned_segments, source_report = md_cleanup.clean_segments(segments)
    texts = [s["text"] for s in cleaned_segments]
    joined = "\n".join(texts)
    check("en-têtes anglais appris", {h.text for h in source_report.headers} == {EN_BOOK, EN_CHAPTER},
          f"({[h.text for h in source_report.headers]})")
    check("plus aucun en-tête dans le texte à traduire (hors bloc protégé)",
          not any("HIDDEN SIDE OF THINGS" in s["text"] or EN_CHAPTER in s["text"].replace(f"— {EN_CHAPTER}", "")
                  for s in cleaned_segments if s["type"] != "restricted"))
    check("phrase rendue propre au moteur", any("harm done to the mental and astral bodies" in t for t in texts))
    check("mot coupé recollé AVANT traduction (« spi28 … ced » -> « spiced »)",
          any("highly spiced foods" in t for t in texts))
    check("chapitre dégagé en segment « title », mot d'origine gardé",
          {"type": "title", "text": f"CHAPTER XIV — {EN_CHAPTER}"} in cleaned_segments,
          f"({[s for s in cleaned_segments if s['type'] != 'paragraph']})")
    check("section dégagée en segment « heading » (« A saying… » reconnu comme début de phrase)",
          {"type": "heading", "text": "NOURISHMENT"} in cleaned_segments)
    check("le paragraphe reprend après les titres",
          any(t.startswith("A saying attributed to the Christ") for t in texts))
    check("bloc restreint remis tel quel, jamais touché", restricted in cleaned_segments)
    check("… et à sa place (après le paragraphe qui le précédait)",
          texts.index(restricted["text"]) > next(i for i, t in enumerate(texts) if "harm done" in t))
    check("intertitre fait d'un en-tête seul : retiré", "20 THE HIDDEN SIDE OF THINGS" not in joined)
    check("vrai intertitre conservé", {"type": "heading", "text": "The Hidden Side of Things"} in cleaned_segments)
    check("puce conservée en puce", {"type": "bullet", "text": "first item of the list"} in cleaned_segments)
    check("tiret de dialogue : reste un paragraphe",
          {"type": "paragraph", "text": "- Hello, he said, with a dialogue dash."} in cleaned_segments)
    plain_segments = [{"type": "heading", "text": "Title"}, {"type": "paragraph", "text": FILLER.strip()}]
    check("segments sans en-têtes : rendus à l'identique",
          md_cleanup.clean_segments(plain_segments)[0] == plain_segments)

    print("\n10. Pipeline : le moteur ne reçoit plus les en-têtes (faux moteur, Traduire)")
    from core import translate  # noqa: E402

    received: list[str] = []

    class RecordingEngine:
        def __init__(self, *args, **kwargs):
            pass

        def load(self, on_status=None):
            pass

        def translate(self, text: str, *, heartbeat=None) -> str:
            received.append(text)
            return "FR " + text

        def unload(self):
            pass

    engine_names = ("PreciseEngine", "FastEngine", "OpusMtEngine", "MadladEngine")
    originals = {name: getattr(translate, name) for name in engine_names}
    for name in engine_names:
        setattr(translate, name, RecordingEngine)
    workdir = Path(tempfile.mkdtemp(prefix="translax_md_cleanup_src_"))
    try:
        book = workdir / "book.txt"
        book.write_text("\n\n".join(english_paragraphs()) + "\n", encoding="utf-8")
        result = pipeline.run_job(pipeline.Job(
            input_path=book, output_path=workdir / "book.md", strategy="blocks", translate_title=False,
        ))
        check("aucun en-tête envoyé au moteur", not any("HIDDEN SIDE OF THINGS" in t for t in received),
              f"({[t for t in received if 'HIDDEN' in t][:2]})")
        check("phrase propre envoyée au moteur", any("harm done to the mental and astral bodies" in t for t in received))
        check("mot recollé envoyé au moteur", any("highly spiced foods" in t for t in received))
        check("titre de chapitre traduit à part", f"CHAPTER XIV — {EN_CHAPTER}" in received)
        check("rapport du texte source dans le résultat",
              result.source_cleanup_report is not None and result.source_cleanup_report.occurrences_removed == 7,
              f"({result.source_cleanup_report})")
        check("vérification finale passée aussi (filet de sécurité)", result.md_cleanup_report is not None)
        written = result.output_path.read_text(encoding="utf-8")
        check("titre de chapitre en « # » dans le .md traduit",
              f"# FR CHAPTER XIV — {EN_CHAPTER}" in written.split("\n"))

        received.clear()
        off = pipeline.run_job(pipeline.Job(
            input_path=book, output_path=workdir / "book_off.md", strategy="blocks", translate_title=False,
            advanced_cleanup=False,
        ))
        check("option décochée : le moteur reçoit le texte tel quel",
              off.source_cleanup_report is None and any("10 THE HIDDEN SIDE OF THINGS" in t for t in received))

        print("\n11. Fenêtre de vérification des pages : la liste s'y ajoute, aucune pause de plus")
        pages = [
            f"{paragraph}\n\n\n{i} | P a g e" for i, paragraph in enumerate(english_paragraphs(), start=1)
        ]
        paginated = workdir / "paginated.txt"
        paginated.write_text("\f".join(pages), encoding="utf-8")
        shown: list[list[str]] = []

        def review(report, decision):
            shown.append(report.summary_lines())
            return decision

        received.clear()
        kept = pipeline.run_job(
            pipeline.Job(input_path=paginated, output_path=workdir / "paginated.md", strategy="blocks",
                         translate_title=False),
            on_page_cleanup=lambda report: review(report, "clean"),
        )
        check("une seule fenêtre, celle qui existait déjà", len(shown) == 1)
        check("elle liste les en-têtes en pleine phrase",
              any("nettoyage avancé" in line and EN_BOOK in line for line in shown[0]), f"({shown})")
        check("« version nettoyée » : retirés avant la traduction",
              kept.source_cleanup_report is not None and not any("HIDDEN SIDE" in t for t in received))

        received.clear()
        original = pipeline.run_job(
            pipeline.Job(input_path=paginated, output_path=workdir / "paginated_original.md",
                         strategy="blocks", translate_title=False),
            on_page_cleanup=lambda report: review(report, "original"),
        )
        check("« original » : en-têtes gardés, ni avant ni après la traduction",
              original.source_cleanup_report is None and original.md_cleanup_report is None
              and any("HIDDEN SIDE OF THINGS" in t for t in received))
    finally:
        for name, engine in originals.items():
            setattr(translate, name, engine)
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    if failures:
        print(f"{len(failures)} test(s) en échec : " + ", ".join(failures))
        return 1
    print("Tous les tests du nettoyage avancé passent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
