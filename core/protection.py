"""
Protection du texte pour le mode serveur (pré-traduction des textes du site
ajtvirtual.com, voir `core/server.py` et SPEC.md §5 tricies octies).

Les textes arrivent avec la syntaxe du système de traduction du site
(ICU MessageFormat, celle de next-intl) :
  - variables `{name}`, arguments formatés `{n, number}` : recopiés tels quels ;
  - pluriels / select `{count, plural, one {# service} other {# services}}` :
    la structure est gardée, chaque branche est traduite séparément, `#`
    est gardé ;
  - balises sans attribut `<strong>…</strong>`, `<link>…</link>`, et
    auto-fermantes `<twint/>` : gardées, le texte intérieur est traduit
    dans le contexte de la phrase entière ;
  - marques jamais traduites (AJTVIRTUAL, AJT Connect, STRUKTUR…), termes
    `keep_terms` et termes imposés `glossary_entries`.

Méthode : chaque élément protégé est remplacé par un REPÈRE, la phrase est
traduite d'un bloc, puis on vérifie que tous les repères sont revenus, une
seule fois, correctement imbriqués. Le modèle voit les repères dans la
syntaxe d'origine (`{name}`, `<strong>`, marques en clair -- `SourceStyle`,
mesuré mieux recopié que des repères neutres `<x3/>`), sauf les termes
imposés, toujours en repères neutres. En cas d'échec : réparation qui garde
la phrase entière si possible (balise perdue replacée, marque retraduite
masquée, variable renommée reprise), sinon la phrase est retraduite PAR
MORCEAUX entre les repères (contexte plus court, structure garantie) --
voir `core/translation_service.py`, qui orchestre ces passes.

Ce module ne charge aucun modèle : pure manipulation de texte, testée par
`tests/test_server.py` avec un moteur factice.

Représentation interne : un repère est écrit `\\ue000<type><n>\\ue001`
(caractères Unicode à usage privé, jamais présents dans un vrai texte), avec
type = A (élément structurel : variable, balise auto-fermante, `#`,
pluriel entier), T (terme : marque, keep_terms, terme imposé), O/C (balise
ouvrante/fermante d'une paire, même numéro). Ce n'est qu'au moment
d'appeler le modèle que ces repères sont rendus dans un style lisible par
lui (`SOURCE_STYLE`, ou `XML_STYLE`), puis relus depuis sa sortie.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

# Marques de l'écosystème AJTVIRTUAL : jamais traduites, quelle que soit la
# langue cible (demande explicite de l'utilisateur). Comparaison sensible à
# la casse : « Struktur » (mot allemand courant) n'est pas la marque.
# AJTOOL (le hub AJTASK + STRUKTUR de l'app) ajouté après l'essai réel du
# 21/10/2026, où MADLAD-400 le laissait passer sans protection.
DEFAULT_BRANDS: tuple[str, ...] = (
    "AJTVIRTUAL",
    "AJT Connect",
    "STRUKTUR",
    "AJTASK",
    "AJTOOL",
    "TWINT",
    "App Store",
    "Google Play",
)

# --------------------------------------------------------------------------
# 1. Analyse de la syntaxe ICU MessageFormat + balises


class IcuSyntaxError(ValueError):
    """Le texte ne suit pas la syntaxe attendue (accolade ou balise orpheline...)."""


@dataclass
class Text:
    value: str


@dataclass
class Placeholder:
    """`{name}`, `{n, number}`, littéral ICU entre apostrophes : recopié tel quel."""
    raw: str


@dataclass
class Pound:
    """Le `#` d'une branche de pluriel (remplacé par le nombre à l'affichage)."""
    raw: str = "#"


@dataclass
class EmptyTag:
    """Balise auto-fermante `<twint/>`, `<br/>`."""
    raw: str
    name: str


@dataclass
class Tag:
    """Paire `<strong>…</strong>` : le contenu est un message à part entière."""
    name: str
    open_raw: str
    close_raw: str
    children: list


@dataclass
class Branch:
    prefix: str  # tout ce qui précède l'accolade de la branche, espaces compris : " one ", " =0 "
    children: list


@dataclass
class Choice:
    """`{count, plural, …}`, `{x, select, …}`, `{n, selectordinal, …}`."""
    head: str     # "{count, plural," -- recopié tel quel
    name: str
    kind: str
    branches: list
    tail: str     # espaces éventuels + "}" final


CHOICE_KINDS = ("plural", "select", "selectordinal")

_TAG_OPEN_RE = re.compile(r"<([A-Za-z][\w.:-]*)((?:\s+[^<>]*?)?)\s*(/?)>")
_TAG_CLOSE_RE = re.compile(r"</([A-Za-z][\w.:-]*)\s*>")
_ARG_NAME_RE = re.compile(r"\s*([^\s{}<>#,'\"]+)\s*")
_ARG_TYPE_RE = re.compile(r"\s*([A-Za-z]+)\s*")
_BRANCH_PREFIX_RE = re.compile(r"\s*(?:offset:\s*\d+\s+)?(?:=\s*-?\d+(?:\.\d+)?|[A-Za-z_][\w-]*)\s*")


def parse_message(text: str) -> list:
    """Analyse un texte ICU. Lève `IcuSyntaxError` si la syntaxe est invalide."""
    parser = _Parser(text)
    nodes = parser.message(pound=False, closing_tag=None, in_branch=False)
    if parser.pos != len(text):  # pragma: no cover - garde-fou, message() consomme tout ou lève
        raise IcuSyntaxError(f"texte non analysé à partir de la position {parser.pos}")
    return nodes


class _Parser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0

    def message(self, pound: bool, closing_tag: str | None, in_branch: bool) -> list:
        text = self.text
        nodes: list = []
        buf: list[str] = []

        def flush() -> None:
            if buf:
                nodes.append(Text("".join(buf)))
                buf.clear()

        while self.pos < len(text):
            char = text[self.pos]
            if char == "{":
                flush()
                nodes.append(self.argument(pound))
                continue
            if char == "}":
                if in_branch:
                    break
                raise IcuSyntaxError(f"accolade fermante inattendue (position {self.pos})")
            if char == "#" and pound:
                flush()
                nodes.append(Pound())
                self.pos += 1
                continue
            if char == "<":
                close = _TAG_CLOSE_RE.match(text, self.pos)
                if close:
                    if close.group(1) == closing_tag:
                        break
                    raise IcuSyntaxError(f"balise fermante </{close.group(1)}> sans balise ouvrante")
                opening = _TAG_OPEN_RE.match(text, self.pos)
                if opening:
                    flush()
                    if opening.group(3):
                        nodes.append(EmptyTag(opening.group(0), opening.group(1)))
                        self.pos = opening.end()
                        continue
                    self.pos = opening.end()
                    children = self.message(pound, opening.group(1), in_branch)
                    close = _TAG_CLOSE_RE.match(text, self.pos)
                    if not close or close.group(1) != opening.group(1):
                        raise IcuSyntaxError(f"balise <{opening.group(1)}> jamais fermée")
                    self.pos = close.end()
                    nodes.append(Tag(opening.group(1), opening.group(0), close.group(0), children))
                    continue
            if char == "'":
                nxt = text[self.pos + 1] if self.pos + 1 < len(text) else ""
                if nxt == "'":
                    buf.append("''")
                    self.pos += 2
                    continue
                if nxt and (nxt in "{}<" or (nxt == "#" and pound)):
                    # Littéral ICU entre apostrophes ('{' affiche une accolade) :
                    # recopié tel quel, jamais montré au modèle.
                    end = self._quoted_end(self.pos + 1)
                    flush()
                    nodes.append(Placeholder(text[self.pos:end]))
                    self.pos = end
                    continue
            buf.append(char)
            self.pos += 1
        flush()
        return nodes

    def _quoted_end(self, index: int) -> int:
        text = self.text
        while index < len(text):
            if text[index] == "'":
                if index + 1 < len(text) and text[index + 1] == "'":
                    index += 2
                    continue
                return index + 1
            index += 1
        return len(text)

    def argument(self, pound: bool):
        text = self.text
        start = self.pos
        self.pos += 1
        name = _ARG_NAME_RE.match(text, self.pos)
        if not name:
            raise IcuSyntaxError(f"nom d'argument attendu (position {self.pos})")
        self.pos = name.end()
        if self.pos < len(text) and text[self.pos] == "}":
            self.pos += 1
            return Placeholder(text[start:self.pos])
        if self.pos >= len(text) or text[self.pos] != ",":
            raise IcuSyntaxError(f"argument {{{name.group(1)}}} mal formé")
        self.pos += 1
        kind_match = _ARG_TYPE_RE.match(text, self.pos)
        if not kind_match:
            raise IcuSyntaxError(f"type d'argument attendu après {{{name.group(1)},")
        kind = kind_match.group(1)
        self.pos = kind_match.end()

        if kind in CHOICE_KINDS:
            if self.pos >= len(text) or text[self.pos] != ",":
                raise IcuSyntaxError(f"{kind} sans branches")
            self.pos += 1
            head = text[start:self.pos]
            branch_pound = pound or kind != "select"
            branches: list[Branch] = []
            while True:
                segment_start = self.pos
                while self.pos < len(text) and text[self.pos].isspace():
                    self.pos += 1
                if self.pos >= len(text):
                    raise IcuSyntaxError(f"{kind} jamais fermé")
                if text[self.pos] == "}":
                    self.pos += 1
                    tail = text[segment_start:self.pos]
                    break
                brace = text.find("{", self.pos)
                if brace < 0:
                    raise IcuSyntaxError(f"branche de {kind} sans accolade")
                prefix = text[segment_start:brace]
                if not _BRANCH_PREFIX_RE.fullmatch(prefix):
                    raise IcuSyntaxError(f"sélecteur ICU invalide : {prefix.strip()!r}")
                self.pos = brace + 1
                children = self.message(branch_pound, None, True)
                if self.pos >= len(text) or text[self.pos] != "}":
                    raise IcuSyntaxError(f"branche {prefix.strip()!r} jamais fermée")
                self.pos += 1
                branches.append(Branch(prefix, children))
            if not branches:
                raise IcuSyntaxError(f"{kind} sans branche")
            return Choice(head, name.group(1), kind, branches, tail)

        # Argument formaté simple ({n, number}, {d, date, short},
        # {n, number, ::currency/CHF}) : recopié tel quel jusqu'à l'accolade
        # fermante correspondante.
        depth = 1
        while self.pos < len(text) and depth:
            if text[self.pos] == "{":
                depth += 1
            elif text[self.pos] == "}":
                depth -= 1
            self.pos += 1
        if depth:
            raise IcuSyntaxError(f"argument {{{name.group(1)}, {kind}…}} jamais fermé")
        return Placeholder(text[start:self.pos])


_LENIENT_RE = re.compile(r"\{[^{}]*\}|</?[A-Za-z][\w.:-]*(?:\s+[^<>]*?)?\s*/?>")


def parse_lenient(text: str) -> list:
    """
    Repli quand `parse_message` échoue (texte qui n'est pas de l'ICU valide) :
    chaque `{…}` simple et chaque balise isolée devient un élément recopié tel
    quel, le reste est du texte. Aucune structure (paires, pluriels) n'est
    reconnue -- le service ajoute un avertissement dans ce cas.
    """
    nodes: list = []
    last = 0
    for match in _LENIENT_RE.finditer(text):
        if match.start() > last:
            nodes.append(Text(text[last:match.start()]))
        nodes.append(Placeholder(match.group(0)))
        last = match.end()
    if last < len(text):
        nodes.append(Text(text[last:]))
    return nodes


def render_message(nodes: list) -> str:
    """Inverse exact de `parse_message` : render_message(parse_message(s)) == s."""
    out: list[str] = []
    for node in nodes:
        if isinstance(node, Text):
            out.append(node.value)
        elif isinstance(node, (Placeholder, Pound, EmptyTag)):
            out.append(node.raw)
        elif isinstance(node, Tag):
            out.append(node.open_raw + render_message(node.children) + node.close_raw)
        elif isinstance(node, Choice):
            out.append(node.head)
            for branch in node.branches:
                out.append(branch.prefix + "{" + render_message(branch.children) + "}")
            out.append(node.tail)
        else:  # pragma: no cover - garde-fou
            raise TypeError(f"élément inconnu : {node!r}")
    return "".join(out)


def structure_signature(nodes: list) -> Counter:
    """
    Empreinte de la structure d'un message (variables, balises, pluriels, `#`,
    avec leur imbrication), indépendante de l'ordre des mots : deux messages
    de même empreinte ont exactement les mêmes éléments protégés aux mêmes
    niveaux. Sert au contrôle final source/traduction.
    """
    signature: Counter = Counter()

    def walk(items: list, path: tuple) -> None:
        for node in items:
            if isinstance(node, Placeholder):
                signature[(path, "var", node.raw)] += 1
            elif isinstance(node, Pound):
                signature[(path, "#")] += 1
            elif isinstance(node, EmptyTag):
                signature[(path, "tag/", node.raw)] += 1
            elif isinstance(node, Tag):
                signature[(path, "tag", node.open_raw, node.close_raw)] += 1
                walk(node.children, path + (node.open_raw,))
            elif isinstance(node, Choice):
                selectors = tuple(branch.prefix.strip() for branch in node.branches)
                signature[(path, "choice", node.head, selectors)] += 1
                for branch in node.branches:
                    walk(branch.children, path + (node.head, branch.prefix.strip()))

    walk(nodes, ())
    return signature


def describe_signature_difference(source: Counter, output: Counter) -> str:
    """Résumé lisible d'un écart d'empreinte, pour le champ `warnings`."""
    missing = source - output
    extra = output - source

    def label(key: tuple) -> str:
        kind = key[1]
        if kind == "var":
            return key[2]
        if kind == "#":
            return "#"
        if kind == "tag/":
            return key[2]
        if kind == "tag":
            return key[2] + "…" + key[3]
        return key[2] + "…}"

    parts = []
    if missing:
        parts.append("manquant : " + ", ".join(sorted({label(k) for k in missing})))
    if extra:
        parts.append("en trop ou déplacé : " + ", ".join(sorted({label(k) for k in extra})))
    return " ; ".join(parts) or "structure différente"


# --------------------------------------------------------------------------
# 2. Termes protégés : marques, keep_terms, termes imposés


@dataclass(frozen=True)
class Term:
    source: str          # tel que cherché dans le texte français
    target: str          # tel que réinséré après traduction (marque : identique)
    kind: str            # "brand" | "keep" | "glossary"
    case_sensitive: bool


class TermSet:
    """
    Repère les termes protégés dans un texte. Les marques et `keep_terms`
    sont cherchés à la casse près ; les termes imposés (`glossary_entries`)
    sans tenir compte de la casse (« Devis » en début de phrase = « devis »).
    Toujours des mots entiers ; le plus long gagne (« AJT Connect » avant un
    éventuel « Connect »).
    """

    def __init__(
        self,
        *,
        brands: tuple[str, ...] | list[str] = DEFAULT_BRANDS,
        keep_terms: tuple[str, ...] | list[str] = (),
        glossary: dict[str, str] | None = None,
    ) -> None:
        terms: list[Term] = []
        seen: set[str] = set()

        def add(term: Term) -> None:
            key = term.source if term.case_sensitive else term.source.lower()
            if term.source and key not in seen:
                seen.add(key)
                terms.append(term)

        for brand in brands:
            add(Term(brand, brand, "brand", True))
        for keep in keep_terms:
            keep = (keep or "").strip()
            if keep:
                add(Term(keep, keep, "keep", True))
        for source, target in (glossary or {}).items():
            source = (source or "").strip()
            target = (target or "").strip()
            if source and target:
                add(Term(source, target, "glossary", False))

        terms.sort(key=lambda t: len(t.source), reverse=True)
        self.terms = terms
        if terms:
            alternatives = []
            for index, term in enumerate(terms):
                body = re.escape(term.source)
                if not term.case_sensitive:
                    body = f"(?i:{body})"
                alternatives.append(f"(?P<t{index}>{body})")
            self._regex = re.compile(r"(?<!\w)(?:" + "|".join(alternatives) + r")(?!\w)")
        else:
            self._regex = None

    def split(self, text: str) -> list:
        """Découpe `text` en morceaux : chaînes et tuples (Term, texte trouvé)."""
        if self._regex is None or not text:
            return [text] if text else []
        pieces: list = []
        last = 0
        for match in self._regex.finditer(text):
            if match.start() > last:
                pieces.append(text[last:match.start()])
            term = self.terms[int(match.lastgroup[1:])]
            pieces.append((term, match.group(0)))
            last = match.end()
        if last < len(text):
            pieces.append(text[last:])
        return pieces


# --------------------------------------------------------------------------
# 3. Masquage : message -> texte à repères


MARK_START = "\ue000"
MARK_END = "\ue001"
MARKER_RE = re.compile("\ue000([AOCT])(\\d+)\ue001")
_STRUCTURAL_MARKER_RE = re.compile("(\ue000[AOC]\\d+\ue001)")


def marker(kind: str, index: int) -> str:
    return f"{MARK_START}{kind}{index}{MARK_END}"


@dataclass
class Marker:
    kind: str                 # "A", "T" ou "P" (paire O/C)
    index: int
    raw: str                  # texte source exact (P : balise ouvrante)
    close_raw: str = ""       # P : balise fermante
    replacement: str = ""     # T : texte réinséré (terme imposé, ou la marque elle-même)
    term_kind: str | None = None
    choice: Choice | None = None


@dataclass
class MaskedUnit:
    """Un message (ou une branche de pluriel) prêt à traduire."""
    canonical: str
    markers: dict[int, Marker] = field(default_factory=dict)

    def kinds(self) -> dict[int, str]:
        return {index: mk.kind for index, mk in self.markers.items()}


# Espaces typographiques du français (insécable, fine insécable, fine) :
# ramenés à une espace simple avant traduction -- le modèle ne les distingue
# pas, et la typographie de la langue cible est refaite après coup (voir
# `normalize_typography`).
_FRENCH_SPACES = str.maketrans({"\u00a0": " ", "\u202f": " ", "\u2009": " ", "\u2011": "-", "\u00ad": None})


def mask_nodes(nodes: list, terms: TermSet) -> MaskedUnit:
    """
    Remplace chaque élément protégé par un repère. Les `Choice` (pluriels)
    deviennent UN repère A : leurs branches sont traduites à part (voir
    `core/translation_service.py`) puis réinsérées à la place de ce repère.
    """
    unit = MaskedUnit("")
    parts: list[str] = []
    counter = [0]

    def new(kind: str, raw: str, **extra) -> Marker:
        counter[0] += 1
        mk = Marker(kind=kind, index=counter[0], raw=raw, **extra)
        unit.markers[mk.index] = mk
        return mk

    def text(value: str) -> None:
        value = value.translate(_FRENCH_SPACES).replace(MARK_START, "").replace(MARK_END, "")
        for piece in terms.split(value):
            if isinstance(piece, str):
                parts.append(piece)
            else:
                term, found = piece
                mk = new("T", found, replacement=term.target, term_kind=term.kind)
                parts.append(marker("T", mk.index))

    def walk(items: list) -> None:
        for node in items:
            if isinstance(node, Text):
                text(node.value)
            elif isinstance(node, (Placeholder, Pound, EmptyTag)):
                parts.append(marker("A", new("A", node.raw).index))
            elif isinstance(node, Choice):
                parts.append(marker("A", new("A", render_message([node]), choice=node).index))
            elif isinstance(node, Tag):
                mk = new("P", node.open_raw, close_raw=node.close_raw)
                parts.append(marker("O", mk.index))
                walk(node.children)
                parts.append(marker("C", mk.index))
            else:  # pragma: no cover - garde-fou
                raise TypeError(f"élément inconnu : {node!r}")

    walk(nodes)
    unit.canonical = "".join(parts)
    return unit


def has_letters(canonical: str) -> bool:
    """Vrai s'il reste quelque chose à traduire une fois les repères retirés."""
    return re.search(r"[^\W\d_]", MARKER_RE.sub("", canonical)) is not None


