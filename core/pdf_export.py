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
import re
from pathlib import Path

_HEADING1_RE = re.compile(r"^#\s+(.*)$")
_HEADING2_RE = re.compile(r"^##\s+(.*)$")
_BULLET_RE = re.compile(r"^-\s+(.*)$")
_QUOTE_RE = re.compile(r"^>\s?(.*)$")

# Noir sur blanc explicite (demande explicite de l'utilisateur) : jamais
# hérité d'un thème sombre ou d'un réglage système, ce PDF est fait pour
# être imprimé/lu comme un document classique.
CSS = """
body { font-family: Helvetica, sans-serif; font-size: 11pt; color: #000000; background-color: #ffffff; line-height: 1.4; }
h1 { font-size: 20pt; font-weight: bold; margin-top: 0pt; margin-bottom: 14pt; }
h2 { font-size: 14pt; font-weight: bold; margin-top: 16pt; margin-bottom: 8pt; }
p { margin: 0pt 0pt 8pt 0pt; text-align: justify; }
p.bullet { margin-left: 16pt; }
blockquote { border-left: 2pt solid #999999; padding-left: 10pt; color: #444444; margin: 8pt 0pt; }
"""


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
    en PDF propre à `pdf_path`. A4, marges de 0,5 pouce, pagination
    automatique gérée par `pymupdf.Story` -- aussi long que nécessaire,
    pas de limite de pages codée en dur.
    """
    import pymupdf

    body_html = markdown_to_html_body(markdown_text)
    story = pymupdf.Story(html=f"<body>{body_html}</body>", user_css=CSS)

    pdf_path = Path(pdf_path)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    writer = pymupdf.DocumentWriter(str(pdf_path))
    mediabox = pymupdf.paper_rect("a4")
    where = mediabox + (36, 36, -36, -36)  # marges de 0,5 pouce (36 points)
    more = 1
    try:
        while more:
            device = writer.begin_page(mediabox)
            more, _ = story.place(where)
            story.draw(device)
            writer.end_page()
    finally:
        writer.close()


def markdown_file_to_pdf(md_path: Path, pdf_path: Path) -> None:
    """Convertit un fichier .md déjà écrit sur disque en PDF -- voir
    `markdown_to_pdf` pour le détail du rendu."""
    content = Path(md_path).read_text(encoding="utf-8")
    markdown_to_pdf(content, pdf_path)
