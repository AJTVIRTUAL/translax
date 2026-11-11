"""
Export d'un Markdown déjà traduit par TRANSLAX vers un PDF propre --
demande explicite de l'utilisateur : texte noir sur blanc, une organisation
réelle (titres/paragraphes mis en forme), jamais les codes Markdown bruts
("#", "##", "-", ">") visibles dans le résultat final.

Le .md reste le format de sortie PAR DÉFAUT et le seul utilisé pour la
reprise/l'état (voir core/state.py, core/pipeline.py) -- le PDF est
généré en PLUS, à partir du .md déjà écrit, jamais à la place. Choix
délibéré : toute la mécanique de reprise déjà validée (écriture
incrémentale, cache, pointeur de nom traduit) continue de fonctionner
exactement pareil, sans le moindre risque d'y toucher pour cette
fonctionnalité.

`markdown_to_html_body` est l'inverse EXACT de
`core/translate.py::render_markdown` -- pas un analyseur Markdown général :
TRANSLAX ne produit jamais que ces quatre formes de ligne (titre, sous-titre,
puce, citation) plus des paragraphes ordinaires, donc pas besoin d'un vrai
analyseur Markdown pour les reconnaître fidèlement.

Rendu via `pymupdf.Story` (HTML -> mise en page -> PDF), déjà une
dépendance du projet -- aucune bibliothèque supplémentaire nécessaire.
Vérifié réellement avant d'écrire ce module (pas supposé) : rendu réel
d'un document de test avec les quatre types de bloc, PDF ouvert et
capturé en image pour inspection visuelle -- titres en gras à la bonne
taille, paragraphes justifiés, puces indentées, citation visuellement
distincte, tout en noir sur fond blanc.
"""
from __future__ import annotations

import html
import io
import re
from pathlib import Path

_HEADING1_RE = re.compile(r"^#\s+(.*)$")
_HEADING2_RE = re.compile(r"^##\s+(.*)$")
_BULLET_RE = re.compile(r"^-\s+(.*)$")
_QUOTE_RE = re.compile(r"^>\s?(.*)$")

# Géométrie de la page -- partagée avec `core/paragraphs.py`, qui mesure les
# « 6 lignes max par paragraphe » sur CETTE colonne et CETTE taille de texte
# (une ligne du .md = une vraie ligne de ce PDF). Texte agrandi le 10/11/2026
# (demande explicite de l'utilisateur, « rends les textes plus gros ») :
# 11 pt -> 14 pt, interligne et marges élargis en proportion.
# `body { margin: 0 }` est indispensable : sans lui, le moteur HTML de MuPDF
# ajoute 1 em de marge par défaut de chaque côté (vérifié : colonne réelle de
# 459 pt au lieu de 487) et la mesure des lignes serait fausse.
PAGE_WIDTH_PT = 595.0          # A4 (pymupdf.paper_rect("a4"))
PAGE_MARGIN_PT = 54.0          # 0,75 pouce (1,9 cm)
BODY_FONT_SIZE = 14.0
TEXT_WIDTH_PT = PAGE_WIDTH_PT - 2 * PAGE_MARGIN_PT

# Noir sur blanc explicite (demande explicite de l'utilisateur) : jamais
# hérité d'un thème sombre ou d'un réglage système, ce PDF est fait pour
# être imprimé/lu comme un document classique.
CSS = f"""
body {{ margin: 0pt; padding: 0pt; font-family: Helvetica, sans-serif; font-size: {BODY_FONT_SIZE:g}pt; color: #000000; background-color: #ffffff; line-height: 1.5; }}
h1 {{ font-size: 26pt; font-weight: bold; margin-top: 0pt; margin-bottom: 20pt; line-height: 1.25; }}
h2 {{ font-size: 18pt; font-weight: bold; margin-top: 22pt; margin-bottom: 10pt; line-height: 1.3; }}
p {{ margin: 0pt 0pt 12pt 0pt; text-align: justify; }}
p.bullet {{ margin-left: 18pt; }}
blockquote {{ border-left: 2pt solid #999999; padding-left: 12pt; color: #444444; margin: 10pt 0pt; }}
"""

# Numéro de page, centré en bas, discret (gris, petit) -- ajouté après coup
# sur chaque page déjà rendue, pour ne pas toucher à la mise en page du texte.
_PAGE_NUMBER_SIZE = 9.0
_PAGE_NUMBER_COLOR = (0.45, 0.45, 0.45)