# --------------------------------------------------------------------------
# 4. Découpage en phrases


# Abréviations françaises courantes : un point après elles ne termine pas la
# phrase (« M. Dupont », « p. ex. »).
_ABBREVIATIONS = {
    "m", "mm", "mme", "mmes", "mlle", "dr", "pr", "st", "ste", "etc", "cf", "env",
    "ex", "no", "n°", "art", "al", "vol", "chap", "fig", "réf", "tél", "av", "bd",
    "min", "max", "approx", "resp", "vs",
}
# Fin de phrase : ponctuation, éventuellement suivie de guillemets/parenthèses
# fermants ou de balises fermantes (« … reçu.</strong> Suite »), puis espace.
_BOUNDARY_RE = re.compile("[.!?…]+(?:[»\"”’)\\]]|\ue000C\\d+\ue001)*(\\s+)")
_NEXT_STARTS_SENTENCE_RE = re.compile(r"[\ue000«\"“„(¿¡\d]|[^\W\d_]")


def split_sentences(canonical: str) -> list[str]:
    """
    Découpe un texte à repères en phrases, en gardant les séparateurs :
    renvoie [avant, phrase1, sep1, phrase2, …, phraseN, après] (longueur
    impaire, les phrases aux positions impaires). Ne coupe jamais à
    l'intérieur d'une paire de balises, ni après une abréviation, ni avant
    une minuscule. Les sauts de ligne coupent toujours (hors balise).

    Traduire phrase par phrase : les modèles de traduction sont entraînés
    sur des phrases ; un long paragraphe envoyé d'un bloc se fait parfois
    tronquer (phrase finale oubliée) -- et les lots de phrases courtes vont
    plus vite.
    """
    lead_match = re.match(r"\s*", canonical)
    lead = lead_match.group(0)
    body_end = len(canonical.rstrip())
    trail = canonical[body_end:]
    body = canonical[len(lead):body_end]
    if not body:
        return [canonical]

    cuts: list[tuple[int, int]] = []

    def pairs_open_before(position: int) -> bool:
        opened: set[str] = set()
        for match in MARKER_RE.finditer(body, 0, position):
            if match.group(1) == "O":
                opened.add(match.group(2))
            elif match.group(1) == "C":
                opened.discard(match.group(2))
        return bool(opened)

    for match in re.finditer(r"[ \t]*\n\s*", body):
        if not pairs_open_before(match.start()):
            cuts.append((match.start(), match.end()))

    for match in _BOUNDARY_RE.finditer(body):
        ws_start, ws_end = match.span(1)
        if "\n" in match.group(1) or ws_end >= len(body):
            continue
        following = body[ws_end:]
        if not _NEXT_STARTS_SENTENCE_RE.match(following):
            continue
        first = following[0]
        if first.isalpha() and first.islower():
            continue
        before = MARKER_RE.sub(" ", body[:match.start()])
        last_word = re.search(r"([\w°]+)\W*$", before)
        if last_word and match.group(0).startswith("."):
            word = last_word.group(1).lower()
            if word in _ABBREVIATIONS or (len(word) == 1 and word.isalpha()):
                continue
        if pairs_open_before(ws_start):
            continue
        cuts.append((ws_start, ws_end))

    cuts.sort()
    result = [lead]
    position = 0
    for start, end in cuts:
        if start < position:
            continue
        result.append(body[position:start])
        result.append(body[start:end])
        position = end
    result.append(body[position:])
    result.append(trail)
    # Fusionne d'éventuelles phrases vides (deux coupures collées).
    merged = [result[0]]
    for index in range(1, len(result) - 1, 2):
        sentence, separator = result[index], result[index + 1]
        if not sentence and len(merged) > 1:
            merged[-1] += separator
        else:
            merged.extend([sentence, separator])
    if len(merged) % 2 == 0:  # pragma: no cover - garde-fou
        merged.append("")
    return merged


