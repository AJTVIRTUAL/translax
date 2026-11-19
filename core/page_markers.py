"""
Liste noire des marqueurs de page -- demande explicite de l'utilisateur
(19/11/2026) : « 17 of 103 », « Page 17 of 103 », « 17 / 103 »… doivent être
détectés et supprimés automatiquement, surtout quand ils coupent une phrase :

    Miss X. retained subconsciously the knowledge and powers that had been
    hers during the previous life when she was
    17 of 103
    implicated in the witch-cult.
 -> … during the previous life when she was implicated in the witch-cult.

**Pour ajouter un marqueur** (l'utilisateur prévoit d'en ajouter d'autres) :
une entrée de plus dans `BLACKLIST`, rien d'autre. Chaque entrée donne :

  - `name`    : nom court, affiché dans les rapports (« N of M ») ;
  - `regex`   : le motif, insensible à la casse, avec le groupe nommé `num`
                (numéro de la page) et, s'il existe, `total` (nombre de
                pages). Utiliser `[ \\t]` plutôt que `\\s` : un marqueur tient
                sur une ligne ;
  - `inline`  : True s'il peut être retiré en pleine phrase, False s'il ne
                l'est que seul sur sa ligne (« Page 17 » dans « voir page 17 »
                est une vraie référence, jamais retirée) ;
  - `examples`: au moins un exemple réel -- `tests/test_page_markers.py`
                vérifie automatiquement que chaque exemple est bien reconnu.

**Garde-fous** (un vrai « 3 of 10 patients » ne doit jamais disparaître) :
les occurrences sont regroupées par marqueur ET par total (« … of 103 »), les
numéros plus grands que le total sont ignorés, et un groupe n'est retiré que
s'il ressemble vraiment à une pagination :

  - au moins une occurrence SEULE SUR SA LIGNE (la forme d'un vrai pied de
    page) et `MIN_DISTINCT_STANDALONE` numéros différents -- deux suffisent,
    document de deux pages compris (« 1 of 2 », « 2 of 2 ») ;
  - sinon (uniquement en pleine phrase, lignes déjà recollées) : au moins
    `MIN_DISTINCT_INLINE` numéros différents, qui AVANCENT au fil du document
    comme des pages (`MIN_INCREASING` des paires successives croissantes) ;
  - sans total (« Page 17 », « - 17 - ») : seulement seul sur sa ligne, avec
    `MIN_DISTINCT_NO_TOTAL` numéros différents.

Appliqué par le nettoyage avancé (core/md_cleanup.py) et, dans le pipeline,
sur le texte brut AVANT le recollage des paragraphes -- là où « seul sur sa
ligne » se voit encore (voir pipeline.run_job).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

MIN_DISTINCT_STANDALONE = 2
MIN_DISTINCT_INLINE = 5
MIN_INCREASING = 0.8
MIN_DISTINCT_NO_TOTAL = 3


@dataclass(frozen=True)
class PageMarker:
    name: str
    regex: str
    inline: bool
    examples: tuple[str, ...]


# ---------------------------------------------------------------------------
# LA LISTE NOIRE -- ajouter les futurs marqueurs ici.
# ---------------------------------------------------------------------------
BLACKLIST: list[PageMarker] = [
    PageMarker(
        "N of M",
        r"(?:page[ \t]+)?(?P<num>\d{1,4})[ \t]+of[ \t]+(?P<total>\d{1,4})",
        inline=True,
        examples=("17 of 103", "Page 17 of 103", "page 17 of 103"),
    ),
    PageMarker(
        "N / M",
        # Espaces obligatoires autour de « / » : « 3/4 », « 12/05/2020 » ne
        # sont jamais des numéros de page. C'est aussi le pied de page des PDF
        # produits par TRANSLAX (core/pdf_export.py : « 17 / 103 »).
        r"(?:page[ \t]+)?(?P<num>\d{1,4})[ \t]+/[ \t]+(?P<total>\d{1,4})",
        inline=True,
        examples=("17 / 103", "Page 17 / 103"),
    ),
    PageMarker(
        "N sur M",
        r"(?:page[ \t]+)?(?P<num>\d{1,4})[ \t]+sur[ \t]+(?P<total>\d{1,4})",
        inline=True,
        examples=("17 sur 103", "Page 17 sur 103"),
    ),
    PageMarker(
        "Seite N von M",
        r"seite[ \t]+(?P<num>\d{1,4})[ \t]+von[ \t]+(?P<total>\d{1,4})",
        inline=True,
        examples=("Seite 17 von 103",),
    ),
    PageMarker(
        "Página N de M",
        r"p[áa]g(?:ina|\.)[ \t]*(?P<num>\d{1,4})[ \t]+de[ \t]+(?P<total>\d{1,4})",
        inline=True,
        examples=("Página 17 de 103", "Pág. 17 de 103"),
    ),
    PageMarker(
        "Pagina N di M",
        r"pagina[ \t]+(?P<num>\d{1,4})[ \t]+di[ \t]+(?P<total>\d{1,4})",
        inline=True,
        examples=("Pagina 17 di 103",),
    ),
    PageMarker(
        "Page N",
        # Sans total : seulement seul sur sa ligne (« voir page 17 » reste).
        r"(?:page|p\.|seite|p[áa]gina)[ \t]*(?P<num>\d{1,4})",
        inline=False,
        examples=("Page 17", "p. 17", "Seite 17", "Página 17"),
    ),
    PageMarker(
        "- N -",
        r"[-–—][ \t]*(?P<num>\d{1,4})[ \t]*[-–—]",
        inline=False,
        examples=("- 17 -", "— 17 —"),
    ),
]


def _compile(marker: PageMarker) -> re.Pattern:
    # Jamais un morceau d'un nombre, d'un mot ou d'une date plus longue.
    return re.compile(rf"(?<![\w/.,:]){marker.regex}(?![\w/])", re.IGNORECASE)


_COMPILED = [(marker, _compile(marker)) for marker in BLACKLIST]


@dataclass
class MarkerReport:
    # « N of M (… of 103) » -> nombre retiré
    removed: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.removed.values())

    def summary(self) -> str:
        found = ", ".join(f"« {label} » ×{count}" for label, count in self.removed.items())
        return f"Numéros de page retirés (liste noire) : {self.total} — {found}."


def _line_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    """Début et fin de la ligne -- un saut de page (\\f, texte brut d'un PDF)
    termine une ligne aussi bien qu'un saut de ligne."""
    line_start = max(text.rfind("\n", 0, start), text.rfind("\f", 0, start)) + 1
    ends = [i for i in (text.find("\n", end), text.find("\f", end)) if i != -1]
    return line_start, min(ends) if ends else len(text)


def _remove_line(text: str, start: int, end: int) -> str:
    """Retire la ligne entière ; un saut de page (\\f) n'est jamais retiré."""
    line_start, line_end = _line_bounds(text, start, end)
    if line_end < len(text) and text[line_end] == "\n":
        return text[:line_start] + text[line_end + 1:]
    cut = line_start - 1 if line_start > 0 and text[line_start - 1] == "\n" else line_start
    return text[:cut] + text[line_end:]


def _increasing_ratio(numbers: list[int]) -> float:
    pairs = list(zip(numbers, numbers[1:]))
    return sum(1 for a, b in pairs if b > a) / len(pairs) if pairs else 0.0


def _is_pagination(occurrences: list[tuple[re.Match, bool]], has_total: bool) -> bool:
    numbers = [int(m.group("num")) for m, _ in occurrences]
    distinct = len(set(numbers))
    if not has_total:
        return distinct >= MIN_DISTINCT_NO_TOTAL
    if any(alone for _, alone in occurrences):
        return distinct >= MIN_DISTINCT_STANDALONE
    return distinct >= MIN_DISTINCT_INLINE and _increasing_ratio(numbers) >= MIN_INCREASING


def _is_standalone(text: str, start: int, end: int) -> bool:
    """Seul sur sa ligne -- un « ## » ou « > » Markdown devant compris."""
    line_start, line_end = _line_bounds(text, start, end)
    before = text[line_start:start]
    after = text[end:line_end]
    return re.fullmatch(r"[ \t]*(?:#{1,6}[ \t]*|>[ \t]*)?", before) is not None and not after.strip()


def _label(marker: PageMarker, total: str | None) -> str:
    """« N of M » + total 103 -> « N of 103 »."""
    if total is None:
        return marker.name
    return re.sub(r"\bM\b(?!.*\bM\b)", total, marker.name)


def strip_markers(text: str) -> tuple[str, MarkerReport]:
    """Retire les marqueurs de la liste noire qui reviennent assez souvent
    (voir le docstring du module). Retourne (texte, rapport)."""
    report = MarkerReport()
    for marker, pattern in _COMPILED:
        groups: dict[str | None, list[tuple[re.Match, bool]]] = {}
        for match in pattern.finditer(text):
            standalone = _is_standalone(text, match.start(), match.end())
            if not marker.inline and not standalone:
                continue
            total = match.groupdict().get("total")
            if total is not None and int(match.group("num")) > int(total):
                continue
            groups.setdefault(total, []).append((match, standalone))

        doomed: list[tuple[re.Match, bool]] = []
        for total, occurrences in groups.items():
            if not _is_pagination(occurrences, has_total=total is not None):
                continue
            doomed.extend(occurrences)
            label = _label(marker, total)
            report.removed[label] = report.removed.get(label, 0) + len(occurrences)

        for match, standalone in sorted(doomed, key=lambda pair: -pair[0].start()):
            if standalone:
                # La ligne entière disparaît : « was\n17 of 103\nimplicated »
                # -> « was\nimplicated », la phrase se retrouve d'un seul tenant.
                text = _remove_line(text, match.start(), match.end())
            else:
                # En pleine phrase : « was 17 of 103 implicated » -> « was implicated ».
                before = text[:match.start()].rstrip(" \t")
                after = text[match.end():].lstrip(" \t")
                glue = " " if before and after and not before.endswith(("\n", "\f")) and not after.startswith(("\n", "\f")) else ""
                text = before + glue + after
    return text, report


def preview(text: str) -> MarkerReport:
    """Ce que `strip_markers` retirerait, sans rien modifier."""
    return strip_markers(text)[1]
