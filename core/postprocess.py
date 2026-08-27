"""
Passe de nettoyage sur le Markdown traduit (ex-`cleanup_headings.py`).

Deux corrections, purement structurelles — aucun contenu n'est réécrit :

  1. Rétrograder les « ## » qui n'en sont pas. L'heuristique « ligne unique
     et courte = titre » de la segmentation attrape aussi des fragments de
     tableau ou des phrases complètes. La décision se prend sur le texte
     ANGLAIS d'origine (aligné 1:1 avec les blocs français par position),
     sur des signaux structurels seulement : mots-clés, ponctuation, casse.

  2. Corriger un artefact de détokenisation de NLLB : une espace parasite
     après un trait d'union à l'intérieur d'un mot (« nous- mêmes »).

Cette passe est indispensable au rendu final ; elle faisait partie du
pipeline validé et n'est donc pas optionnelle par défaut.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

KEEP_PATTERNS = [
    r"^chapter\s+\d",
    r"^context\.",
    r"^conclusion",
    r"^training exercises",
    r"^table\s+\d",
    r"^table of contents",
    r"^foundation for",
    r"^\d+(\.\d+)?[\.\s]",  # « 1. », « 1.1 ... », sous-sections numérotées
]
KEEP_RE = re.compile("|".join(KEEP_PATTERNS), re.IGNORECASE)
HYPHEN_SPACE_RE = re.compile(r"([A-Za-zÀ-ÖØ-öø-ÿ])-\s+([A-Za-zÀ-ÖØ-öø-ÿ])")


@dataclass
class CleanupReport:
    demoted: int = 0
    hyphen_fixes: int = 0
    aligned: bool = True
    message: str = ""

    def summary(self) -> str:
        if not self.aligned:
            return f"Nettoyage ignoré : {self.message}"
        return (
            f"{self.demoted} faux titres rétrogradés, "
            f"{self.hyphen_fixes} traits d'union recollés."
        )


def should_demote(english_text: str) -> bool:
    text = english_text.strip()
    if KEEP_RE.search(text):
        return False
    if text[:1].islower():
        return True
    if text.endswith((".", "!", "?")) and len(text.split()) > 3:
        return True
    return False


def cleanup_markdown(segments: list[dict], content: str) -> tuple[str, CleanupReport]:
    """
    Retourne le Markdown nettoyé et un rapport.

    Si le nombre de blocs ne correspond pas au nombre de segments (traduction
    partielle, fichier édité à la main), la rétrogradation des titres est
    abandonnée — elle repose sur un alignement 1:1 — mais la correction des
    traits d'union, elle, reste sûre et est appliquée.
    """
    report = CleanupReport()
    report.hyphen_fixes = len(HYPHEN_SPACE_RE.findall(content))

    blocks = [b for b in content.split("\n\n") if b.strip()]
    if len(blocks) != len(segments):
        report.aligned = False
        report.message = (
            f"{len(segments)} segments source pour {len(blocks)} blocs traduits "
            "— seule la correction des traits d'union est appliquée."
        )
        return HYPHEN_SPACE_RE.sub(r"\1-\2", content), report

    new_blocks = []
    for segment, block in zip(segments, blocks):
        if segment["type"] == "heading" and block.startswith("## ") and should_demote(segment["text"]):
            block = block[len("## "):]
            report.demoted += 1
        new_blocks.append(block)

    cleaned = "\n\n".join(new_blocks) + "\n"
    return HYPHEN_SPACE_RE.sub(r"\1-\2", cleaned), report


def backup_path(md_path: Path) -> Path:
    """
    Où est rangée une copie du fichier telle qu'elle était AVANT le
    nettoyage -- seule façon fiable d'annuler ensuite : la rétrogradation
    de faux titres est reconstructible (elle vient d'une règle appliquée au
    texte source), mais le recollement des traits d'union ne l'est pas
    (« mot-mot » après coup ne dit pas si c'était déjà collé ou pas avant).
    """
    md_path = Path(md_path)
    return md_path.with_name(md_path.name + ".avant_nettoyage")


def has_backup(md_path: Path) -> bool:
    """True s'il existe une sauvegarde à restaurer pour ce fichier."""
    return backup_path(md_path).exists()


def undo_cleanup(md_path: Path) -> bool:
    """
    Restaure le fichier tel qu'il était avant le nettoyage typographique
    (voir `cleanup_file`) -- demande explicite de l'utilisateur : pouvoir
    revenir en arrière si le nettoyage a corrigé quelque chose à tort.

    Retourne False sans rien changer si aucune sauvegarde n'existe (jamais
    nettoyé, ou déjà annulé une fois) -- appelant responsable d'avertir
    l'utilisateur dans ce cas plutôt que cette fonction elle-même.
    """
    md_path = Path(md_path)
    bpath = backup_path(md_path)
    if not bpath.exists():
        return False
    md_path.write_text(bpath.read_text(encoding="utf-8"), encoding="utf-8")
    bpath.unlink()
    return True


def cleanup_file(segments: list[dict], md_path: Path, apply: bool = True) -> CleanupReport:
    md_path = Path(md_path)
    content = md_path.read_text(encoding="utf-8")
    cleaned, report = cleanup_markdown(segments, content)
    if apply and cleaned != content:
        # Sauvegardé une seule fois : si ce fichier a déjà une sauvegarde
        # (reprise d'un job déjà nettoyé une première fois), ne pas
        # l'écraser par une version déjà nettoyée -- ça rendrait "Annuler"
        # incapable de retrouver le tout premier état.
        bpath = backup_path(md_path)
        if not bpath.exists():
            bpath.write_text(content, encoding="utf-8")
        md_path.write_text(cleaned, encoding="utf-8")
    return report