# --------------------------------------------------------------------------
# 5. Repères tels que le modèle les voit


class MarkerStyle:
    """
    Repères neutres : `<x3/>` pour un élément isolé, `<g2>…</g2>` pour une
    paire (forme XLIFF des chaînes de logiciels localisés). La relecture
    tolère les variantes que le modèle produit parfois (espaces dans la
    balise, majuscule, barre oblique oubliée).

    Mesuré sur 80 phrases réelles du site (SPEC.md §5 tricies octies) :
    MADLAD-400 en perd davantage que la syntaxe d'origine (`SourceStyle`,
    le style par défaut) -- 6 échecs contre 2 en allemand, 5 contre 0 en
    anglais. Gardé pour les éléments sans forme d'origine simple (balise
    avec attributs, littéral ICU, terme imposé) et comme style possible.
    """

    name = "xml"
    _PARSE_RE = re.compile(r"<\s*(/?)\s*([xXgG])\s*(\d+)\s*(/?)\s*>")

    def render(self, canonical: str, unit: "MaskedUnit | None" = None) -> str:
        def repl(match: re.Match) -> str:
            kind, index = match.group(1), match.group(2)
            if kind in "AT":
                return f"<x{index}/>"
            if kind == "O":
                return f"<g{index}>"
            return f"</g{index}>"

        return MARKER_RE.sub(repl, canonical)

    def parse(self, text: str, source: str, unit: "MaskedUnit") -> str:
        """Ramène la sortie du modèle à la forme interne. Un repère inconnu
        (numéro inexistant, mauvais type) est laissé tel quel : le contrôle
        le signalera."""
        return self.parse_kinds(text, unit.kinds())

    def parse_kinds(self, text: str, kinds: dict[int, str]) -> str:
        def repl(match: re.Match) -> str:
            lead_slash, letter, number, end_slash = match.groups()
            index = int(number)
            kind = kinds.get(index)
            if letter in "xX":
                if kind in ("A", "T") and not lead_slash:
                    return marker(kind, index)
                return match.group(0)
            if kind != "P" or end_slash:
                return match.group(0)
            return marker("C" if lead_slash else "O", index)

        text = text.replace(MARK_START, "").replace(MARK_END, "")
        return self._PARSE_RE.sub(repl, text)


