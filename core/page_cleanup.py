"""
Détection et suppression des en-têtes/pieds de page répétés (titre du
livre, titre de chapitre) et des numéros de page (arabes ou romains),
avant segmentation.

Sans ce nettoyage, ces artefacts de mise en page entrent dans le texte à
traduire au même titre que le vrai contenu, et peuvent couper une phrase à
cheval sur deux pages (le numéro/titre s'intercale entre la fin d'une page
et le début de la suivante).

Principe : contrairement à un balayage de tout le document à la recherche
de lignes répétées, on ne regarde QUE les quelques premières et dernières
lignes de chaque page — c'est là, physiquement, que vivent les en-têtes et
pieds de page. Ça évite de confondre une vraie phrase du corps du texte
(qui pourrait légitimement se répéter, une citation par exemple) avec un
artefact de mise en page. Le découpage par page vient des sauts de page
(``\\f``) conservés par `extract.py` — ce module n'a donc de sens que pour
du texte issu d'un PDF ; un texte source sans saut de page (TXT, MD) ne
produit jamais aucune détection, sans qu'il soit nécessaire de vérifier le
format en amont.

Deux catégories détectées séparément :
  1. Numéro de page isolé (« 42 », « xiv », « XIV ») : la FORME seule
     suffit, aucun seuil de répétition nécessaire.
  2. En-tête/pied de page répété (« 1 | P a g e », « xiv Foreword »,
     titre du livre en haut de chaque page…) : on retire d'abord un
     numéro de page éventuel en tête ou en fin de ligne pour ne garder que
     le « cœur » du texte (ça permet de regrouper « 1 | P a g e » et
     « 2 | P a g e », ou « xiv Foreword » et « Foreword xix »), puis on
     regroupe ces cœurs par ressemblance plutôt que par égalité stricte —
     l'OCR introduit du bruit d'une occurrence à l'autre (rencontré en
     pratique : « THBVODOU » sur une page, « THEVODOU » sur une autre,
     pour le même en-tête). Un groupe n'est retenu que s'il revient assez
     souvent pour ne pas être une coïncidence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

EDGE_LINES = 3              # lignes examinées en haut ET en bas de chaque page
# Nombre minimum d'occurrences pour qu'une ligne-candidate soit retenue
# comme en-tête/pied de page. Volontairement un seuil ABSOLU, pas un
# pourcentage du livre entier : un en-tête de section courte (une préface
# de 8 pages, par exemple) doit être détecté aussi sûrement qu'un en-tête
# de chapitre qui court sur 50 pages -- un pourcentage du total aurait
# laissé passer les petites sections (constaté en pratique : un seuil à
# 10 % du livre ratait « Foreword »/« Preface », presents sur ~10 pages
# chacun dans un livre de 386 pages, alors qu'ils sont sans ambiguïté des
# en-têtes répétés).
MIN_OCCURRENCES = 3
SIMILARITY_THRESHOLD = 0.82  # tolérance au bruit d'OCR pour regrouper deux cœurs de texte

# Une vraie phrase de corps de texte qui déborde sur les 3 premières/
# dernières lignes d'une page (très fréquent : la plupart des pages
# n'ont ni en-tête ni pied de page distinct de leur contenu) n'a AUCUNE
# raison d'être courte. Ce filtre -- calibré comme celui déjà éprouvé
# dans un autre projet pour le même problème -- élimine la quasi-totalité
# de ce bruit avant même de chercher des répétitions : sans lui,
# `detect_running_headers` doit comparer des milliers de fragments de
# phrases uniques entre eux (constaté en pratique : plusieurs minutes
# sur un livre de 231 pages, corrigé par ce filtre à quelques secondes).
MAX_CANDIDATE_CHARS = 80
MAX_CANDIDATE_WORDS = 6

ARABIC_NUM_RE = re.compile(r"^\d{1,4}$")
# Numéral romain valide (ex. xiv, XLII) -- volontairement borné en longueur
# (<=7 caractères) pour limiter le risque qu'un mot anglais composé
# uniquement des lettres i/v/x/l/c/d/m (rare, mais ça existe : « MIX »,
# « LIV ») soit confondu avec un numéro de page.
ROMAN_RE = re.compile(r"^(?=[MDCLXVI])M{0,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$", re.IGNORECASE)
LEADING_NUMBER_RE = re.compile(r"^\s*([ivxlcdm]{1,7}|\d{1,4})\b[\s|:.\-–—]*", re.IGNORECASE)
TRAILING_NUMBER_RE = re.compile(r"[\s|:.\-–—]*\b([ivxlcdm]{1,7}|\d{1,4})\s*$", re.IGNORECASE)
# Un numéro de page mal extrait par l'OCR ne redevient pas forcément un
# chiffre ou un chiffre romain lisible -- parfois juste un symbole isolé
# (rencontré en pratique : un puce « • » suivi d'un « / », à la place d'un
# « 7 » sur une page scannée). `TRAILING_NUMBER_RE` ne peut rien faire d'un
# résidu pareil. Celui-ci rattrape le cas : une poignée de caractères NI
# lettres NI chiffres, en fin de ligne -- jamais un vrai mot, jamais assez
# long pour risquer de retirer autre chose qu'un artefact d'OCR.
TRAILING_JUNK_RE = re.compile(r"\s+[^\w\s]{1,3}$")


def is_bare_page_number(text: str) -> bool:
    """« 42 », « xiv », « XIV » -- une ligne qui n'est QUE ça, sans rien
    d'autre autour."""
    t = text.strip()
    if not t:
        return False
    if ARABIC_NUM_RE.match(t):
        return True
    return len(t) <= 7 and bool(ROMAN_RE.match(t))


