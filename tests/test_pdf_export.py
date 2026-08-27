"""
Tests de `core/pdf_export.py` -- export d'un Markdown déjà traduit par
TRANSLAX vers un PDF propre (demande explicite de l'utilisateur, 25/08/2026) :
texte noir sur blanc, une organisation réelle (titres/paragraphes mis en
forme), jamais les codes Markdown bruts ("#", "##", "-", ">") visibles.

    python tests/test_pdf_export.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import pdf_export, translate  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK   {label}")
    else:
        print(f"  ECHEC {label} {detail}")
        failures.append(label)


def main() -> int:
    print("\n1. markdown_to_html_body -- l'inverse exact de render_markdown")
    html_title = pdf_export.markdown_to_html_body("# Mon Titre")
    check("titre -> <h1>", html_title == "<h1>Mon Titre</h1>", f"({html_title!r})")

    html_heading = pdf_export.markdown_to_html_body("## Un sous-titre")
    check("sous-titre -> <h2>", html_heading == "<h2>Un sous-titre</h2>", f"({html_heading!r})")

    html_bullet = pdf_export.markdown_to_html_body("- Un point de liste")
    check("puce -> <p class=\"bullet\"> avec le symbole •",
          html_bullet == '<p class="bullet">• Un point de liste</p>', f"({html_bullet!r})")

    html_para = pdf_export.markdown_to_html_body("Un paragraphe ordinaire.")
    check("paragraphe ordinaire -> <p>", html_para == "<p>Un paragraphe ordinaire.</p>", f"({html_para!r})")

    html_quote = pdf_export.markdown_to_html_body("> Première ligne\n> Deuxième ligne")
    check("citation multi-lignes -> un seul <blockquote> englobant",
          html_quote == "<blockquote>\n<p>Première ligne</p>\n<p>Deuxième ligne</p>\n</blockquote>",
          f"({html_quote!r})")

    html_escaped = pdf_export.markdown_to_html_body("Un texte avec <balise> & \"guillemets\".")
    check("caractères HTML spéciaux échappés (jamais interprétés comme du HTML)",
          "&lt;balise&gt;" in html_escaped and "&amp;" in html_escaped, f"({html_escaped!r})")

    print("\n2. Rendu PDF réel, avec les quatre types de bloc à la suite")
    md_content = "\n".join([
        translate.render_markdown("title", "Titre du Document de Test"),
        translate.render_markdown("heading", "Premier Chapitre"),
        translate.render_markdown(
            "paragraph",
            "Ceci est un paragraphe de test suffisamment long pour vérifier le rendu "
            "visuel du texte justifié dans le PDF généré par ce module.",
        ),
        translate.render_markdown("bullet", "Premier point"),
        translate.render_markdown("bullet", "Second point"),
        translate.render_markdown("restricted", "Texte original conservé tel quel."),
    ])

    workdir = Path(tempfile.mkdtemp(prefix="translax_pdf_export_"))
    try:
        pdf_path = workdir / "export.pdf"
        pdf_export.markdown_to_pdf(md_content, pdf_path)
        check("le fichier PDF a bien été créé", pdf_path.exists())

        import pymupdf
        doc = pymupdf.open(str(pdf_path))
        check("au moins une page produite", doc.page_count >= 1, f"({doc.page_count})")
        full_text = "\n".join(page.get_text() for page in doc)
        doc.close()

        check("le vrai contenu (titre) est présent dans le PDF rendu",
              "Titre du Document de Test" in full_text)
        check("le vrai contenu (paragraphe) est présent",
              "paragraphe de test" in full_text)
        check("le vrai contenu (puces) est présent",
              "Premier point" in full_text and "Second point" in full_text)
        check("le vrai contenu (citation) est présent",
              "Texte original conservé" in full_text)
        # Le cœur de la demande : jamais les symboles Markdown bruts dans
        # le résultat final -- vérifié ligne par ligne, pas juste "absent
        # quelque part" (un "-" ou un "#" isolé dans un vrai mot ne doit
        # pas faire échouer ce test à tort).
        raw_lines = [line for line in full_text.split("\n") if line.strip()]
        check("aucune ligne ne commence par '#' ou '##' (code Markdown brut)",
              not any(line.strip().startswith("#") for line in raw_lines), f"({raw_lines})")
        check("aucune ligne ne commence par '- ' (code Markdown brut, pas juste un tiret dans le texte)",
              not any(line.strip().startswith("- ") for line in raw_lines), f"({raw_lines})")

        print("\n3. markdown_file_to_pdf -- à partir d'un vrai fichier .md sur disque")
        md_path = workdir / "source.md"
        md_path.write_text(md_content, encoding="utf-8")
        pdf_path2 = workdir / "from_file.pdf"
        pdf_export.markdown_file_to_pdf(md_path, pdf_path2)
        check("PDF créé à partir du fichier", pdf_path2.exists())

        print("\n4. Document vide -- ne plante pas")
        empty_pdf = workdir / "empty.pdf"
        pdf_export.markdown_to_pdf("", empty_pdf)
        check("un contenu vide produit quand même un PDF valide (pas de plantage)", empty_pdf.exists())

    finally:
        import shutil
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    if failures:
        print(f"{len(failures)} test(s) en échec : " + ", ".join(failures))
        return 1
    print("Tous les tests d'export PDF passent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
