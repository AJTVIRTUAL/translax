"""
Tests de `core/document_flow.py` -- recollage des paragraphes coupés entre
deux pages, retrait des répétitions inutiles, repérage textuel des notes
(ajouté le 11/09/2026, demande explicite de l'utilisateur).

Ce module ne travaillant que sur du TEXTE (jamais sur de la géométrie), tout
se teste directement avec de vraies chaînes paginées -- le format exact que
produisent `extract.py`, `layout.py` et `vision_ocr.py` : des pages séparées
par un saut de page (``\\f``).

Plusieurs cas ci-dessous sont des **régressions réelles** rencontrées pendant
l'écriture du module, pas des cas imaginés : ils sont signalés comme tels.

    python tests/test_document_flow.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import document_flow, segment  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK   {label}")
    else:
        print(f"  ECHEC {label} {detail}")
        failures.append(label)


def main() -> int:
    print("\n1. Recollage d'un paragraphe coupé, avec des pages entières entre les deux moitiés")
    # Reproduction fidèle du cas signalé par l'utilisateur : la phrase
    # s'interrompt page 1, deux pages de tableau suivent, la suite est page 4.
    pages = [
        "The third author recomputed all effect sizes and recoded all study "
        "attributes. Only one coding",
        "Study : Bender (1968) ; Total n : 96",
        "Study : Carlson (1934) ; Total n : 100",
        "variable (goal of study) involved a subjective judgment, and the two "
        "discrepancies for this variable were resolved by discussion.",
    ]
    fixed, report = document_flow.repair("\f".join(pages))
    check("le paragraphe est recollé avec sa suite exacte",
          "Only one coding variable (goal of study) involved a subjective judgment" in fixed,
          f"({fixed[:160]!r})")
    check("le rapport le dit clairement, rien de silencieux", report.paragraphs_stitched == 1,
          f"({report.paragraphs_stitched})")
    check("les pages intercalées ne sont pas perdues pour autant",
          "Bender (1968)" in fixed and "Carlson (1934)" in fixed)

    print("\n1 bis. Un paragraphe coupé en TROIS morceaux est recollé en entier")
    # Un long paragraphe peut être interrompu plusieurs fois (deux tableaux,
    # une figure...). S'arrêter au premier recollage laisserait la fin
    # orpheline -- ce que ce test vérifie réellement.
    three_parts = [
        "The author recomputed all effect sizes and recoded every one of the study "
        "attributes. Only one coding",
        "Study : Bender (1968) ; Total n : 96 ; Effect : -.10",
        "variable involved a subjective judgment, and the two discrepancies were",
        "Study : Carlson (1934) ; Total n : 100 ; Effect : -.19",
        "resolved by discussion between the three authors involved in the review.",
    ]
    whole, report_three = document_flow.repair("\f".join(three_parts))
    joined = [p for p in whole.replace("\f", "\n").split("\n\n") if "Only one coding" in p]
    check("les deux coupures sont recollées", report_three.paragraphs_stitched == 2,
          f"({report_three.paragraphs_stitched})")
    check("les trois morceaux forment un seul paragraphe continu",
          bool(joined) and "Only one coding variable involved a subjective judgment" in joined[0]
          and "discrepancies were resolved by discussion" in joined[0],
          f"({joined[:1]})")

    print("\n2. Ce qu'il ne faut SURTOUT pas recoller")
    finished = "This paragraph ends properly.\fA New Chapter Title"
    _, report_finished = document_flow.repair(finished)
    check("un paragraphe déjà terminé par un point n'est pas prolongé",
          report_finished.paragraphs_stitched == 0)

    heading = ("A paragraph that runs on without any final punctuation mark at all "
               "and keeps going for a while\fConclusion")
    _, report_heading = document_flow.repair(heading)
    check("un TITRE court n'est jamais absorbé comme suite d'un paragraphe",
          report_heading.paragraphs_stitched == 0)

    capital = ("A paragraph that stops without punctuation and runs long enough to "
               "count as a real paragraph\fThe next paragraph starts with a capital "
               "letter, so it begins something new.")
    _, report_capital = document_flow.repair(capital)
    check("un paragraphe commençant par une MAJUSCULE n'est pas pris pour une suite",
          report_capital.paragraphs_stitched == 0)

    print("\n3. Césure réparée au moment du recollage")
    hyphen = ("The relation was described as being highly intelli-"
              "\fgent by every one of the reviewers involved.")
    joined, _ = document_flow.repair(hyphen)
    check("« intelli- » + « gent » redonne bien « intelligent »",
          "intelligent by every one" in joined, f"({joined!r})")

    print("\n4. Répétitions inutiles : le filigrane que `page_cleanup` laisse passer")
    watermark = "Downloaded from pdf.highwire.org by guest on August 12, 2013"
    body = [f"Page {i} carries its own distinct sentence of real content here.\n{watermark}"
            for i in range(1, 9)]
    cleaned, report_repeat = document_flow.repair("\f".join(body))
    check("le filigrane répété sur toutes les pages disparaît",
          watermark not in cleaned, f"({cleaned[:120]!r})")
    check("le rapport compte bien les lignes retirées", report_repeat.repeated_lines_removed == 8,
          f"({report_repeat.repeated_lines_removed})")
    check("le vrai contenu de chaque page est intact",
          all(f"Page {i} carries" in cleaned for i in range(1, 9)))

    print("\n5. RÉGRESSION RÉELLE : ne jamais confondre « répété » et « qui se ressemble »")
    # Rencontré pendant l'écriture : neutraliser les chiffres avant de comparer
    # (« page 4 » et « page 5 » ramenés à « page # ») faisait passer cinq
    # paragraphes de corps de texte pour cinq copies d'une même ligne -- et les
    # supprimait tous les cinq. Vrai contenu perdu en silence.
    similar = [f"Paragraph number {i} of the paginated test document, long enough "
               f"to count on its own." for i in range(1, 6)]
    kept, report_similar = document_flow.repair("\f".join(similar))
    check("cinq paragraphes qui ne diffèrent QUE par un chiffre sont tous conservés",
          all(f"Paragraph number {i} " in kept for i in range(1, 6)),
          f"({report_similar.repeated_lines_removed} ligne(s) retirée(s) à tort)")

    print("\n6. RÉGRESSION RÉELLE : la découpe en paragraphes survit au recollage")
    # Rencontré pendant l'écriture : recoller les pages avec « \\f » seul
    # supprimait la ligne vide de fin de page. `segment.py` remplaçant ensuite
    # « \\f » par un simple retour à la ligne, les cinq paragraphes n'en
    # formaient plus qu'un seul, géant.
    paginated = "\f".join(f"Paragraph number {i} of the document, long enough to stand alone.\n\n"
                          for i in range(1, 6))
    repaired, _ = document_flow.repair(paginated)
    blocks = segment.segment_text(repaired, strategy="blocks")
    check("les 5 paragraphes restent 5 segments distincts", len(blocks) == 5,
          f"(obtenu {len(blocks)})")

    print("\n7. Le choix « original » de l'utilisateur est respecté")
    with_footer = "\f".join([f"Real sentence number {i} on this page.\nCONFIDENTIAL DRAFT COPY"
                             for i in range(1, 9)])
    untouched, report_original = document_flow.repair(with_footer, drop_repeats=False)
    check("aucune ligne répétée n'est retirée quand on demande le texte original",
          untouched.count("CONFIDENTIAL DRAFT COPY") == 8,
          f"({untouched.count('CONFIDENTIAL DRAFT COPY')})")
    check("le rapport n'annonce alors aucun retrait", report_original.repeated_lines_removed == 0)

    print("\n8. Un document court ne déclenche aucune conclusion hâtive")
    short = "\f".join(["Same line", "Same line"])
    short_out, short_report = document_flow.repair(short)
    check("deux pages ne suffisent pas à déclarer une ligne « répétée »",
          short_out.count("Same line") == 2 and short_report.repeated_lines_removed == 0)

    print("\n9. Repérage textuel d'une note de bas de page (sources sans géométrie)")
    check("un appel de note numéroté suivi d'une explication courte est repéré",
          document_flow.looks_like_footnote_text("1. Kanazawa conducted these analyses separately."))
    check("« Note. » est repéré", document_flow.looks_like_footnote_text("Note. GPA = grade point average."))
    check("un vrai paragraphe long n'est jamais pris pour une note",
          not document_flow.looks_like_footnote_text(
              "1. " + "This is ordinary body text that happens to start with a number "
              "and continues at length, well past anything a footnote would ever be, "
              "so it must not be treated as one. " * 3))
    check("une ligne vide n'est pas une note", not document_flow.looks_like_footnote_text("   "))

    print()
    if failures:
        print(f"{len(failures)} test(s) en échec : " + ", ".join(failures))
        return 1
    print("Tous les tests de remise en ordre du texte passent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