def _strip_number_token(text: str) -> str:
    """Retire un numéro de page (arabe ou romain) en tête OU en fin de
    ligne, ainsi qu'un résidu de numéro d'OCR illisible en fin de ligne,
    pour ne garder que le « cœur » du texte à regrouper. Les espaces
    internes sont aussi ramenés à un seul, pour que la comparaison floue
    (`detect_running_headers`) ne soit jamais faussée par un simple trou de
    mise en page entre le titre et un numéro de page mal extrait."""
    core = LEADING_NUMBER_RE.sub("", text, count=1)
    core = TRAILING_NUMBER_RE.sub("", core, count=1)
    core = TRAILING_JUNK_RE.sub("", core, count=1)
    return re.sub(r"\s+", " ", core).strip()


@dataclass
class HeaderOccurrence:
    page_index: int
    line_index: int  # position dans la page, pour cibler la suppression
    text: str


@dataclass
class HeaderGroup:
    representative: str
    is_page_number: bool = False
    occurrences: list[HeaderOccurrence] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.occurrences)


@dataclass
class PageCleanupReport:
    total_pages: int
    groups: list[HeaderGroup] = field(default_factory=list)

    @property
    def lines_removed(self) -> int:
        return sum(g.count for g in self.groups)

    def summary_lines(self, limit: int = 12) -> list[str]:
        ordered = sorted(self.groups, key=lambda g: -g.count)
        lines = []
        for g in ordered[:limit]:
            if g.is_page_number:
                lines.append(f"Numéros de page isolés — {g.count} occurrences retirées")
            else:
                label = g.representative or "(vide)"
                lines.append(f"« {label} » — en-tête/pied de page répété, {g.count} occurrences retirées")
        if len(ordered) > limit:
            lines.append(f"… et {len(ordered) - limit} autre(s) motif(s) répété(s).")
        return lines


def _split_pages(raw_text: str) -> list[str]:
    return raw_text.split("\f")


def _is_candidate_shape(text: str) -> bool:
    """Assez court pour être plausiblement un en-tête/pied de page -- une
    vraie phrase de corps de texte qui déborde sur le bord d'une page n'a
    aucune raison d'être aussi courte."""
    t = text.strip()
    if not t or len(t) > MAX_CANDIDATE_CHARS:
        return False
    return len(t.split()) <= MAX_CANDIDATE_WORDS


def _extract_edge_candidates(pages: list[str]) -> list[HeaderOccurrence]:
    candidates: list[HeaderOccurrence] = []
    for page_index, page_text in enumerate(pages):
        lines = page_text.split("\n")
        nonblank_idx = [i for i, line in enumerate(lines) if line.strip()]
        if not nonblank_idx:
            continue
        edge_idx = set(nonblank_idx[:EDGE_LINES]) | set(nonblank_idx[-EDGE_LINES:])
        for i in edge_idx:
            text = lines[i].strip()
            if _is_candidate_shape(text):
                candidates.append(HeaderOccurrence(page_index, i, text))
    return candidates


