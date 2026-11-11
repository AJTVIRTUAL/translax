"""
Aération des paragraphes trop longs, à la toute fin d'une traduction --
demande explicite de l'utilisateur (10/11/2026) : « sépare chaque
paragraphe, 6 lignes maximum par paragraphe », pour le .md comme pour le PDF.

Logique reprise de substans.app (`utils/rehypeShortParagraphs.ts`) :

  - on ne coupe JAMAIS au milieu d'une phrase : seulement après une fin de
    phrase (. ! ? … et les guillemets/parenthèses fermants qui la suivent,
    espace typographique française comprise : « il ? » »), devant une phrase
    qui commence vraiment (majuscule, chiffre, guillemet ou tiret ouvrant) ;
  - une abréviation (« M. », « Dr. », « J. », « U.S. ») ne termine pas une
    phrase ;
  - rééquilibrage : mieux vaut 4 + 4 lignes que 6 + 2 (programmation
    dynamique sur les fins de phrase, coût = écart au carré à la taille
    idéale) ;
  - différence avec substans, pour que la limite soit vraiment tenue : une
    phrase SEULE plus longue que la limite (rare : listes sans ponctuation,
    phrases à rallonge) est coupée après un « ; », « : » ou « , », et en
    tout dernier recours entre deux mots -- jamais ailleurs que dans ce cas.

Différence voulue avec substans : là-bas le budget est en CARACTÈRES
(lecture sur téléphone, largeur d'écran variable). Ici le document final a
une géométrie connue -- celle du PDF exporté (`core/pdf_export.py`) -- donc
une « ligne » est une VRAIE ligne de ce PDF, mesurée avec les métriques
réelles de la police (Helvetica, la même que le PDF). Le .md est découpé avec
cette même mesure : .md et PDF ont exactement les mêmes paragraphes.

Ne touche qu'aux paragraphes ordinaires : titres (#, ##), puces (-),
citations (>) et blocs restreints (jamais traduits) restent tels quels.
Aucun mot n'est ajouté, retiré ni modifié -- seuls des sauts de paragraphe
sont insérés entre deux phrases existantes.
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MAX_LINES = 6

# Mesure prise sur 97 % de la largeur réelle : le moteur de mise en page du
# PDF et cette simulation peuvent différer d'un cheveu (crénage, espaces
# justifiés…). Avec cette marge, l'erreur va toujours dans le sens « une
# ligne de trop estimée » -- donc un paragraphe un peu plus court que
# nécessaire --, jamais dans celui d'un paragraphe de 7 lignes dans le PDF.
_WIDTH_SAFETY = 0.97

# Repère du bloc jamais traduit (voir core/segment.py) -- dupliqué ici
# plutôt qu'importé pour garder ce module sans dépendance interne lourde.
_RESTRICTED_PREFIX = "⛔ TRANSLAX"

_CLOSERS = "\"'’”»)]」』）"
_OPENERS = "«\"“‘'([—–-¿¡„‚「『（"
# Fin de phrase en fin de chaîne : ponctuation finale, puis éventuellement des
# fermants (avec ou sans espace avant -- « Viens. » »).
_SENTENCE_END_RE = re.compile(r"[.!?…؟](?:\s*[\"'’”»)\]」』）])*$")
_CLOSERS_TAIL_RE = re.compile(r"(?:\s*[\"'’”»)\]」』）])+$")
# Ponctuation finale CJK : suivie directement de la phrase suivante, sans espace.
_CJK_END_RE = re.compile(r"[。！？](?:[」』”’）)\]])*")
_WHITESPACE_RE = re.compile(r"\s+")

# Abréviations qui, suivies d'un point, ne terminent pas une phrase (en plus
# de la règle générale « mot de 1-2 lettres en majuscule » et des mots à
# points internes comme « U.S » ou « J.-C »). En minuscules, sans le point.
_ABBREVIATIONS = frozenset({
    # titres et civilités
    "mr", "mrs", "ms", "mme", "mmes", "mlle", "mlles", "mgr", "messrs", "dr", "drs",
    "prof", "pr", "sr", "jr", "st", "ste", "rev", "gen", "gén", "col", "capt",
    "cap", "lt", "sgt", "cdt", "hon", "sra", "srta", "hr", "fr", "frau",
    # références
    "vol", "chap", "ch", "fig", "art", "no", "nº", "p", "pp", "cf", "ed", "éd",
    "eds", "op", "cit", "ibid", "vs", "env", "av", "apr", "approx", "dept",
    "réf", "ref", "suiv", "sq", "sqq",
})

_CJK_RANGES = (
    "⺀-⿿　-〿぀-ヿ㄀-ㇿ㈀-鿿"
    "가-힯豈-﫿＀-￯"
)
# Un « mot » insécable pour la mise en page : un idéogramme seul (le CJK se
# coupe entre deux caractères quelconques), ou une suite sans espace ni CJK.
_TOKEN_RE = re.compile(rf"\s+|[{_CJK_RANGES}]|[^\s{_CJK_RANGES}]+")


# --------------------------------------------------------------------------
# Mesure des lignes
# --------------------------------------------------------------------------

def _load_font():
    """Police Helvetica intégrée à PyMuPDF (la même que celle du PDF) et
    l'ensemble des caractères qu'elle couvre -- (None, vide) si PyMuPDF
    n'est pas disponible : la mesure retombe alors sur une estimation."""
    try:
        import pymupdf
        font = pymupdf.Font("helv")
        return font, frozenset(font.valid_codepoints())
    except Exception:  # noqa: BLE001 - une estimation vaut mieux qu'un plantage
        return None, frozenset()