XML_STYLE = MarkerStyle()

_SIMPLE_OPEN_TAG_RE = re.compile(r"<[A-Za-z][\w.:-]*>")
_SIMPLE_EMPTY_TAG_RE = re.compile(r"<[A-Za-z][\w.:-]*\s*/>")
_ARGUMENT_NAME_RE = re.compile(r"\{\s*([^\s{}<>#,'\"]+)\s*(?:,[^{}]*)?\}")


class SourceStyle(MarkerStyle):
    """
    Style par défaut : le modèle voit la syntaxe D'ORIGINE -- `{name}`,
    `<strong>…</strong>`, `<twint/>`, `#`, un pluriel sous la forme
    `{count}`, les marques et `keep_terms` en clair. Le modèle a vu ces
    formes dans ses données (HTML, chaînes de logiciels) et en tire du
    contexte (`{amount}` est un montant, `{email}` une adresse) : mesuré, il
    les recopie mieux que des repères neutres (voir `MarkerStyle`).

    Seuls restent en repères neutres `<x3/>` : les termes imposés (le terme
    français ne doit pas être montré, le terme cible est réinséré après
    coup), les balises avec attributs et les littéraux ICU. Avec
    `terms_masked=True` (nouvel essai, quand le modèle a traduit une marque
    montrée en clair -- vu : « STRUKTUR » devenu « STRUTTURA » en italien),
    les marques et `keep_terms` passent aussi en repères neutres.

    Relecture : chaque forme d'origine est retrouvée dans la sortie, dans
    l'ordre de la source (deux `<strong>` : le premier trouvé est le
    premier de la source) ; une marque est retrouvée sans tenir compte de
    la casse (« Struktur » redevient « STRUKTUR » au démasquage).
    """

    name = "source"

    def __init__(self, terms_masked: bool = False) -> None:
        self.terms_masked = terms_masked

    def rendering(self, kind: str, index: int, unit: "MaskedUnit") -> tuple[str, str]:
        """(texte montré au modèle, mode de relecture : exact | word | xml)."""
        mk = unit.markers[index]
        if kind in "OC":
            if _SIMPLE_OPEN_TAG_RE.fullmatch(mk.raw):
                return (mk.raw if kind == "O" else mk.close_raw), "exact"
            return (f"<g{index}>" if kind == "O" else f"</g{index}>"), "xml"
        if kind == "A":
            if mk.choice is not None:
                return "{" + mk.choice.name + "}", "exact"
            if mk.raw == "#" or _SIMPLE_EMPTY_TAG_RE.fullmatch(mk.raw):
                return mk.raw, "exact"
            argument = _ARGUMENT_NAME_RE.fullmatch(mk.raw)
            if argument:
                return "{" + argument.group(1) + "}", "exact"
            return f"<x{index}/>", "xml"
        if mk.term_kind == "glossary" or self.terms_masked:
            return f"<x{index}/>", "xml"
        return mk.raw, "word"

    def render(self, canonical: str, unit: "MaskedUnit | None" = None) -> str:
        return MARKER_RE.sub(lambda m: self.rendering(m.group(1), int(m.group(2)), unit)[0], canonical)

    def parse(self, text: str, source: str, unit: "MaskedUnit") -> str:
        text = text.replace(MARK_START, "").replace(MARK_END, "")
        queues: dict[tuple[str, str], list[str]] = {}
        xml_kinds: dict[int, str] = {}
        for match in MARKER_RE.finditer(source):
            kind, index = match.group(1), int(match.group(2))
            shown, mode = self.rendering(kind, index, unit)
            if mode == "xml":
                xml_kinds[index] = unit.markers[index].kind
            else:
                queues.setdefault((shown, mode), []).append(marker(kind, index))
        if xml_kinds:
            text = self.parse_kinds(text, xml_kinds)
        # Deux passes : d'abord la forme exacte (casse comprise), puis, pour
        # les marques pas encore retrouvées, sans tenir compte de la casse --
        # « Die Struktur von STRUKTUR » garde le mot allemand intact.
        for ignore_case in (False, True):
            keys = [key for key in sorted(queues, key=lambda k: len(k[0]), reverse=True)
                    if queues[key] and (not ignore_case or key[1] == "word")]
            if not keys:
                break
            alternatives = []
            for position, (shown, mode) in enumerate(keys):
                body = re.escape(shown)
                if mode == "word":
                    body = rf"(?<!\w){body}(?!\w)"
                    if ignore_case:
                        body = f"(?i:{body})"
                alternatives.append(f"(?P<q{position}>{body})")
            regex = re.compile("|".join(alternatives))

            def repl(match: re.Match, keys=keys) -> str:
                queue = queues[keys[int(match.lastgroup[1:])]]
                return queue.pop(0) if queue else match.group(0)

            text = regex.sub(repl, text)

        # Variable renommée par le modèle (« {number} » devenu « {Zahl} »,
        # vu sur les libellés courts) : reprise seulement si c'est sans
        # ambiguïté -- UNE variable manquante, UNE accolade inconnue. Avec
        # deux, l'ordre a pu changer : jamais deviné, le repli s'en charge.
        missing = [token for (shown, _mode), queue in queues.items() if shown.startswith("{") for token in queue]
        unknown = list(re.finditer("\\{[^{}<>\ue000\ue001]*\\}", text))
        if len(missing) == 1 and len(unknown) == 1:
            found = unknown[0]
            text = text[:found.start()] + missing[0] + text[found.end():]
        return text