def detect_running_headers(pages: list[str]) -> PageCleanupReport:
    """
    Analyse les bords de chaque page et retourne les motifs détectés, sans
    rien modifier — c'est `strip_running_headers` qui applique.

    Regroupement en deux temps, pour rester rapide même sur des centaines
    de pages : d'abord un regroupement EXACT par dictionnaire (rapide,
    absorbe la grande majorité des cas comme « 1 | P a g e » / « 2 | P a g
    e »), puis une fusion floue mais seulement ENTRE LES GROUPES déjà
    formés (un nombre bien plus restreint que le nombre total de lignes),
    pour absorber le bruit d'OCR résiduel (ex. « THBVODOU » vs « THEVODOU »
    pour le même en-tête).
    """
    total_pages = len(pages)
    report = PageCleanupReport(total_pages=total_pages)
    if total_pages < 2:
        return report  # rien à comparer -- pas de PDF multi-page, pas de saut \f

    candidates = _extract_edge_candidates(pages)

    number_occurrences: list[HeaderOccurrence] = []
    exact_groups: dict[str, HeaderGroup] = {}

    for c in candidates:
        if is_bare_page_number(c.text):
            number_occurrences.append(c)
            continue
        core = _strip_number_token(c.text)
        key = re.sub(r"\s+", " ", core.lower()).strip()
        if not key:
            continue
        group = exact_groups.get(key)
        if group is None:
            group = HeaderGroup(representative=core)
            exact_groups[key] = group
        group.occurrences.append(c)

    # Les groupes les plus fournis d'abord : ils absorbent les variantes
    # rares plutôt que l'inverse, et un gros groupe déjà confirmé n'a pas
    # besoin d'être lui-même comparé à un groupe plus petit qui lui sera
    # de toute façon fusionné.
    remaining_groups = sorted(exact_groups.values(), key=lambda g: -g.count)
    merged: list[HeaderGroup] = []
    for g in remaining_groups:
        target = None
        best_ratio = 0.0
        for m in merged:
            sm = SequenceMatcher(None, g.representative.lower(), m.representative.lower())
            if sm.quick_ratio() < SIMILARITY_THRESHOLD:
                continue  # borne supérieure rapide : inutile de calculer le vrai ratio
            ratio = sm.ratio()
            if ratio >= SIMILARITY_THRESHOLD and ratio > best_ratio:
                target, best_ratio = m, ratio
        if target is not None:
            target.occurrences.extend(g.occurrences)
        else:
            merged.append(g)

    if number_occurrences:
        merged.append(HeaderGroup(representative="", is_page_number=True, occurrences=number_occurrences))

    report.groups = [g for g in merged if g.is_page_number or g.count >= MIN_OCCURRENCES]
    return report


def strip_running_headers(raw_text: str, report: PageCleanupReport) -> str:
    """Retire les occurrences identifiées par `detect_running_headers`,
    page par page. Les sauts de page (\\f) sont conservés -- seules les
    lignes ciblées disparaissent, le reste de chaque page est intact."""
    to_remove: dict[int, set[int]] = {}
    for g in report.groups:
        for c in g.occurrences:
            to_remove.setdefault(c.page_index, set()).add(c.line_index)

    if not to_remove:
        return raw_text

    pages = _split_pages(raw_text)
    cleaned_pages = []
    for page_index, page_text in enumerate(pages):
        removed_here = to_remove.get(page_index)
        if not removed_here:
            cleaned_pages.append(page_text)
            continue
        lines = page_text.split("\n")
        kept = [line for i, line in enumerate(lines) if i not in removed_here]
        cleaned_pages.append("\n".join(kept))
    return "\f".join(cleaned_pages)


def clean_pdf_pages(raw_text: str) -> tuple[str, PageCleanupReport]:
    """
    Point d'entrée unique : texte brut -> (texte nettoyé, rapport).

    Si `raw_text` ne contient aucun saut de page (cas des sources TXT/MD,
    ou d'un PDF d'une seule page), le rapport revient vide et le texte est
    retourné inchangé -- pas besoin de savoir le format en amont.
    """
    if "\f" not in raw_text:
        return raw_text, PageCleanupReport(total_pages=1)
    pages = _split_pages(raw_text)
    report = detect_running_headers(pages)
    cleaned = strip_running_headers(raw_text, report) if report.groups else raw_text
    return cleaned, report
