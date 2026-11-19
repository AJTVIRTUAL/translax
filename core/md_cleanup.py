"""
Nettoyage avancé d'un fichier .md existant -- demande explicite de
l'utilisateur (19/11/2026) : « importer un fichier md avec un traitement
avancé : détecter les titres en plein milieu des phrases qui se répètent
(titres de haut ou de bas de page) et réorganiser correctement ». Exemple
réel (Leadbeater, « Le côté caché des choses », tome 2) :

    … de la détérioration apportée 10 LE COTÉ CACHÉ DES CHOSES au corps
    mental et astral …
 -> … de la détérioration apportée au corps mental et astral …

Le texte d'un .md déjà assemblé n'a plus de pages : les en-têtes et pieds de
page courants (numéro de page + titre du livre ou du chapitre) se retrouvent
NOYÉS dans les paragraphes, souvent en pleine phrase. `page_cleanup.py` et
`document_flow.py` ne peuvent rien pour eux : ils travaillent sur les lignes
du haut et du bas de chaque page, avant l'assemblage.

Étapes, dans l'ordre :

  1. **Apprendre les en-têtes du document** (`detect_running_headers`) --
     jamais une liste figée. Signal : une suite de mots EN MAJUSCULES collée
     à un numéro de page (avant ou après), qui revient avec au moins 2 ou 3
     numéros DIFFÉRENTS. « LE COTÉ CACHÉ DES CHOSES » (pages paires) et
     « PAR NOS HABITUDES » (pages impaires, titre du chapitre) sont trouvés
     ainsi, chacun avec ses numéros.
  2. **Les retirer** (`remove_running_headers`), en réparant au passage :
       - le mot coupé en fin de page : « d'ali28 LE COTÉ… ments »
         -> « d'aliments » (le numéro COLLÉ au mot prouve la coupure) ;
       - le numéro de cahier d'imprimeur posé juste avant : « Pendant 2 18
         LE COTÉ… une longue période » -> « Pendant une longue période » ;
       - jamais dans la table des matières (« CHAPITRE XIV. — PAR NOS
         HABITUDES 7 » reste intact : précédé d'un tiret, c'est une entrée).
  3. **Recoller les paragraphes coupés** par la page (« s'y sont habi- » +
     « tués, il lutte… » -> « habitués, il lutte… »), en enjambant une note
     intercalée -- c'est `document_flow.stitch_pages`, réutilisé tel quel.
  4. **Dégager les titres** restés collés au texte (`extract_titles`) :
     « CHAPITRE XIV PAR NOS HABITUDES NOURRITURE Une sentence… » devient un
     titre de chapitre, un titre de section, puis le paragraphe.

Aucun mot d'auteur n'est réécrit : seuls disparaissent les en-têtes, leurs
numéros et les numéros de cahier ; seuls des sauts de paragraphe et des
marques de titre (#, ##) sont ajoutés.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import document_flow, paragraphs as paragraphs_mod

# --- Mots en majuscules ------------------------------------------------------
_UP = "A-ZÀ-ÖØ-ÞŒÆ"
_LOW = "a-zà-öø-ÿœæ"
# Un mot entièrement en majuscules (apostrophes et traits d'union compris :
# « L'INFLUENCE », « NOUS-MÊMES »), qui se termine VRAIMENT là -- jamais le
# « L » de « L'étudiant » ni le début d'un mot en minuscules.
_UPWORD = rf"[{_UP}][{_UP}'’\-]*(?![\w'’])"
_RUN_RE = re.compile(rf"{_UPWORD}(?:[ \t]+{_UPWORD})*")
_ROMAN_RE = re.compile(r"^[IVXLC]+$")

# Numéro de page : 1 à 3 chiffres, jamais un morceau d'un nombre plus long.
_NUM_BEFORE_RE = re.compile(rf"(?<!\d)(\d{{1,3}})[ \t]+({_UPWORD}(?:[ \t]+{_UPWORD})*)")
_NUM_AFTER_RE = re.compile(rf"(?<![\w'’])({_UPWORD}(?:[ \t]+{_UPWORD})*)[ \t]+(\d{{1,3}})(?![\d\w])")

# Une entrée de table des matières : « CHAPITRE XIV. — PAR NOS HABITUDES 7 ».
_TOC_BEFORE_RE = re.compile(r"(?:—|–|CHAPITRE\s+[IVXLC]+\.?)\s*$")

# Seuils de détection (voir `detect_running_headers`). 3 numéros différents
# au minimum : à 2, une adresse d'éditeur citée deux fois en fin de livre
# (« SIÈGE DE LA SOCIÉTÉ … 4, square… ») passait pour un en-tête -- vérifié
# sur un vrai livre. Le plus court chapitre réel y avait encore 4 pages
# d'en-tête.
_MIN_LETTERS = 6
_MIN_PAGES_BY_WORDS = {1: 4}         # un seul mot : 4 numéros ; sinon 3
_MIN_PAGES = 3
_COVERAGE_TO_DROP = 0.8
# Variante d'un en-tête à une coquille près (OCR : « LF COTÉ » pour « LE COTÉ »).
_VARIANT_SIMILARITY = 0.9


@dataclass
class RunningHeader:
    text: str
    pages: set[int] = field(default_factory=set)
    removed: int = 0
    # Coquilles d'OCR du même en-tête (« LF COTÉ CACHÉ… »), retirées avec lui.
    variants: set[str] = field(default_factory=set)


@dataclass
class MdCleanupReport:
    headers: list[RunningHeader] = field(default_factory=list)
    occurrences_removed: int = 0
    words_rejoined: int = 0
    signature_marks_removed: int = 0
    paragraphs_stitched: int = 0
    chapters_extracted: int = 0
    sections_extracted: int = 0
    paragraph_report: paragraphs_mod.SplitReport | None = None

    def summary_lines(self) -> list[str]:
        lines: list[str] = []
        if self.headers:
            found = ", ".join(f"« {h.text} » ({h.removed}×)" for h in self.headers if h.removed)
            lines.append(f"En-têtes / pieds de page retirés : {self.occurrences_removed} — {found}.")
        else:
            lines.append("Aucun en-tête ou pied de page répété détecté.")
        if self.words_rejoined:
            lines.append(f"Mots coupés en fin de page recollés : {self.words_rejoined}.")
        if self.signature_marks_removed:
            lines.append(f"Numéros de cahier d'imprimeur retirés : {self.signature_marks_removed}.")
        if self.paragraphs_stitched:
            lines.append(f"Paragraphes coupés par la page recollés : {self.paragraphs_stitched}.")
        if self.chapters_extracted or self.sections_extracted:
            lines.append(
                f"Titres dégagés du texte : {self.chapters_extracted} chapitre(s), "
                f"{self.sections_extracted} section(s)."
            )
        if self.paragraph_report is not None:
            lines.append(self.paragraph_report.summary())
        return lines


# ---------------------------------------------------------------------------
# 1. Détection
# ---------------------------------------------------------------------------

def _letters(text: str) -> int:
    return sum(1 for c in text if c.isalpha())


def _is_toc_context(text: str, start: int) -> bool:
    return bool(_TOC_BEFORE_RE.search(text[max(0, start - 30):start]))


def detect_running_headers(text: str) -> list[RunningHeader]:
    """
    Repère les en-têtes / pieds de page courants du document.

    Chaque suite de mots en majuscules collée à un numéro donne des
    candidats : ses DÉBUTS quand le numéro est devant (« 10 LE COTÉ CACHÉ
    DES CHOSES MÉTHODE… » -> « LE COTÉ… CHOSES » parmi d'autres), ses FINS
    quand il est derrière. Un candidat est retenu s'il revient avec assez de
    numéros différents (plus il est court, plus il en faut). Puis on ne garde
    que les plus longs : « LE COTÉ » disparaît derrière « LE COTÉ CACHÉ DES
    CHOSES », « PAR NOS » derrière l'ensemble des « PAR NOS HABITUDES »,
    « PAR NOS AMUSEMENTS »… qui couvrent ses mêmes pages.
    """
    pages: dict[str, set[int]] = {}

    def add(words: list[str], number: int) -> None:
        pages.setdefault(" ".join(words), set()).add(number)

    for match in _NUM_BEFORE_RE.finditer(text):
        if _is_toc_context(text, match.start()):
            continue
        words = match.group(2).split()
        for k in range(1, len(words) + 1):
            add(words[:k], int(match.group(1)))
    for match in _NUM_AFTER_RE.finditer(text):
        if _is_toc_context(text, match.start()):
            continue
        words = match.group(1).split()
        for k in range(1, len(words) + 1):
            add(words[-k:], int(match.group(2)))

    candidates: list[RunningHeader] = []
    for phrase, numbers in pages.items():
        words = phrase.split()
        if _letters(phrase) < _MIN_LETTERS or all(_ROMAN_RE.match(w) for w in words):
            continue
        if "CHAPITRE" in words:
            # « CHAPITRE XV » suivi d'un numéro de cahier, ou en-tête collé au
            # début d'un chapitre (« 60 LE COTÉ… CHOSES CHAPITRE XVI ») : le mot
            # appartient au titre du chapitre, jamais à l'en-tête.
            continue
        if len(numbers) < _MIN_PAGES_BY_WORDS.get(len(words), _MIN_PAGES):
            continue
        candidates.append(RunningHeader(phrase, set(numbers)))

    # Les plus longs d'abord : un candidat plus court, contenu au début ou à
    # la fin de candidats plus longs déjà retenus qui couvrent presque toutes
    # ses pages, n'en est qu'un morceau.
    candidates.sort(key=lambda h: (-len(h.text.split()), -len(h.pages)))
    kept: list[RunningHeader] = []
    for header in candidates:
        covered: set[int] = set()
        for longer in kept:
            if longer.text.startswith(header.text + " ") or longer.text.endswith(" " + header.text):
                covered |= longer.pages
        if covered and len(covered & header.pages) >= _COVERAGE_TO_DROP * len(header.pages):
            continue
        kept.append(header)
    _find_variants(text, kept)
    return kept


def _find_variants(text: str, headers: list[RunningHeader]) -> None:
    """
    Coquilles d'OCR d'un en-tête déjà reconnu, collées elles aussi à un
    numéro (« 212 LF COTÉ CACHÉ DES CHOSES ») : même nombre de mots, au
    plus une lettre ou deux de différence. Trop rares pour être apprises
    seules, mais aussi parasites que l'en-tête exact.
    """
    from difflib import SequenceMatcher

    known = {header.text for header in headers}

    def consider(words: list[str]) -> None:
        for header in headers:
            n = len(header.text.split())
            if len(words) < n:
                continue
            for candidate in (" ".join(words[:n]), " ".join(words[-n:])):
                if candidate in known or candidate in header.variants:
                    continue
                if SequenceMatcher(None, candidate, header.text).ratio() >= _VARIANT_SIMILARITY:
                    header.variants.add(candidate)

    for match in _NUM_BEFORE_RE.finditer(text):
        if not _is_toc_context(text, match.start()):
            consider(match.group(2).split())
    for match in _NUM_AFTER_RE.finditer(text):
        if not _is_toc_context(text, match.start()):
            consider(match.group(1).split())


# ---------------------------------------------------------------------------
# 2. Retrait
# ---------------------------------------------------------------------------

def _header_pattern(header: str) -> str:
    return r"[ \t]+".join(re.escape(word) for word in header.split())


_WORD_RE = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*")


def _vocabulary(text: str, forms: list[str]) -> set[str]:
    """
    Mots du document lui-même (en minuscules), pour juger un recollage --
    SANS les morceaux de mots coupés, qui sinon passeraient pour des mots
    (« ment » de « complète11 … ment » revient à chaque coupure en -ment) :
    on masque les lettres collées à un numéro et le premier mot qui suit
    chaque en-tête.
    """
    masked = re.sub(r"[^\W\d_]+(?=\d)", " ", text)
    for form in forms:
        masked = re.sub(
            rf"\d{{1,3}}[ \t]+{_header_pattern(form)}[ \t]+[^\W\d_]+(?:['’][^\W\d_]+)?", " ", masked
        )
    return {w.lower() for w in _WORD_RE.findall(masked)}


def _last_word(text: str) -> str:
    match = re.search(r"([^\W\d_]+)$", text)
    return match.group(1) if match else ""


def _first_word(text: str) -> str:
    match = re.match(r"[^\W\d_]+(?:['’][^\W\d_]+)?", text)
    return match.group(0) if match else ""


# Fins de mots courantes (français, anglais) qui ne sont presque jamais un
# mot à elles seules : « faible » + « ment » est forcément « faiblement »,
# même quand ce mot n'apparaît nulle part ailleurs dans le livre.
_SUFFIX_FRAGMENTS = frozenset({
    "ment", "ments", "tion", "tions", "sion", "sions", "ité", "ités", "able", "ables",
    "ible", "ibles", "ique", "iques", "ance", "ances", "ence", "ences", "euse", "euses",
    "ness", "ing", "ings", "ly", "tive", "tives", "lement",
})


def _should_join(left: str, right: str, vocabulary: set[str]) -> bool:
    """
    Le numéro de page COLLÉ à un mot (« d'ali28 ») signale un mot coupé par
    la page -- mais pas toujours : quand du texte manque dans la source, la
    page suivante peut reprendre sur un autre mot (« aussi parfaite156 LE
    COTÉ… ce point de vue », vu sur un vrai livre). On recolle :
      - si le mot recollé existe ailleurs dans le même livre (« importance ») ;
      - sinon, si l'un des deux morceaux n'est pas un mot du livre (« ali »
        + « ments ») -- c'est forcément un mot coupé ;
    et on laisse une espace quand les deux morceaux sont de vrais mots et que
    leur assemblage n'apparaît nulle part (« parfaite » + « ce »).
    """
    if not left or not right:
        return False
    joined = (left + right).lower()
    if joined in vocabulary:
        return True
    if right.lower() in _SUFFIX_FRAGMENTS and len(left) >= 3:
        return True
    return left.lower() not in vocabulary or right.lower() not in vocabulary


def remove_running_headers(text: str, headers: list[RunningHeader], report: MdCleanupReport) -> str:
    """Retire chaque en-tête avec son numéro -- voir le docstring du module."""
    forms = [(header, form) for header in headers for form in (header.text, *sorted(header.variants))]
    vocabulary = _vocabulary(text, [form for _, form in forms])
    for header, form in sorted(forms, key=lambda pair: -len(pair[1])):
        body = _header_pattern(form)
        before = re.compile(
            rf"(?:(?<![\w.,])(?P<sig>\d{{1,2}})[ \t]+)?(?<!\d)(?P<num>\d{{1,3}})[ \t]+{body}(?![\w'’])(?P<tail>[ \t]*)"
        )
        after = re.compile(rf"(?<![\w'’]){body}[ \t]+(?P<num>\d{{1,3}})(?![\d\w])(?P<tail>[ \t]*)")
        alone = re.compile(rf"(?<![\w'’]){body}[ \t]+(?=CHAPITRE\b)")

        def strip_before(match: re.Match) -> str:
            start = match.start("num")
            if _is_toc_context(text_ref[0], match.start()):
                return match.group(0)
            header.removed += 1
            report.occurrences_removed += 1
            glued = start > 0 and text_ref[0][start - 1].isalpha()
            following = text_ref[0][match.end():match.end() + 40]
            if glued and following[:1].islower():
                # « d'ali28 LE COTÉ… ments » : le numéro collé au mot signale
                # un mot coupé par la page -> « d'aliments » (voir _should_join).
                left = _last_word(text_ref[0][max(0, start - 40):start])
                if _should_join(left, _first_word(following), vocabulary):
                    report.words_rejoined += 1
                    return ""
            if match.group("sig") and not glued:
                report.signature_marks_removed += 1
            return " "

        def strip_after(match: re.Match) -> str:
            if _is_toc_context(text_ref[0], match.start()):
                return match.group(0)
            header.removed += 1
            report.occurrences_removed += 1
            return " "

        text_ref = [text]
        text = before.sub(strip_before, text)
        text_ref = [text]
        text = after.sub(strip_after, text)
        removed_alone = len(alone.findall(text))
        if removed_alone:
            header.removed += removed_alone
            report.occurrences_removed += removed_alone
            text = alone.sub("", text)

    # Espaces doubles laissés par les retraits ; espace orphelin devant une
    # virgule, un point ou une parenthèse fermante (jamais devant ; : ! ? »,
    # que la typographie française précède d'une espace).
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r" +([,.)\]])", r"\1", text)
    blocks = [block.strip() for block in re.split(r"\n\s*\n", text)]
    return "\n\n".join(block for block in blocks if block) + "\n"


# ---------------------------------------------------------------------------
# 4. Titres dégagés du texte
# ---------------------------------------------------------------------------

# Début de phrase : majuscule suivie d'une minuscule (« Les », « L'étudiant »).
_SENTENCE_START = rf"[{_UP}](?:['’][{_LOW}]|[{_LOW}])"
# Numéro romain, coquilles d'OCR comprises (« XVIH » pour « XVIII ») : gardé
# tel quel dans le titre, jamais « corrigé ».
_CHAPTER_RE = re.compile(
    rf"(?<![\w—–.])CHAPITRE[ \t]+(?P<roman>[IVXLC][IVXLCH1l]*)\b\.?[ \t]+(?:\d{{1,2}}[ \t]+)?"
    rf"(?P<title>{_UPWORD}(?:[ \t]+{_UPWORD})*)(?=[ \t]+{_SENTENCE_START}|[ \t]*$)"
)
_SECTION_RE = re.compile(
    rf"(?P<title>{_UPWORD}(?:[ \t]+{_UPWORD})*)(?=[ \t]+{_SENTENCE_START})"
)


def _is_toc_block(block: str) -> bool:
    return block.count("CHAPITRE") >= 2 or " — " in block and block.count("—") >= 3


def _looks_like_title(title: str) -> bool:
    words = title.split()
    if all(_ROMAN_RE.match(w) for w in words):
        return False
    if len(words) == 1:
        return _letters(title) >= 5
    return _letters(title) >= 6


def _at_sentence_start(block: str, position: int) -> bool:
    before = block[:position].rstrip()
    if not before:
        return True
    if not re.search(r"[.!?…»]$", before):
        return False
    # « C. W. LEADBEATER » : le point d'une initiale n'est pas une fin de phrase.
    return not paragraphs_mod._looks_like_abbreviation(before[-40:])  # noqa: SLF001 - même règle partagée


def extract_titles(text: str, headers: list[RunningHeader], report: MdCleanupReport) -> str:
    """
    Sort de leur paragraphe les titres restés collés au texte :
      - « CHAPITRE XIV PAR NOS HABITUDES NOURRITURE Une sentence… » ->
        « # CHAPITRE XIV — PAR NOS HABITUDES », « ## NOURRITURE », texte ;
      - « … fin de phrase. LA PROPRETÉ Les médecins… » -> « ## LA PROPRETÉ ».
    Un titre de section n'est reconnu qu'en début de paragraphe ou juste
    après une fin de phrase, et seulement s'il est suivi d'un vrai début de
    phrase -- jamais un mot mis en majuscules au milieu d'une phrase.
    """
    known = sorted((h.text for h in headers), key=len, reverse=True)
    out: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block:
            continue
        if block.startswith("#") or _is_toc_block(block):
            out.append(block)
            continue

        pieces: list[str] = []
        cursor = 0
        work = block

        def flush_text(upto: int) -> None:
            chunk = work[cursor:upto].strip()
            if chunk:
                pieces.append(chunk)

        events: list[tuple[int, int, list[str]]] = []
        # Pas d'exigence de début de phrase pour un chapitre : « CHAPITRE »
        # + numéro + titre en majuscules + vraie phrase est un signal assez
        # fort à lui seul (vu derrière un sous-titre sans point final :
        # « Comment nous influençons les autres CHAPITRE XVIH PAR CE QUE… »).
        for match in _CHAPTER_RE.finditer(work):
            title = match.group("title")
            chapter, section = title, ""
            for phrase in known:
                if title == phrase or title.startswith(phrase + " "):
                    chapter, section = phrase, title[len(phrase):].strip()
                    break
            lines = [f"# CHAPITRE {match.group('roman')} — {chapter}"]
            report.chapters_extracted += 1
            if section and _looks_like_title(section):
                lines.append(f"## {section}")
                report.sections_extracted += 1
            elif section:
                lines[0] += f" {section}"
            events.append((match.start(), match.end(), lines))

        taken = [(s, e) for s, e, _ in events]
        for match in _SECTION_RE.finditer(work):
            if any(s <= match.start() < e for s, e in taken):
                continue
            title = match.group("title")
            if title.startswith("CHAPITRE") or not _looks_like_title(title):
                continue
            if not _at_sentence_start(work, match.start()):
                continue
            events.append((match.start(), match.end(), [f"## {title}"]))
            report.sections_extracted += 1

        if not events:
            out.append(block)
            continue
        for start, end, lines in sorted(events):
            flush_text(start)
            pieces.extend(lines)
            cursor = end
        flush_text(len(work))
        out.extend(pieces)
    return "\n\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------

def clean_markdown(
    text: str,
    *,
    extract_headings: bool = True,
    aerate: bool = False,
    max_lines: int = paragraphs_mod.DEFAULT_MAX_LINES,
) -> tuple[str, MdCleanupReport]:
    """Enchaîne les étapes du docstring du module sur le contenu d'un .md."""
    report = MdCleanupReport()
    text = text.replace("\r\n", "\n").replace("\f", "\n\n")

    report.headers = detect_running_headers(text)
    text = remove_running_headers(text, report.headers, report)

    flow = document_flow.FlowReport()
    text = document_flow.stitch_pages(text, flow)
    report.paragraphs_stitched = flow.paragraphs_stitched

    if extract_headings:
        text = extract_titles(text, report.headers, report)
    if aerate:
        text, report.paragraph_report = paragraphs_mod.split_markdown(text, max_lines=max_lines)
    if not text.endswith("\n"):
        text += "\n"
    return text, report


def cleaned_path(source: Path) -> Path:
    """« livre.md » -> « livre (nettoyé).md », jamais par-dessus un fichier existant."""
    source = Path(source)
    candidate = source.with_name(f"{source.stem} (nettoyé).md")
    n = 2
    while candidate.exists():
        candidate = source.with_name(f"{source.stem} (nettoyé {n}).md")
        n += 1
    return candidate


def clean_file(
    source: Path,
    *,
    extract_headings: bool = True,
    aerate: bool = True,
    make_pdf: bool = False,
) -> tuple[Path, Path | None, MdCleanupReport]:
    """
    Nettoie `source` et écrit le résultat dans un NOUVEAU fichier à côté
    (`cleaned_path`) -- l'original n'est jamais modifié. Retourne
    (fichier .md écrit, PDF éventuel, rapport).
    """
    from . import pdf_export

    source = Path(source)
    content = source.read_text(encoding="utf-8")
    cleaned, report = clean_markdown(content, extract_headings=extract_headings, aerate=aerate)
    destination = cleaned_path(source)
    destination.write_text(cleaned, encoding="utf-8")
    pdf_path = None
    if make_pdf:
        pdf_path = destination.with_suffix(".pdf")
        pdf_export.markdown_to_pdf(cleaned, pdf_path)
    return destination, pdf_path, report