SOURCE_STYLE = SourceStyle()
SOURCE_STYLE_MASKED_TERMS = SourceStyle(terms_masked=True)


# --------------------------------------------------------------------------
# 6. Contrôle des repères


def marker_problems(source: str, output: str) -> list[str]:
    """
    Liste vide si `output` contient exactement les repères de `source`, une
    seule fois chacun, balises correctement imbriquées, chaque élément dans
    la même balise qu'à la source. Sinon, la liste des problèmes trouvés.
    """
    problems: list[str] = []
    src = [(m.group(1), int(m.group(2))) for m in MARKER_RE.finditer(source)]
    out = [(m.group(1), int(m.group(2))) for m in MARKER_RE.finditer(output)]
    src_count, out_count = Counter(src), Counter(out)
    if src_count != out_count:
        missing = src_count - out_count
        extra = out_count - src_count
        if missing:
            problems.append(f"{sum(missing.values())} repère(s) perdu(s)")
        if extra:
            problems.append(f"{sum(extra.values())} repère(s) en double ou inconnu(s)")
        return problems
    if re.search(r"</?\s*[xXgG]\s*\d+\s*/?>", MARKER_RE.sub("", output)):
        problems.append("repère mal formé")
        return problems

    def parents(sequence: list[tuple[str, int]]) -> dict | None:
        stack: list[int] = []
        result: dict = {}
        for kind, index in sequence:
            if kind == "O":
                result[("P", index)] = stack[-1] if stack else None
                stack.append(index)
            elif kind == "C":
                if not stack or stack[-1] != index:
                    return None
                stack.pop()
            else:
                result[(kind, index)] = stack[-1] if stack else None
        return result if not stack else None

    src_parents, out_parents = parents(src), parents(out)
    if out_parents is None:
        problems.append("balises croisées ou fermées avant d'être ouvertes")
    elif src_parents != out_parents:
        problems.append("élément sorti de sa balise")
    return problems


