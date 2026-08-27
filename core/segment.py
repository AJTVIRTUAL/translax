"""
Découpage du texte brut en segments traduisibles.

Fusion des deux scripts d'origine (`prepare_source.py` et
`prepare_book_source.py`), avec choix automatique de la stratégie :

  - « blocs » : le texte a de vraies coupures de paragraphe (lignes vides),
    cas d'un PDF correctement extrait. On respecte la structure et on
    reconnaît titres / listes à puces / paragraphes.

  - « flux » : le texte est un pavé continu sans aucune ligne vide (cas des
    exports ebook où le retour à la ligne n'est qu'un artefact de césure).
    Impossible de retrouver les paragraphes d'origine : on les reconstruit
    mécaniquement en regroupant des phrases entières (~90 mots).

Un segment est un dict : {"type": "title|heading|bullet|paragraph", "text": ...}
Aucun contenu n'est réécrit : on ne touche qu'aux espaces, aux numéros de
page et aux sauts de page.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

PAGE_NUM_RE = re.compile(r"^\d+$")
SENTENCE_END_RE = re.compile(r'(?<=[.!?])\s+(?=[A-Z0-9"‘“’\'])')

# Abréviations protégées avant le découpage en phrases, sinon le point
# interne provoque une fausse coupure ("Mr. Smith" -> deux phrases).
ABBREVIATIONS = [
    "Mr.", "Mrs.", "Ms.", "Dr.", "St.", "Jr.", "Sr.", "vs.", "etc.",
    "Prof.", "Rev.", "Gen.", "Col.", "Capt.", "Lt.", "Sgt.", "No.", "Vol.",
    "pp.", "U.S.", "U.K.", "i.e.", "e.g.", "A.D.", "B.C.", "Ph.D.", "Ave.",
]

# Seuils de détection de stratégie, calibrés sur les fichiers réels du
# projet : IlluVol1.txt = 0 ligne vide sur 6274 -> flux ; un export
# `pdftotext -layout` en compte typiquement 20 à 40 %.
MIN_BLANK_RATIO = 0.05
MIN_BLANK_LINES = 20

DEFAULT_TARGET_WORDS = 90
HEADING_MAX_CHARS = 100

# Marque un bloc à ne JAMAIS traduire (voir core/vision_ocr.py) -- une page
# bloquée par le filtre de contenu de l'API Anthropic garde son texte
# original, entouré de ce marqueur, plutôt que d'être perdue ou traduite à
# moitié. Repéré par préfixe exact plutôt que par un caractère de contrôle
# invisible : reste lisible même si un cas limite de segmentation le laisse
# fusionné avec du texte voisin (stratégie « flux », voir _segment_flow).
RESTRICTED_MARKER_PREFIX = "⛔ TRANSLAX"


def detect_strategy(raw_text: str) -> str:
    """Retourne "blocks" ou "flow" selon la présence de lignes vides."""
    lines = raw_text.replace("\f", "\n").split("\n")
    blank = sum(1 for line in lines if not line.strip())
    non_blank = len(lines) - blank
    if non_blank == 0:
        return "flow"
    if blank >= MIN_BLANK_LINES and blank / non_blank >= MIN_BLANK_RATIO:
        return "blocks"
    return "flow"


def segment_text(
    raw_text: str,
    strategy: str = "auto",
    target_words: int = DEFAULT_TARGET_WORDS,
) -> list[dict]:
    """
    Texte brut -> liste de segments.

    `strategy` vaut "auto" (défaut), "blocks" ou "flow" pour forcer un mode.
    """
    if strategy == "auto":
        strategy = detect_strategy(raw_text)
    if strategy == "blocks":
        return _segment_blocks(raw_text)
    if strategy == "flow":
        return _segment_flow(raw_text, target_words)
    raise ValueError(f"Stratégie inconnue : {strategy!r}")


# --------------------------------------------------------------------------
# Stratégie « blocs » (ex-prepare_source.py)
# --------------------------------------------------------------------------

def _load_blocks(raw_text: str) -> list[list[str]]:
    raw_text = raw_text.replace("\f", "\n")
    cleaned = []
    for line in raw_text.split("\n"):
        stripped = line.strip()
        if PAGE_NUM_RE.match(stripped):  # numéro de page isolé
            continue
        cleaned.append(line.rstrip())

    blocks: list[list[str]] = []
    current: list[str] = []
    for line in cleaned:
        if line.strip() == "":
            if current:
                blocks.append(current)
                current = []
        else:
            current.append(line.strip())
    if current:
        blocks.append(current)
    return blocks


def _split_bullets(block: list[str]) -> list[str]:
    items: list[str] = []
    current: str | None = None
    for line in block:
        if line.startswith("ü") or line.startswith("Ü"):
            if current is not None:
                items.append(current)
            current = line.lstrip("üÜ").strip()
        elif current is None:
            current = line
        else:
            current += " " + line
    if current is not None:
        items.append(current)
    return items


def _classify(block: list[str]) -> list[dict]:
    if any(line.startswith("ü") for line in block):
        return [
            {"type": "bullet", "text": re.sub(r"\s+", " ", item).strip()}
            for item in _split_bullets(block)
            if item.strip()
        ]
    if block and block[0].startswith(RESTRICTED_MARKER_PREFIX):
        # Vérifié AVANT la détection de titre : un bloc restreint peut être
        # court comme il peut être long, ça ne doit jamais devenir un titre.
        return [{"type": "restricted", "text": "\n".join(block).strip()}]
    # Une ligne unique assez courte pour n'avoir jamais eu besoin d'être
    # coupée : c'est structurellement un titre, pas un paragraphe.
    if len(block) == 1 and len(block[0]) <= HEADING_MAX_CHARS:
        return [{"type": "heading", "text": block[0]}]
    text = re.sub(r"\s+", " ", " ".join(block)).strip()
    return [{"type": "paragraph", "text": text}] if text else []


def _segment_blocks(raw_text: str) -> list[dict]:
    segments: list[dict] = []
    for block in _load_blocks(raw_text):
        segments.extend(_classify(block))
    return segments


# --------------------------------------------------------------------------
# Stratégie « flux » (ex-prepare_book_source.py)
# --------------------------------------------------------------------------

def _protect_abbreviations(text: str) -> str:
    for abbr in ABBREVIATIONS:
        text = text.replace(abbr, abbr.replace(".", "\x00"))
    return text


def _restore_abbreviations(text: str) -> str:
    return text.replace("\x00", ".")


def split_sentences(text: str) -> list[str]:
    protected = _protect_abbreviations(text)
    parts = SENTENCE_END_RE.split(protected)
    return [_restore_abbreviations(p).strip() for p in parts if p.strip()]


def _group_into_paragraphs(sentences: list[str], target_words: int) -> list[str]:
    paragraphs: list[str] = []
    current: list[str] = []
    words = 0
    for sentence in sentences:
        current.append(sentence)
        words += len(sentence.split())
        if words >= target_words:
            paragraphs.append(" ".join(current))
            current = []
            words = 0
    if current:
        paragraphs.append(" ".join(current))
    return paragraphs


def _segment_flow(raw_text: str, target_words: int) -> list[dict]:
    flat = re.sub(r"\s+", " ", raw_text).strip()
    sentences = split_sentences(flat)
    paragraphs = _group_into_paragraphs(sentences, target_words)
    # Repérage best-effort : la stratégie « flux » aplatit tous les sauts de
    # ligne avant de re-regrouper par phrases, donc un bloc restreint n'a
    # aucune chance de rester isolé proprement comme en stratégie « blocs »
    # (voir _classify) -- il peut se retrouver mélangé à des phrases
    # voisines dans le même paragraphe reconstruit. On le marque quand même
    # comme "restricted" pour qu'il ne soit jamais traduit, plutôt que de
    # ne rien faire : le pire cas est qu'un peu de texte voisin reste, lui
    # aussi, non traduit avec lui -- jamais une perte de contenu.
    return [
        {"type": "restricted" if RESTRICTED_MARKER_PREFIX in p else "paragraph", "text": p}
        for p in paragraphs
    ]


# --------------------------------------------------------------------------
# Entrées / sorties JSONL (compatibles avec les fichiers déjà produits)
# --------------------------------------------------------------------------

def save_segments(segments: list[dict], path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for segment in segments:
            f.write(json.dumps(segment, ensure_ascii=False) + "\n")


def load_segments(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
