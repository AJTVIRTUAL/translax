"""
Tests de `core/paragraphs.py` -- aération des paragraphes trop longs en fin
de traduction (demande explicite de l'utilisateur, 10/11/2026 : « 6 lignes
maximum par paragraphe », pour le .md comme pour le PDF).

    python tests/test_paragraphs.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import paragraphs, pdf_export, pipeline, postprocess, translate  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK   {label}")
    else:
        print(f"  ECHEC {label} {detail}")
        failures.append(label)


def words(text: str) -> list[str]:
    return text.split()


SENTENCES = [
    "Le conseil s'est réuni à l'aube pour examiner les nouvelles propositions venues de la capitale.",
    "Personne ne savait vraiment ce que contenaient les documents scellés depuis des années.",
    "M. Dupont, le plus ancien des membres, demanda la parole avant tout le monde.",
    "Il rappela que le Dr. Martin avait déjà mis en garde l'assemblée contre une décision hâtive.",
    "« Nous devons attendre. » Puis il se rassit, sans un mot de plus.",
    "La séance reprit une heure plus tard, dans une atmosphère beaucoup plus tendue qu'auparavant.",
    "Les délégués du Nord exigeaient une réponse immédiate, ceux du Sud réclamaient un délai.",
    "À midi, aucun accord n'avait encore été trouvé entre les deux camps.",
    "Le président suspendit alors les débats jusqu'au lendemain matin, à neuf heures précises.",
    "Chacun repartit avec la certitude que la journée suivante serait décisive pour tous.",
]
LONG_PARAGRAPH = " ".join(SENTENCES)


def main() -> int:
    measure = paragraphs.pdf_measure()
    max_lines = paragraphs.DEFAULT_MAX_LINES

    print("\n1. Paragraphe court : jamais touché")
    short = "Une seule phrase, bien assez courte pour tenir sur une ligne."
    check("renvoyé tel quel", paragraphs.split_paragraph(short) == [short])

    print("\n2. Paragraphe long : découpé, chaque morceau <= 6 lignes, aucun mot perdu")
    total = measure.count_lines(LONG_PARAGRAPH)
    pieces = paragraphs.split_paragraph(LONG_PARAGRAPH)
    check(f"le paragraphe de test dépasse bien la limite ({total} lignes)", total > max_lines)
    check("découpé en plusieurs morceaux", len(pieces) > 1, f"({len(pieces)})")
    check("chaque morceau <= 6 lignes",
          all(measure.count_lines(p) <= max_lines for p in pieces),
          f"({[measure.count_lines(p) for p in pieces]})")
    check("texte strictement identique une fois recollé", words(" ".join(pieces)) == words(LONG_PARAGRAPH))
    check("chaque morceau commence par le début d'une phrase",
          all(any(p.startswith(s[:25]) for s in SENTENCES) or p.startswith("Puis") for p in pieces),
          f"({[p[:30] for p in pieces]})")
    counts = [measure.count_lines(p) for p in pieces]
    check("morceaux équilibrés (pas de 6 + 1)", max(counts) - min(counts) <= 2, f"({counts})")

    print("\n3. Fins de phrase : abréviations et guillemets")
    points = paragraphs.sentence_boundaries(
        "Il vit M. Dupont au marché. Le Dr. Martin aussi. J. F. Kennedy était là. "
        "Aux U.S. Army comme ailleurs. Voir ci-dessus. 1. Ils partent. Fin."
    )
    text = ("Il vit M. Dupont au marché. Le Dr. Martin aussi. J. F. Kennedy était là. "
            "Aux U.S. Army comme ailleurs. Voir ci-dessus. 1. Ils partent. Fin.")
    starts = [text[p:p + 6] for p in points]
    check("jamais après « M. », « Dr. », une initiale, « U.S. » ni « 1. »",
          starts == ["Le Dr.", "J. F. ", "Aux U.", "Voir c", "1. Ils", "Fin."], f"({starts})")
    quote = "Il cria : « Viens. » Puis il partit. « Où ? » dit-elle."
    qstarts = [quote[p:p + 4] for p in paragraphs.sentence_boundaries(quote)]
    check("coupe après un guillemet fermant, jamais avant une incise en minuscule",
          qstarts == ["Puis", "« Où"], f"({qstarts})")
    check("pas de coupe avant une minuscule (« etc. et »)",
          paragraphs.sentence_boundaries("Des pommes, des poires, etc. et des noix.") == [])
    check("points de suspension = vraie fin de phrase",
          len(paragraphs.sentence_boundaries("Il hésita... Puis il entra.")) == 1)
    cjk = "这是第一句话。这是第二句话。这是第三句话。"
    check("ponctuation chinoise « 。 » reconnue (sans espace)",
          len(paragraphs.sentence_boundaries(cjk)) == 2)

    print("\n4. Phrase seule plus longue que la limite : coupée à une virgule")
    long_sentence = ", ".join(
        f"la fondation numéro {i} a versé une somme considérable à des organisations diverses"
        for i in range(1, 16)
    ) + "."
    check("la phrase de test dépasse la limite", measure.count_lines(long_sentence) > max_lines)
    report = paragraphs.SplitReport()
    cut = paragraphs.split_paragraph(long_sentence, report=report)
    check("chaque morceau <= 6 lignes", all(measure.count_lines(p) <= max_lines for p in cut),
          f"({[measure.count_lines(p) for p in cut]})")
    check("coupée juste après une virgule", all(p.endswith((",", ".")) for p in cut), f"({[p[-5:] for p in cut]})")
    check("comptée dans le rapport", report.long_sentences_cut == 1)
    list_blob = " ".join(f"Fondation{i} -- {i * 1000} $" for i in range(1, 80))
    blob_cut = paragraphs.split_paragraph(list_blob)
    check("même une liste sans ponctuation respecte la limite (dernier recours : entre deux mots)",
          all(measure.count_lines(p) <= max_lines for p in blob_cut) and words(" ".join(blob_cut)) == words(list_blob))

    print("\n5. Markdown complet : seuls les paragraphes ordinaires sont touchés")
    md = "\n".join([
        translate.render_markdown("title", "Titre du livre"),
        translate.render_markdown("heading", "Chapitre premier"),
        translate.render_markdown("paragraph", LONG_PARAGRAPH),
        translate.render_markdown("bullet", LONG_PARAGRAPH),
        translate.render_markdown("restricted", "⛔ TRANSLAX -- page 3\n" + LONG_PARAGRAPH),
        translate.render_markdown("paragraph", short),
    ])
    out, report = paragraphs.split_markdown(md)
    blocks = [b.strip() for b in out.split("\n\n") if b.strip()]
    check("titre et sous-titre intacts", blocks[0] == "# Titre du livre" and blocks[1] == "## Chapitre premier")
    check("puce intacte (jamais découpée)", f"- {LONG_PARAGRAPH}" in blocks)
    check("bloc restreint intact", any(b.startswith("> ⛔ TRANSLAX") and LONG_PARAGRAPH in b for b in blocks))
    check("paragraphe long découpé, paragraphe court intact",
          report.paragraphs_split == 1 and blocks[-1] == short, f"({report})")
    check("rapport lisible", "découpé" in report.summary(), f"({report.summary()!r})")
    again, report2 = paragraphs.split_markdown(out)
    check("idempotent : aérer deux fois ne change plus rien", again == out and report2.paragraphs_split == 0)
    check("fichier CRLF (Windows) accepté", paragraphs.split_markdown(md.replace("\n", "\r\n"))[0] == out)

    workdir = Path(tempfile.mkdtemp(prefix="translax_paragraphs_"))
    try:
        print("\n6. split_file : sauvegarde commune avec le nettoyage, annulable")
        md_path = workdir / "livre.md"
        md_path.write_text(md, encoding="utf-8")
        file_report = paragraphs.split_file(md_path)
        check("fichier réécrit", md_path.read_text(encoding="utf-8") == out and file_report.paragraphs_split == 1)
        check("sauvegarde créée", postprocess.has_backup(md_path))
        check("« Annuler le nettoyage » restaure l'original",
              postprocess.undo_cleanup(md_path) and md_path.read_text(encoding="utf-8") == md)
        untouched = workdir / "court.md"
        untouched.write_text(translate.render_markdown("paragraph", short), encoding="utf-8")
        paragraphs.split_file(untouched)
        check("rien à aérer : ni réécriture ni sauvegarde", not postprocess.has_backup(untouched))

        print("\n7. Rendu PDF réel : aucun paragraphe de plus de 6 lignes, texte agrandi")
        # Titres + paragraphes ordinaires seulement : puces et citations ne
        # sont volontairement jamais découpées (voir section 5).
        book = "\n".join(
            [translate.render_markdown("title", "Titre du livre")]
            + [translate.render_markdown("paragraph", p) for p in [LONG_PARAGRAPH] * 12 + [short]]
        )
        aired, _ = paragraphs.split_markdown(book)
        pdf_path = workdir / "livre.pdf"
        pdf_export.markdown_to_pdf(aired, pdf_path)
        import pymupdf
        doc = pymupdf.open(str(pdf_path))
        body_blocks = []
        body_sizes = set()
        for page in doc:
            for block in page.get_text("dict")["blocks"]:
                if block.get("type") != 0:
                    continue
                sizes = {round(s["size"]) for line in block["lines"] for s in line["spans"]}
                text = " ".join("".join(s["text"] for s in line["spans"]) for line in block["lines"])
                if sizes & {26, 18, 9}:
                    continue  # titres, numéros de page
                body_sizes |= sizes
                body_blocks.append(len({round(line["bbox"][1]) for line in block["lines"]}))
        doc.close()
        check("texte du corps en 14 pt (au lieu de 11)", body_sizes == {14}, f"({body_sizes})")
        check("aucun paragraphe de plus de 6 lignes réelles dans le PDF",
              bool(body_blocks) and max(body_blocks) <= max_lines, f"({body_blocks})")
        check("numéros de page ajoutés (« 1 / N »)",
              "1 / " in pymupdf.open(str(pdf_path))[0].get_text())

        print("\n8. Pipeline : aération en fin de job (extraction seule, sans modèle)")
        source = workdir / "source.txt"
        source.write_text("\n\n".join([LONG_PARAGRAPH] * 30) + "\n", encoding="utf-8")
        result = pipeline.run_job(pipeline.Job(
            input_path=source, output_path=workdir / "sortie.md", extract_only=True,
            strategy="blocks", cleanup=False, output_format="pdf",
        ))
        check("rapport d'aération présent dans le résultat",
              result.paragraph_report is not None and result.paragraph_report.paragraphs_split == 30,
              f"({result.paragraph_report})")
        written = result.output_path.read_text(encoding="utf-8")
        check("le .md final est aéré",
              all(measure.count_lines(b) <= max_lines for b in written.split("\n\n") if b.strip()))
        check("le PDF est généré à partir du .md aéré", result.pdf_path is not None and result.pdf_path.exists())
        result_off = pipeline.run_job(pipeline.Job(
            input_path=source, output_path=workdir / "sortie_brute.md", extract_only=True,
            strategy="blocks", cleanup=False, split_paragraphs=False, resume="restart",
        ))
        check("case décochée : aucun découpage",
              result_off.paragraph_report is None
              and len([b for b in result_off.output_path.read_text(encoding="utf-8").split("\n\n") if b.strip()]) == 30)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    if failures:
        print(f"{len(failures)} test(s) en échec : " + ", ".join(failures))
        return 1
    print("Tous les tests d'aération des paragraphes passent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
