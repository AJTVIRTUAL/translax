"""
Extraction du texte brut à partir d'un PDF, d'un EPUB, d'un TXT ou d'un MD.

Remplace l'appel externe `pdftotext -enc UTF-8 -layout` du pipeline
d'origine par PyMuPDF (fitz), dont le binaire est embarqué dans le paquet
pip : aucune dépendance native à installer sur la machine.

L'extraction PDF conserve volontairement les sauts de page sous forme de
caractères de saut de page (\f), exactement comme le faisait pdftotext :
`segment.py` les traite déjà comme des séparateurs de blocs.

EPUB : PyMuPDF ouvre un `.epub` directement (vérifié -- MuPDF le traite
en interne comme un document paginé, une « page » par fichier XHTML du
spine, dans l'ordre de lecture du livre). Même fonction d'extraction que
le PDF, aucun code séparé : `\f` entre chaque chapitre, comme entre deux
pages de PDF -- `page_cleanup.py` peut donc aussi s'appliquer si un livre
numérique répète un même bandeau à chaque chapitre, sans rien coder de
plus pour ça. Une différence structurelle à connaître : un EPUB n'a pas de
mise en page fixe (texte reflowable), donc les paragraphes HTML (`<p>`)
n'y sont pas toujours séparés par une ligne vide comme le serait un PDF
bien extrait -- `segment.detect_strategy` bascule alors naturellement en
stratégie « flux » (reconstruction par phrases), exactement le cas déjà
prévu pour les exports ebook en `.txt`.

Limitation connue (documentée, pas contournée) : un PDF dont le contenu est
disposé en colonnes ou en tableau voit ses colonnes mélangées sur une même
ligne logique. Le tri par position atténue le problème sans le résoudre.
"""
from __future__ import annotations

from pathlib import Path

SUPPORTED_SUFFIXES = {".pdf", ".epub", ".txt", ".md", ".markdown"}
_PAGED_SUFFIXES = {".pdf", ".epub"}  # ouverts via PyMuPDF, découpage en \f entre pages/chapitres


class UnsupportedFormat(ValueError):
    """Le fichier fourni n'est ni un PDF, ni un EPUB, ni un TXT, ni un MD."""


def is_supported(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_SUFFIXES


def extract_text(path: Path, on_progress=None) -> str:
    """
    Retourne le texte brut du fichier.

    `on_progress(page_courante, total_pages)` est appelé pendant
    l'extraction d'un PDF/EPUB (les fichiers texte sont lus d'un bloc).
    """
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix in _PAGED_SUFFIXES:
        return _extract_paged(path, on_progress)
    if suffix in {".txt", ".md", ".markdown"}:
        return _read_text_file(path)
    raise UnsupportedFormat(
        f"Format non pris en charge : {path.suffix or '(aucune extension)'}. "
        f"Formats acceptés : {', '.join(sorted(SUPPORTED_SUFFIXES))}"
    )


def _extract_paged(path: Path, on_progress=None) -> str:
    """PDF ou EPUB : PyMuPDF ouvre les deux formats de façon identique, une
    « page » (PDF) ou un chapitre (EPUB) à la fois."""
    # PyMuPDF >= 1.24 s'importe sous le nom `pymupdf` ; `fitz` reste le nom
    # historique et est déprécié (il émet un avertissement en 1.28).
    try:
        import pymupdf
    except ImportError:  # PyMuPDF antérieur à 1.24
        import fitz as pymupdf

    pages: list[str] = []
    with pymupdf.open(path) as doc:
        total = doc.page_count
        for number, page in enumerate(doc, start=1):
            # sort=True : ordre de lecture par position verticale, ce qui se
            # rapproche le plus du rendu `pdftotext -layout` sur lequel les
            # heuristiques de segmentation ont été calibrées.
            pages.append(page.get_text("text", sort=True))
            if on_progress is not None:
                on_progress(number, total)
    return "\f".join(pages)


def _read_text_file(path: Path) -> str:
    """
    Lecture d'un .txt/.md en UTF-8, avec repli sur cp1252 puis latin-1.

    Les fichiers issus d'exports ebook/Windows ne sont pas toujours en UTF-8 ;
    on ne veut pas planter, ni remplacer silencieusement des caractères par
    des « ? » quand un autre encodage lit le fichier proprement.
    """
    data = path.read_bytes()
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")
