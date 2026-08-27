"""
Numéro de version et date de build de TRANSLAX — affichés tout en bas de la
fenêtre (voir `ui/main_window.py`).

Ce fichier est régénéré automatiquement par `scripts/stamp_build_date.py` à
chaque empaquetage (voir SPEC.md §8 / MACOS_BUILD.md) : seule la ligne
BUILD_DATE est réécrite, avec la date réelle du jour de la construction.
VERSION reste inchangée sauf décision délibérée d'incrémenter (nouvelle
fonctionnalité notable) via `--bump`.

Ne pas éditer les valeurs ci-dessous à la main dans le cours normal du
travail — c'est le rôle du script.
"""
from __future__ import annotations

VERSION = "1.17.0"
BUILD_DATE = "2026-08-27"  # AAAA-MM-JJ

_MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def _ordinal(day: int) -> str:
    """1 -> "1st", 2 -> "2nd", 3 -> "3rd", 4 -> "4th"... 11/12/13 -> "th"
    (exception anglaise standard : onzième/douzième/treizième ne suivent
    pas la règle du dernier chiffre)."""
    if 10 <= day % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def format_build_date(iso_date: str = BUILD_DATE) -> str:
    """"2026-08-23" -> "23rd August 2026"."""
    year, month, day = (int(part) for part in iso_date.split("-"))
    return f"{_ordinal(day)} {_MONTHS[month - 1]} {year}"


def version_string() -> str:
    return f"TRANSLAX v{VERSION}  ·  {format_build_date()}"