def markdown_to_html_body(markdown_text: str) -> str:
    """
    Convertit ligne par ligne le Markdown produit par `render_markdown` en
    un corps HTML simple. Une ligne qui ne correspond à aucun des quatre
    motifs connus (titre/sous-titre/puce/citation) est traitée comme un
    paragraphe ordinaire -- jamais de ligne perdue silencieusement.
    """
    lines_html: list[str] = []
    in_quote = False

    def close_quote() -> None:
        nonlocal in_quote
        if in_quote:
            lines_html.append("</blockquote>")
            in_quote = False

    for raw_line in markdown_text.split("\n"):
        line = raw_line.rstrip()
        if not line:
            close_quote()
            continue
        if m := _HEADING1_RE.match(line):
            close_quote()
            lines_html.append(f"<h1>{html.escape(m.group(1))}</h1>")
        elif m := _HEADING2_RE.match(line):
            close_quote()
            lines_html.append(f"<h2>{html.escape(m.group(1))}</h2>")
        elif m := _BULLET_RE.match(line):
            close_quote()
            lines_html.append(f"<p class=\"bullet\">• {html.escape(m.group(1))}</p>")
        elif m := _QUOTE_RE.match(line):
            if not in_quote:
                lines_html.append("<blockquote>")
                in_quote = True
            lines_html.append(f"<p>{html.escape(m.group(1))}</p>")
        else:
            close_quote()
            lines_html.append(f"<p>{html.escape(line)}</p>")
    close_quote()
    return "\n".join(lines_html)


def markdown_to_pdf(markdown_text: str, pdf_path: Path) -> None:
    """
    Rend `markdown_text` (le contenu d'un .md déjà traduit par TRANSLAX)
    en PDF propre à `pdf_path`. A4, marges de 0,75 pouce, pagination
    automatique gérée par `pymupdf.Story` -- aussi long que nécessaire,
    pas de limite de pages codée en dur -- puis numéros de page en bas.
    """
    import pymupdf

    body_html = markdown_to_html_body(markdown_text)
    story = pymupdf.Story(html=f"<body>{body_html}</body>", user_css=CSS)

    pdf_path = Path(pdf_path)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    # Rendu en mémoire d'abord : les numéros de page ne peuvent être posés
    # qu'une fois le nombre total de pages connu.
    stream = io.BytesIO()
    writer = pymupdf.DocumentWriter(stream)
    mediabox = pymupdf.paper_rect("a4")
    m = PAGE_MARGIN_PT
    where = mediabox + (m, m, -m, -m)
    more = 1
    try:
        while more:
            device = writer.begin_page(mediabox)
            more, _ = story.place(where)
            story.draw(device)
            writer.end_page()
    finally:
        writer.close()

    doc = pymupdf.open("pdf", stream.getvalue())
    try:
        _add_page_numbers(doc)
        title = _first_title(markdown_text)
        if title:
            doc.set_metadata({"title": title, "creator": "TRANSLAX"})
        doc.save(str(pdf_path), garbage=3, deflate=True)
    finally:
        doc.close()


def _add_page_numbers(doc) -> None:
    """« 3 / 120 », centré en bas de chaque page, dans la marge -- jamais
    sur la première page seule d'un document d'une page (inutile)."""
    import pymupdf

    total = doc.page_count
    if total < 2:
        return
    for index, page in enumerate(doc, start=1):
        rect = page.rect
        box = pymupdf.Rect(0, rect.height - PAGE_MARGIN_PT * 0.75, rect.width, rect.height - PAGE_MARGIN_PT * 0.25)
        page.insert_textbox(
            box,
            f"{index} / {total}",
            fontsize=_PAGE_NUMBER_SIZE,
            fontname="helv",
            color=_PAGE_NUMBER_COLOR,
            align=pymupdf.TEXT_ALIGN_CENTER,
        )


def _first_title(markdown_text: str) -> str | None:
    """Premier titre « # … » du document, pour les métadonnées du PDF."""
    for line in markdown_text.split("\n"):
        if m := _HEADING1_RE.match(line.rstrip()):
            return m.group(1).strip() or None
    return None


def markdown_file_to_pdf(md_path: Path, pdf_path: Path) -> None:
    """Convertit un fichier .md déjà écrit sur disque en PDF -- voir
    `markdown_to_pdf` pour le détail du rendu."""
    content = Path(md_path).read_text(encoding="utf-8")
    markdown_to_pdf(content, pdf_path)