def lost_pairs(source: str, output: str) -> list[int]:
    """
    Numéros des balises dont le modèle a perdu l'ouvrante ET la fermante,
    quand c'est le SEUL défaut de la sortie (rien en trop, tout le reste
    présent). Cas typique, mesuré : le modèle réorganise la phrase et
    abandonne un `<strong>` -- réparable sans perdre le contexte (voir
    `wrap_span` et `translation_service._repair_pass`).
    """
    src = Counter((m.group(1), int(m.group(2))) for m in MARKER_RE.finditer(source))
    out = Counter((m.group(1), int(m.group(2))) for m in MARKER_RE.finditer(output))
    if out - src:
        return []
    missing = src - out
    if not missing or any(kind not in "OC" for kind, _index in missing):
        return []
    pairs = sorted({index for _kind, index in missing})
    if any(missing[("O", i)] != 1 or missing[("C", i)] != 1 for i in pairs):
        return []
    return pairs


def lost_terms_only(source: str, output: str) -> bool:
    """Vrai si le seul défaut de la sortie est un terme protégé (marque,
    keep_terms, terme imposé) disparu -- tout le reste est là, rien en trop."""
    src = Counter((m.group(1), int(m.group(2))) for m in MARKER_RE.finditer(source))
    out = Counter((m.group(1), int(m.group(2))) for m in MARKER_RE.finditer(output))
    missing = src - out
    return bool(missing) and not (out - src) and all(kind == "T" for kind, _index in missing)