class LineMeasure:
    """
    Compte les lignes qu'occupe un texte dans une colonne de largeur donnée,
    en simulant le retour à la ligne « glouton » d'un moteur de mise en page
    (celui de MuPDF, utilisé pour le PDF, en est un) : on remplit chaque
    ligne mot par mot, et un mot qui ne tient plus passe à la ligne suivante.
    """

    def __init__(self, width_pt: float, font_size: float):
        self.width = width_pt * _WIDTH_SAFETY
        self.font_size = font_size
        self._font, self._covered = _load_font()
        self._cache: dict[str, float] = {}
        self._space = self._token_width(" ")

    def _char_width(self, char: str) -> float:
        if self._font is not None and ord(char) in self._covered:
            return self._font.text_length(char, fontsize=self.font_size)
        # Caractère absent d'Helvetica (arabe, idéogrammes…) : le PDF le
        # dessine avec une police de secours. Estimation volontairement large.
        if unicodedata.east_asian_width(char) in ("W", "F"):
            return self.font_size
        return self.font_size * 0.6

    def _token_width(self, token: str) -> float:
        width = self._cache.get(token)
        if width is None:
            if self._font is not None and all(ord(c) in self._covered for c in token):
                width = self._font.text_length(token, fontsize=self.font_size)
            else:
                width = sum(self._char_width(c) for c in token)
            self._cache[token] = width
        return width

    def count_lines(self, text: str) -> int:
        lines = 0
        used = 0.0
        pending_space = False
        for match in _TOKEN_RE.finditer(text):
            token = match.group()
            if token.isspace():
                pending_space = True
                continue
            width = self._token_width(token)
            gap = self._space if pending_space else 0.0
            pending_space = False
            if lines == 0:
                lines, used = 1, width
            elif used + gap + width <= self.width:
                used += gap + width
                continue
            else:
                lines += 1
                used = width
            # Un « mot » plus large que la colonne entière (longue URL…)
            # déborde sur plusieurs lignes à lui seul.
            if width > self.width:
                extra = math.ceil(width / self.width) - 1
                lines += extra
                used = width - extra * self.width
        return lines


_PDF_MEASURE: LineMeasure | None = None


def pdf_measure() -> LineMeasure:
    """La mesure de référence : une ligne de corps de texte du PDF exporté
    (largeur de colonne et taille de police réelles de `core/pdf_export.py`).
    Construite une seule fois, puis réutilisée (cache des largeurs de mots)."""
    global _PDF_MEASURE
    if _PDF_MEASURE is None:
        from . import pdf_export
        _PDF_MEASURE = LineMeasure(pdf_export.TEXT_WIDTH_PT, pdf_export.BODY_FONT_SIZE)
    return _PDF_MEASURE


# --------------------------------------------------------------------------
# Fins de phrase
# --------------------------------------------------------------------------

def _is_sentence_start(char: str) -> bool:
    """Majuscule, chiffre, ouvrant -- ou lettre d'une écriture sans casse
    (arabe, chinois…), où la notion de majuscule n'existe pas."""
    return char.isdigit() or char in _OPENERS or (char.isalpha() and not char.islower())


def _looks_like_abbreviation(before: str) -> bool:
    """`before` se termine par un point (et d'éventuels fermants) : ce point
    est-il celui d'une abréviation (« M. », « Dr. », « U.S. ») plutôt
    qu'une vraie fin de phrase ?"""
    stripped = _CLOSERS_TAIL_RE.sub("", before)
    if not stripped.endswith(".") or stripped.endswith(".."):
        return False  # « ! », « ? », « … » ou points de suspension : vraie fin
    words = stripped[:-1].split()
    if not words:
        return False
    word = words[-1].lstrip(_OPENERS)
    if not word:
        return False
    if "." in word:
        return True  # U.S. / e.g. / J.-C.
    if word.isdigit() and len(word) <= 2:
        return True  # énumération « … ci-dessus. 1. Ils fournissent » : le « 1. » reste avec sa suite
    if len(word) <= 2 and word[0].isupper():
        return True  # initiale (« J. »), « M. », « Dr. »
    return word.lower() in _ABBREVIATIONS


