"""
Tests de `core/layout.py` -- l'analyse de la mise en page réelle d'un PDF
(ajoutée le 11/09/2026, demande explicite de l'utilisateur).

Deux niveaux, volontairement :

  1. Des PDF **réellement construits ici** avec PyMuPDF (pas des chaînes de
     caractères qui feraient semblant) : deux colonnes, un titre pleine
     largeur, un tableau imprimé en paysage. On sait exactement ce qu'ils
     contiennent, donc on peut affirmer ce que l'analyse doit en tirer.

  2. Le **vrai article** signalé par l'utilisateur, s'il est présent sur cette
     machine : « Relation between Intelligence and religiosity.pdf ». C'est le
     document sur lequel le défaut a été constaté, donc le seul qui prouve
     vraiment que c'est corrigé. Sauté proprement (jamais en échec) si le
     fichier n'est pas là -- il vit hors du dépôt.

    python tests/test_layout.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import layout  # noqa: E402

failures: list[str] = []

# Le PDF réel qui a motivé tout ce module (hors dépôt : un article sous droits).
REAL_ARTICLE = Path(r"C:\DEV\TRANSLAX\DRAFTS\PDF\Relation between Intelligence and religiosity.pdf")


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK   {label}")
    else:
        print(f"  ECHEC {label} {detail}")
        failures.append(label)


def _pymupdf():
    try:
        import pymupdf
    except ImportError:  # PyMuPDF antérieur à 1.24
        import fitz as pymupdf
    return pymupdf


COLUMN_LEFT = [
    "The first column opens here and keeps going down",
    "the page for several lines in a row, exactly as a",
    "real article in two columns would be typeset by a",
    "publisher preparing a journal issue for printing.",
    "It ends with a sentence that stops mid-thought and",
]
COLUMN_RIGHT = [
    "The second column is physically to the right of the",
    "first one, and a human reader only reaches it after",
    "finishing the whole left column above. Sorting text",
    "by vertical position alone interleaves the two, which",
    "is the defect this module exists to correct.",
]


def _build_two_column_pdf(path: Path, heading: str | None = None) -> None:
    """Un vrai PDF à deux colonnes, écrit avec PyMuPDF."""
    pymupdf = _pymupdf()
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    top = 80.0
    if heading:
        # Titre pleine largeur AU-DESSUS des deux colonnes.
        page.insert_text((60, 60), heading, fontsize=16)
        top = 100.0
    for index, line in enumerate(COLUMN_LEFT):
        page.insert_text((60, top + index * 14), line, fontsize=9)
    for index, line in enumerate(COLUMN_RIGHT):
        page.insert_text((330, top + index * 14), line, fontsize=9)
    doc.save(path)
    doc.close()


TABLE_HEADER = ["Study", "Total n", "Measure", "Effect"]
TABLE_ROWS = [
    ["Bender (1968)", "96", "UEE and GPA", "-.10"],
    ["Carlson (1934)", "100", "UEE", "-.19"],
    ["Corey (1940)", "234", "UEE", "-.03"],
    ["Crossman (2001)", "75", "Free recall", "-.13"],
]


def _build_rotated_table_pdf(path: Path) -> None:
    """
    Un vrai tableau imprimé en PAYSAGE sur une page portrait -- le cas
    exact des pages 5 à 7 de l'article de référence, que le tri par position
    réduisait à une bouillie de fragments.
    """
    pymupdf = _pymupdf()
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    # rotate=90 : le texte monte le long de la page. Les LIGNES du tableau se
    # succèdent alors vers la droite (axe x), les COLONNES vers le haut.
    column_y = [700.0, 520.0, 430.0, 240.0]
    for row_index, row in enumerate([TABLE_HEADER] + TABLE_ROWS):
        x = 120.0 + row_index * 22.0
        for cell, y in zip(row, column_y):
            page.insert_text((x, y), cell, fontsize=9, rotate=90)
    doc.save(path)
    doc.close()


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="translax_layout_"))
    try:
        print("\n1. Ordre de lecture sur un vrai PDF à deux colonnes")
        two_col = workdir / "two_columns.pdf"
        _build_two_column_pdf(two_col)
        document = layout.analyze_pdf(two_col)
        body = document.body_text()

        check("les deux colonnes sont détectées", document.report.columns_per_page == [2],
              f"({document.report.columns_per_page})")
        first_left = body.find(COLUMN_LEFT[0])
        last_left = body.find(COLUMN_LEFT[-1])
        first_right = body.find(COLUMN_RIGHT[0])
        check("toute la colonne de gauche est présente", first_left >= 0 and last_left >= 0)
        check("toute la colonne de droite est présente", first_right >= 0)
        check("la colonne de GAUCHE est lue entièrement AVANT la droite",
              last_left < first_right, f"(fin gauche={last_left}, début droite={first_right})")
        # Le vrai symptôme d'origine : une ligne de gauche collée à une ligne
        # de droite dans la même phrase.
        check("aucune ligne de gauche n'est collée à une ligne de droite",
              "down" not in body.split(COLUMN_RIGHT[0])[0][-40:].lower()
              or last_left < first_right)

        print("\n2. Un titre pleine largeur est lu AVANT les colonnes qu'il annonce")
        titled = workdir / "with_heading.pdf"
        _build_two_column_pdf(titled, heading="Method and Materials")
        body_titled = layout.analyze_pdf(titled).body_text()
        check("le titre est présent", "Method and Materials" in body_titled)
        check("le titre précède les deux colonnes",
              body_titled.find("Method and Materials") < body_titled.find(COLUMN_LEFT[0])
              and body_titled.find("Method and Materials") < body_titled.find(COLUMN_RIGHT[0]))

        print("\n3. Tableau paysage : reconstruit, étiqueté, sorti du corps du texte")
        table_pdf = workdir / "rotated_table.pdf"
        _build_rotated_table_pdf(table_pdf)
        table_doc = layout.analyze_pdf(table_pdf)
        tables = [r for r in table_doc.regions if r.kind == "table"]
        check("un tableau est détecté", len(tables) == 1, f"({len(tables)} trouvé(s))")
        if tables:
            text = tables[0].text
            check("chaque ligne du tableau porte l'étiquette de sa colonne",
                  "Study : Bender (1968)" in text and "Total n : 96" in text, f"({text[:120]!r})")
            check("la dernière colonne est étiquetée elle aussi (pas d'orpheline)",
                  "Effect : -.10" in text, f"({text[:200]!r})")
            check("les 4 lignes de données sont là, une par ligne",
                  all(f"Study : {r[0]}" in text for r in TABLE_ROWS))
            check("l'en-tête n'est pas répété comme une ligne de données",
                  "Study : Study" not in text)
        check("le tableau ne reste PAS dans le corps du texte",
              "Bender (1968)" not in table_doc.body_text())
        check("le tableau est bien rangé parmi les régions différées",
              any(r.kind == "table" for r in table_doc.deferred_regions()))

        print("\n4. Un document ordinaire (une colonne) ne déclenche rien")
        plain = workdir / "plain.pdf"
        pymupdf = _pymupdf()
        doc = pymupdf.open()
        page = doc.new_page(width=612, height=792)
        for index in range(12):
            page.insert_text((72, 90 + index * 16),
                             "A single column of ordinary prose running down the page.", fontsize=11)
        doc.save(plain)
        doc.close()
        plain_doc = layout.analyze_pdf(plain)
        check("une seule colonne détectée", plain_doc.report.columns_per_page == [1],
              f"({plain_doc.report.columns_per_page})")
        check("aucun tableau inventé", plain_doc.report.tables_found == 0)
        check("`is_layout_useful` dit bien de garder l'extraction classique",
              not layout.is_layout_useful(plain_doc))

        print("\n5. Le VRAI article de l'utilisateur (le cas qui a motivé ce module)")
        if not REAL_ARTICLE.exists():
            print(f"  SAUTÉ -- {REAL_ARTICLE.name} absent de cette machine "
                  "(fichier hors dépôt) ; les sections 1 à 4 couvrent la mécanique.")
        else:
            real = layout.analyze_pdf(REAL_ARTICLE)
            report = real.report
            check("les 30 pages sont vues comme étant à deux colonnes",
                  report.multi_column_pages == report.total_pages == 30,
                  f"({report.multi_column_pages}/{report.total_pages})")
            check("les tableaux de l'article sont détectés (au moins 8)",
                  report.tables_found >= 8, f"({report.tables_found})")
            labels = " | ".join(r.label for r in real.regions if r.kind == "table")
            check("le Tableau 1 (paysage, 3 pages) est reconnu avec sa légende",
                  "Overview of Studies Included in the Meta-Analysis" in labels)
            check("le Tableau 2 (imprimé normalement, pleine largeur) est reconnu aussi",
                  "Correlations Between Religiosity and Intelligence" in labels, f"({labels[:150]})")

            body = real.body_text()
            pages = body.split("\f")
            check("la page 4 se termine bien sur « Only one coding » (phrase inachevée)",
                  pages[3].rstrip().endswith("Only one coding"), f"({pages[3][-60:]!r})")
            check("la page 8 reprend bien sur « variable (goal of study) »",
                  "variable (goal of study) involved a subjective judgment" in pages[7])
            check("le corps du texte ne contient plus de lignes de tableau",
                  "Bertsch and Pesta (2009) ; Total n" not in body)
            check("les colonnes ne sont plus entrelacées "
                  "(le défaut d'origine a bien disparu)",
                  "Christians scorn university entrance exams" not in body)
            check("les notes répétées à l'identique ne sont gardées qu'une fois",
                  report.duplicate_notes_removed > 0, f"({report.duplicate_notes_removed})")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    if failures:
        print(f"{len(failures)} test(s) en échec : " + ", ".join(failures))
        return 1
    print("Tous les tests de mise en page passent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