def lost_pound_only(source: str, output: str, unit: "MaskedUnit") -> int | None:
    """Numéro du repère `#` si c'est le SEUL élément perdu (vu : « # chapitres »
    compris comme « nombre de chapitres » -> « Anzahl der Kapitel »)."""
    src = Counter((m.group(1), int(m.group(2))) for m in MARKER_RE.finditer(source))
    out = Counter((m.group(1), int(m.group(2))) for m in MARKER_RE.finditer(output))
    missing = src - out
    if out - src or len(missing) != 1:
        return None
    (kind, index), count = next(iter(missing.items()))
    if kind == "A" and count == 1 and unit.markers[index].raw == "#":
        return index
    return None


def pound_number(selector: str, text: str) -> str | None:
    """
    Nombre à montrer au modèle à la place de `#` dans une branche de pluriel,
    pour un second essai : 1 pour `one`, la valeur de `=N`, 12 pour `other`
    (pas 5 : « 5 étoiles » devient « 5-Sterne-Hotel », vu). Jamais un nombre
    déjà présent dans la phrase. None si aucun ne convient.
    """
    last = selector.split()[-1] if selector.split() else ""
    base = {"one": "1", "zero": "0", "two": "2", "few": "3"}.get(last)
    if last.startswith("="):
        base = last[1:].strip()
    for number in ([base] if base else []) + ["12", "37"]:
        if not re.search(rf"(?<!\d){re.escape(number)}(?!\d)", MARKER_RE.sub(" ", text)):
            return number
    return None


def pair_content(source: str, index: int) -> str:
    """Le texte (forme interne) entre la balise ouvrante et la fermante n°`index`."""
    start = source.index(marker("O", index)) + len(marker("O", index))
    return source[start:source.index(marker("C", index))]


def wrap_span(output: str, needle: str, index: int) -> str | None:
    """
    Entoure de la paire n°`index` l'unique occurrence de `needle` (le
    contenu de la balise, traduit seul) dans `output`. None si ce texte
    n'y figure pas exactement une fois -- casse ignorée en second essai,
    ponctuation finale ajoutée par le modèle ignorée.
    """
    needle = needle.strip()
    candidates = [needle]
    trimmed = needle.rstrip(".!?…:;,").rstrip()
    if trimmed and trimmed != needle:
        candidates.append(trimmed)
    for candidate in candidates:
        if not candidate:
            continue
        for flags in (0, re.IGNORECASE):
            found = [m.start() for m in re.finditer(re.escape(candidate), output, flags)]
            if len(found) == 1:
                start, end = found[0], found[0] + len(candidate)
                return output[:start] + marker("O", index) + output[start:end] + marker("C", index) + output[end:]
    return None


_WORD_RE = re.compile(r"[^\W_]+")


def wrap_span_fuzzy(output: str, needle: str, index: int) -> str | None:
    """
    Second essai de `wrap_span` quand le contenu traduit seul ne se retrouve
    pas mot pour mot (« Feste vertragliche Verpflichtung » seul, mais
    « verbindliche vertragliche Verpflichtung » dans la phrase) : la plus
    longue suite de mots communs (comparés sur leurs 5 premières lettres,
    pour tolérer les accords) est entourée, si elle est unique et couvre au
    moins la moitié des mots du contenu (et au moins deux mots si le contenu
    en a plusieurs). Le service signale alors la balise comme « replacée par
    rapprochement, à vérifier ».
    """
    def key(word: str) -> str:
        return word.lower()[:5]

    needle_words = [key(m.group()) for m in _WORD_RE.finditer(MARKER_RE.sub(" ", needle))]
    if not needle_words:
        return None
    blanked = MARKER_RE.sub(lambda m: " " * len(m.group()), output)
    words = [(key(m.group()), m.start(), m.end()) for m in _WORD_RE.finditer(blanked)]
    best_length, best_runs = 0, []
    for i in range(len(words)):
        for j in range(len(needle_words)):
            length = 0
            while (i + length < len(words) and j + length < len(needle_words)
                   and words[i + length][0] == needle_words[j + length]):
                length += 1
            if length > best_length:
                best_length, best_runs = length, [(i, i + length - 1)]
            elif length == best_length and length and (i, i + length - 1) not in best_runs:
                best_runs.append((i, i + length - 1))
    if (not best_length or len(best_runs) != 1 or best_length * 2 < len(needle_words)
            or (len(needle_words) >= 2 and best_length < 2)):
        return None
    first, last = best_runs[0]
    start, end = words[first][1], words[last][2]
    if MARKER_RE.search(output[start:end]):
        return None
    return output[:start] + marker("O", index) + output[start:end] + marker("C", index) + output[end:]


# --------------------------------------------------------------------------
# 7. Typographie et retouches de la langue cible


_SPACE_CHARS = "\u00a0\u202f\u2009"


def normalize_typography(text: str, target: str) -> str:
    """
    Typographie de la langue cible (texte source : français) :
      - pas d'espace avant `: ; ? !` en allemand, italien, anglais, portugais
        (le français en met une, souvent insécable) ;
      - guillemets français « … » : sans espaces intérieures en allemand
        (usage suisse), italien et portugais ; “…” en anglais ;
      - allemand : guillemets „…“ ramenés aux guillemets suisses «…».
    Les repères internes ne contiennent aucun de ces signes : ce passage se
    fait avant leur remplacement, sans risque de toucher une variable.
    """
    if target == "fr":
        return text
    for char in _SPACE_CHARS:
        text = text.replace(char, " ")
    text = re.sub(r" +([:;?!])", r"\1", text)
    if target == "en":
        text = re.sub(r"«\s*", "“", text)
        text = re.sub(r"\s*»", "”", text)
    else:
        text = re.sub(r"«\s+", "«", text)
        text = re.sub(r"\s+»", "»", text)
        if target == "de":
            text = re.sub(r"„([^„“”\"]*)[“”]", r"«\1»", text)
    return text