def sentence_boundaries(text: str) -> list[int]:
    """
    Positions (indices dans `text`) où commence une nouvelle phrase -- les
    seuls endroits où un paragraphe a le droit d'être coupé.
    """
    points: set[int] = set()
    length = len(text)
    for match in _WHITESPACE_RE.finditer(text):
        start, end = match.span()
        if start == 0 or end >= length:
            continue
        # Seule la fin compte : on borne pour rester linéaire même sur un
        # paragraphe démesuré (livre entier d'un seul bloc).
        before = text[max(0, start - 40):start]
        if not _SENTENCE_END_RE.search(before):
            continue
        if _looks_like_abbreviation(before):
            continue
        if not _is_sentence_start(text[end]):
            continue
        points.add(end)
    for match in _CJK_END_RE.finditer(text):
        end = match.end()
        if 0 < end < length and not text[end].isspace():
            points.add(end)
    return sorted(points)


# --------------------------------------------------------------------------
# Découpage
# --------------------------------------------------------------------------

# Coupures de repli, utilisées UNIQUEMENT à l'intérieur d'une phrase seule
# déjà plus longue que la limite (sinon elle resterait à 7, 8… 13 lignes --
# vérifié sur un vrai livre traduit : listes de fondations et montants sans
# un seul point, longues phrases à rallonge).
_WEAK_BREAK_RE = re.compile(r"[;:,](?=\s)")
_LEVEL_SENTENCE, _LEVEL_PUNCTUATION, _LEVEL_WORD = 0, 1, 2


def _weak_boundaries(text: str) -> list[int]:
    """Début du mot qui suit un « ; », « : » ou « , »."""
    points = []
    for match in _WEAK_BREAK_RE.finditer(text):
        end = match.end()
        while end < len(text) and text[end].isspace():
            end += 1
        if 0 < end < len(text):
            points.append(end)
    return points


def _word_boundaries(text: str) -> list[int]:
    """Début de chaque mot (dernier recours : une liste sans ponctuation)."""
    return [m.end() for m in _WHITESPACE_RE.finditer(text) if 0 < m.end() < len(text)]


def _balanced_split(text: str, points: list[int], max_lines: int, measure: LineMeasure) -> list[str]:
    """
    Choisit parmi `points` les coupures qui donnent des morceaux de
    `max_lines` lignes au plus, aussi égaux que possible -- mieux vaut 4 + 4
    lignes que 6 + 2. Programmation dynamique sur les points de coupure ; un
    morceau ne dépasse la limite que s'il est fait d'un seul segment
    insécable (il sera alors repris au niveau de coupure suivant).
    """
    total_lines = measure.count_lines(text)
    bounds = [0, *points, len(text)]
    count = len(bounds) - 1
    # Taille idéale d'un morceau, en caractères : le texte réparti également
    # sur le plus petit nombre de morceaux possible.
    target = len(text) / math.ceil(total_lines / max_lines)

    best = [math.inf] * (count + 1)
    prev = [-1] * (count + 1)
    best[0] = 0.0
    for i in range(1, count + 1):
        for j in range(i - 1, -1, -1):
            chunk = text[bounds[j]:bounds[i]].strip()
            # Au-delà d'un segment, tout morceau trop long est exclu -- et
            # tous ceux qui commenceraient plus tôt le seraient aussi.
            if i - j > 1 and measure.count_lines(chunk) > max_lines:
                break
            if best[j] == math.inf:
                continue
            cost = best[j] + (len(chunk) - target) ** 2
            if cost < best[i]:
                best[i] = cost
                prev[i] = j

    cuts: list[int] = []
    i = prev[count]
    while i > 0:
        cuts.append(bounds[i])
        i = prev[i]
    cuts.reverse()
    edges = [0, *cuts, len(text)]
    return [piece for a, b in zip(edges, edges[1:]) if (piece := text[a:b].strip())]


def _split(text: str, max_lines: int, measure: LineMeasure, level: int, report: "SplitReport | None") -> list[str]:
    if level > _LEVEL_WORD or measure.count_lines(text) <= max_lines:
        return [text]
    if level == _LEVEL_SENTENCE:
        points = sentence_boundaries(text)
    elif level == _LEVEL_PUNCTUATION:
        points = _weak_boundaries(text)
    else:
        points = _word_boundaries(text)
    pieces = _balanced_split(text, points, max_lines, measure) if points else [text]
    if report is not None and level > _LEVEL_SENTENCE and len(pieces) > 1:
        report.long_sentences_cut += 1
    out: list[str] = []
    for piece in pieces:
        out.extend(_split(piece, max_lines, measure, level + 1, report))
    return out


