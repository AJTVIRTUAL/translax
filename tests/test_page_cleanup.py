"""
Tests de `core/page_cleanup.py` sur des documents synthétiques qui
reproduisent les deux motifs réels observés (voir SPEC.md) :

  - pied de page numéroté qui varie seulement par le numéro
    (« 1 | P a g e », « 2 | P a g e »... — The Code to the Matrix) ;
  - en-tête « numéro romain + titre de chapitre », avec du bruit d'OCR
    d'une occurrence à l'autre (« THBVODOU » vs « THEVODOU » — The Vodou
    Quantum Leap) ;
  - en-têtes alternés verso/recto (titre du livre à gauche, titre de
    chapitre à droite — A New Era of Thought), avec un numéro de page
    illisible par l'OCR (symbole isolé au lieu d'un chiffre) sur l'une des
    occurrences.

Rapide par construction (documents synthétiques, pas les vrais PDF) :
sert aussi de garde-fou contre une régression de performance (le premier
jet de l'algorithme mettait plusieurs minutes sur un livre de 231 pages
avant le filtre de forme -- voir le commentaire de MAX_CANDIDATE_CHARS).

    python tests/test_page_cleanup.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import page_cleanup  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK   {label}")
    else:
        print(f"  ECHEC {label} {detail}")
        failures.append(label)


def make_footer_book(pages: int = 40) -> str:
    """Reproduit le motif « 1 | P a g e » : un pied de page numéroté qui
    ne varie que par le numéro, sur un texte de corps normal."""
    blocks = []
    for i in range(1, pages + 1):
        body = f"Ceci est le contenu réel de la page {i}, une phrase normale du livre."
        blocks.append(f"{body}\n\n\n{i} | P a g e")
    return "\f".join(blocks)


def make_chapter_header_book() -> str:
    """Reproduit le motif « xiv Foreword » / « Preface xxiv », avec un peu
    de bruit d'OCR sur l'une des occurrences du titre."""
    pages = []
    # "Foreword" sur 4 pages (numérotation romaine avant OU après le titre,
    # comme dans un vrai livre où verso/recto alternent la mise en page)
    romans = ["xiv", "xv", "xvi", "xvii"]
    for i, roman in enumerate(romans):
        header = f"{roman}   Foreword" if i % 2 == 0 else f"Foreword   {roman}"
        pages.append(f"{header}\n\nContenu de l'avant-propos, page {i}, du vrai texte à traduire.")
    # "Preface" sur 3 pages, avec une occurrence bruitée par l'OCR
    prefaces = ["Preface", "Preface", "Prefaee"]  # 3e occurrence : bruit d'OCR (c -> e)
    for i, title in enumerate(prefaces):
        pages.append(f"xviii   {title}\n\nContenu de la préface, page {i}, du vrai texte à traduire.")
    return "\f".join(pages)


def make_alternating_header_book() -> str:
    """
    Reproduit la disposition verso/recto d'un livre imprimé : le titre du
    livre en haut des pages paires, le titre du chapitre en haut des pages
    impaires -- deux motifs DIFFÉRENTS qui se répètent chacun de leur côté
    (cas réel rencontré : « A New Era of Thought », voir SPEC.md §5). La
    dernière occurrence du titre de chapitre a un numéro de page corrompu
    par l'OCR -- un symbole isolé au lieu d'un chiffre ou d'un romain --
    exactement comme rencontré dans ce vrai livre.
    """
    pages = []
    for i in range(6):
        body = f"Vrai contenu de la page {i}, une phrase normale du chapitre."
        if i % 2 == 0:
            header = f"{i}      BOOK TITLE."
        elif i == 5:
            header = "                     Chapter One.                                         •/"
        else:
            header = f"                    Chapter One.                   {i}"
        pages.append(f"{header}\n\n{body}")
    return "\f".join(pages)


def main() -> int:
    print("\n1. Pied de page numéroté (« N | P a g e »)")
    raw = make_footer_book(40)
    t0 = time.time()
    cleaned, report = page_cleanup.clean_pdf_pages(raw)
    elapsed = time.time() - t0
    check("détection rapide (<2s)", elapsed < 2.0, f"({elapsed:.2f}s)")
    check("un seul groupe détecté", len(report.groups) == 1, f"({len(report.groups)} groupes)")
    if report.groups:
        check("toutes les occurrences retirées", report.groups[0].count == 40, f"({report.groups[0].count})")
    check("le pied de page a disparu", "P a g e" not in cleaned)
    check("le contenu réel est intact", all(f"page {i}," in cleaned for i in range(1, 41)))

    print("\n2. En-têtes de chapitre courts, avec bruit d'OCR")
    raw2 = make_chapter_header_book()
    cleaned2, report2 = page_cleanup.clean_pdf_pages(raw2)
    kinds = {g.representative for g in report2.groups if not g.is_page_number}
    check("« Foreword » détecté malgré l'alternance numéro avant/après", "Foreword" in kinds, f"({kinds})")
    preface_group = next((g for g in report2.groups if "Preface" in g.representative), None)
    check("« Preface » détecté", preface_group is not None)
    if preface_group:
        check("la variante bruitée par l'OCR (« Prefaee ») est regroupée avec les autres",
              preface_group.count == 3, f"({preface_group.count})")
    check("les numéros romains d'en-tête ont disparu", "xiv" not in cleaned2 and "xviii" not in cleaned2)
    check("le vrai contenu est intact", "avant-propos" in cleaned2 and "préface" in cleaned2)

    print("\n3. En-têtes alternés verso/recto (titre du livre / titre de chapitre)")
    raw4 = make_alternating_header_book()
    cleaned4, report4 = page_cleanup.clean_pdf_pages(raw4)
    kinds4 = {g.representative for g in report4.groups if not g.is_page_number}
    check("le titre du livre (pages paires) détecté séparément", "BOOK TITLE." in kinds4, f"({kinds4})")
    chapter_group = next((g for g in report4.groups if "Chapter One" in g.representative), None)
    check("le titre de chapitre (pages impaires) détecté séparément", chapter_group is not None)
    if chapter_group:
        check("l'occurrence au numéro de page illisible par l'OCR est quand même regroupée",
              chapter_group.count == 3, f"({chapter_group.count})")
    check("les deux en-têtes ont disparu du texte nettoyé",
          "BOOK TITLE." not in cleaned4 and "Chapter One." not in cleaned4)
    check("le vrai contenu est intact", all(f"page {i}," in cleaned4 for i in range(6)))

    print("\n4. Cas limites")
    empty_report_pages = page_cleanup.detect_running_headers([])
    check("liste de pages vide : aucun groupe, pas d'erreur", empty_report_pages.groups == [])

    single_page = "Un texte sur une seule page, sans aucun saut de page."
    cleaned3, report3 = page_cleanup.clean_pdf_pages(single_page)
    check("texte sans saut de page (TXT/MD) : rien détecté, rien modifié",
          report3.groups == [] and cleaned3 == single_page)

    check("numéro arabe isolé reconnu", page_cleanup.is_bare_page_number("42"))
    check("numéro romain isolé reconnu", page_cleanup.is_bare_page_number("xiv"))
    check("un mot normal n'est pas pris pour un numéro", not page_cleanup.is_bare_page_number("Chapitre"))
    check("une phrase de corps de texte n'est pas une forme de candidat",
          not page_cleanup._is_candidate_shape(  # noqa: SLF001 - test délibéré de l'heuristique interne
              "Ceci est une phrase bien trop longue pour être un en-tête ou un pied de page plausible."
          ))

    print()
    if failures:
        print(f"{len(failures)} test(s) en échec : " + ", ".join(failures))
        return 1
    print("Tous les tests de nettoyage de pages passent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