def swiss_german(text: str) -> str:
    """Allemand de Suisse : jamais de ß."""
    return text.replace("ß", "ss").replace("ẞ", "SS")


def _surrounding_space(text: str, position: int, before: bool) -> str:
    if before:
        match = re.search(r"[ \t]*$", text[:position])
        return match.group(0) if match else ""
    match = re.match(r"[ \t]*", text[position:])
    return match.group(0) if match else ""


def align_tag_spacing(output: str, source: str) -> str:
    """
    Reprend les espaces INTÉRIEURES des balises de la source (« <strong> Le
    paiement…</strong> » garde son espace après la balise ouvrante) et évite
    les doubles espaces quand le modèle a déplacé une espace hors de la
    balise. Si la source avait une espace entre un mot et la balise et que
    le modèle les a collés, l'espace est remise.
    """
    for match in MARKER_RE.finditer(source):
        kind, index = match.group(1), match.group(2)
        if kind not in "OC":
            continue
        token = marker(kind, int(index))
        src_before = _surrounding_space(source, match.start(), True)
        src_after = _surrounding_space(source, match.end(), False)
        position = output.find(token)
        if position < 0:
            continue
        out_before = _surrounding_space(output, position, True)
        out_after = _surrounding_space(output, position + len(token), False)
        left = output[:position - len(out_before)] if out_before else output[:position]
        right = output[position + len(token) + len(out_after):]
        if kind == "O":
            new_after = src_after
            if src_before:
                new_before = out_before or (" " if left[-1:].isalnum() else "")
            else:
                new_before = "" if src_after else out_before
        else:
            new_before = src_before
            if src_after:
                new_after = out_after or (" " if right[:1].isalnum() else "")
            else:
                new_after = "" if src_before else out_after
        output = left + new_before + token + new_after + right
    return output


def fix_final_period(output: str, source: str) -> str:
    """Le modèle ajoute volontiers un point final à un libellé qui n'en a pas
    (« Payer plus tard » -> « Pay later. ») : retiré si la source n'en a pas."""
    src = MARKER_RE.sub("", source).rstrip()
    stripped = output.rstrip()
    if src and src[-1] not in ".!?…:;" and stripped.endswith(".") and not stripped.endswith(".."):
        return stripped[:-1] + output[len(stripped):]
    return output


def finish_segment(output: str, source: str, target: str) -> str:
    """Toutes les retouches d'une phrase traduite (forme interne, repères en place)."""
    text = normalize_typography(output, target)
    if target == "de":
        text = swiss_german(text)
    text = fix_final_period(text, source)
    text = align_tag_spacing(text, source)
    text = re.sub(r"(?<=\S) {2,}(?=\S)", " ", text)
    return text


# --------------------------------------------------------------------------
# 8. Démasquage


_SENTENCE_START_RE = re.compile(r"(?:^|[.!?…]\s+)[«\"“„(\[\s]*$")


def unmask(canonical: str, unit: MaskedUnit, choice_texts: dict[int, str] | None = None,
           target: str = "") -> str:
    """
    Remet chaque élément protégé à la place de son repère. `choice_texts` :
    pour un repère qui représente un pluriel entier, le pluriel déjà
    reconstruit avec ses branches traduites.

    Une apostrophe droite collée devant une variable ou une balise
    (« dell'{brand} ») serait lue par ICU comme le début d'un littéral et
    casserait l'affichage : elle devient l'apostrophe typographique ’.
    """
    choice_texts = choice_texts or {}
    canonical = re.sub("'(?=\ue000[AOC])", "’", canonical)

    def repl(match: re.Match) -> str:
        kind, index = match.group(1), int(match.group(2))
        mk = unit.markers[index]
        if kind == "O":
            return mk.raw
        if kind == "C":
            return mk.close_raw
        if kind == "A":
            return choice_texts.get(index, mk.raw)
        replacement = mk.replacement
        if mk.term_kind == "glossary":
            if target == "de":
                replacement = swiss_german(replacement)
            before = MARKER_RE.sub("", canonical[:match.start()])
            if mk.raw.isupper() and len(mk.raw) > 1:
                replacement = replacement.upper()
            elif _SENTENCE_START_RE.search(before) and replacement[:1].islower():
                replacement = replacement[:1].upper() + replacement[1:]
        return replacement

    return MARKER_RE.sub(repl, canonical)


# --------------------------------------------------------------------------
# 9. Repli par morceaux


def split_pieces(canonical: str) -> list[str]:
    """
    Découpe une phrase à repères aux éléments STRUCTURELS (variables,
    balises) : [texte, repère, texte, repère, …, texte]. Les termes (T)
    restent dans les morceaux de texte. Sert au repli quand le modèle a
    perdu ou déplacé un repère : chaque morceau de texte est alors traduit
    seul, et la structure est réassemblée telle quelle.
    """
    return _STRUCTURAL_MARKER_RE.split(canonical)


_EDGE_RE = re.compile(r"^([^\w\ue000]*)(.*?)([^\w\ue001]*)$", re.S)


def split_edges(piece: str) -> tuple[str, str, str]:
    """(début, cœur, fin) : la ponctuation et les espaces des bords sont
    gardés tels quels autour du morceau traduit."""
    match = _EDGE_RE.match(piece)
    return match.group(1), match.group(2), match.group(3)