def split_paragraph(
    text: str,
    max_lines: int = DEFAULT_MAX_LINES,
    measure: LineMeasure | None = None,
    report: "SplitReport | None" = None,
) -> list[str]:
    """
    Découpe un paragraphe en morceaux de `max_lines` lignes au plus, en
    équilibrant les morceaux. Toujours aux fins de phrase en priorité ; une
    phrase seule plus longue que la limite est ensuite coupée après un
    « ; », « : » ou « , », et seulement en tout dernier recours entre deux
    mots. Renvoie `[text]` inchangé s'il tient déjà.
    """
    text = text.strip()
    if not text:
        return []
    if max_lines < 1:
        return [text]
    measure = measure or pdf_measure()
    return _split(text, max_lines, measure, _LEVEL_SENTENCE, report)


def _is_plain_paragraph(block: str) -> bool:
    """Un paragraphe ordinaire, tel qu'écrit par `translate.render_markdown` :
    une seule ligne, ni titre, ni puce, ni citation, ni bloc restreint."""
    return (
        "\n" not in block
        and not block.startswith(("#", "- ", ">", _RESTRICTED_PREFIX))
    )


@dataclass
class SplitReport:
    paragraphs_split: int = 0     # paragraphes trop longs effectivement découpés
    paragraphs_added: int = 0     # paragraphes supplémentaires créés par ces découpes
    long_sentences_cut: int = 0   # phrases seules > limite, coupées à une virgule (ou entre deux mots)
    max_lines: int = DEFAULT_MAX_LINES

    def summary(self) -> str:
        if not self.paragraphs_split:
            return f"Paragraphes : tous font déjà {self.max_lines} lignes ou moins."
        text = (
            f"Paragraphes aérés : {self.paragraphs_split} paragraphe(s) long(s) découpé(s) "
            f"en {self.paragraphs_split + self.paragraphs_added} ({self.max_lines} lignes max)."
        )
        if self.long_sentences_cut:
            text += (
                f" Dont {self.long_sentences_cut} phrase(s) de plus de {self.max_lines} lignes "
                "coupée(s) à une virgule ou un point-virgule."
            )
        return text


def split_markdown(
    content: str,
    max_lines: int = DEFAULT_MAX_LINES,
    measure: LineMeasure | None = None,
) -> tuple[str, SplitReport]:
    """
    Applique `split_paragraph` à chaque paragraphe ordinaire d'un Markdown
    produit par TRANSLAX ; titres, puces, citations et blocs restreints
    restent tels quels. Les nouveaux paragraphes sont séparés par une ligne
    vide, comme tous les autres blocs du fichier.
    """
    measure = measure or pdf_measure()
    report = SplitReport(max_lines=max_lines)
    normalized = content.replace("\r\n", "\n")
    blocks = [b.strip("\n") for b in re.split(r"\n[ \t]*\n", normalized)]
    out: list[str] = []
    for block in blocks:
        if not block.strip():
            continue
        if not _is_plain_paragraph(block):
            out.append(block)
            continue
        pieces = split_paragraph(block, max_lines=max_lines, measure=measure, report=report)
        if len(pieces) > 1:
            report.paragraphs_split += 1
            report.paragraphs_added += len(pieces) - 1
        out.extend(pieces)
    if not out:
        return content, report
    return "\n\n".join(out) + "\n", report


def split_file(
    md_path: Path,
    max_lines: int = DEFAULT_MAX_LINES,
    *,
    backup: bool = True,
) -> SplitReport:
    """
    Aère un fichier .md sur place. Si le contenu change et que `backup` est
    vrai, l'état d'avant est d'abord sauvegardé -- dans la MÊME sauvegarde
    que le nettoyage typographique (`postprocess.backup_path`), et seulement
    s'il n'en existe pas déjà une : l'outil « Annuler le nettoyage » restaure
    ainsi le fichier tel qu'il était avant TOUT le post-traitement de fin.
    """
    from . import postprocess

    md_path = Path(md_path)
    content = md_path.read_text(encoding="utf-8")
    new_content, report = split_markdown(content, max_lines=max_lines)
    if new_content != content.replace("\r\n", "\n") and report.paragraphs_split:
        if backup:
            bpath = postprocess.backup_path(md_path)
            if not bpath.exists():
                bpath.write_text(content, encoding="utf-8")
        md_path.write_text(new_content, encoding="utf-8")
    return report
