"""
Traduction protégée par lots, pour le mode serveur (voir `core/server.py`).

Reçoit une liste de textes du site (syntaxe ICU, balises, marques...), et
renvoie pour chacun la traduction et d'éventuels avertissements. Toute la
protection du texte est dans `core/protection.py` ; ce module orchestre
les passes et les appels au moteur :

  1. chaque texte est analysé (ICU), ses pluriels éclatés en branches, ses
     éléments protégés remplacés par des repères, puis découpé en phrases ;
  2. PREMIÈRE PASSE : toutes les phrases de toute la requête partent au
     moteur en un seul lot (les doublons une seule fois), dans la syntaxe
     d'origine (`{name}`, `<strong>`, voir `protection.SourceStyle`) ;
     chaque sortie est contrôlée (tous les éléments revenus, une fois, bien
     imbriqués, aucune accolade ni balise ajoutée) ;
  3. RÉPARATION quand le seul défaut est une balise perdue (le modèle a
     réorganisé la phrase et abandonné un `<strong>`) : le contenu de la
     balise est traduit seul, retrouvé dans la phrase traduite, et entouré
     à nouveau -- la phrase garde tout son contexte ;
  4. REPLI PAR MORCEAUX pour les phrases encore en échec : le texte entre
     deux éléments structurels est traduit seul et la structure réassemblée
     telle quelle -- contexte plus court, structure garantie ;
     avertissement « traduit par morceaux » dans la réponse ;
  5. retouches de la langue cible (typographie, ß -> ss), démasquage,
     reconstruction des pluriels, puis CONTRÔLE FINAL : le texte traduit
     est réanalysé et doit avoir exactement les mêmes variables, balises et
     pluriels que la source ; sinon nouvel essai par morceaux, puis
     avertissement si l'échec persiste.

Le moteur n'est appelé que sous un verrou : un seul modèle en mémoire, une
traduction à la fois, même si le serveur HTTP reçoit plusieurs requêtes.
"""
from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from . import protection as prot
from . import server_engines

# Au-delà, une phrase est recoupée (aux points-virgules, virgules, puis
# espaces) : MADLAD-400 a été entraîné sur des entrées de 512 jetons au plus
# (`n_positions` de sa configuration), et la qualité s'effondre au-delà --
# 1 200 caractères de français restent nettement en dessous.
MAX_SEGMENT_CHARS = 1200

_STRAY_MARKUP_RE = re.compile(r"[{}]|</?[A-Za-z][\w.:-]*\s*/?>")


@dataclass
class TranslationResult:
    text: str
    warnings: list[str] = field(default_factory=list)


@dataclass
class _Segment:
    source: str                      # forme interne, sans espaces de bord
    unit: "_Unit"
    output: str | None = None
    first_output: str | None = None  # sortie refusée de la première passe (pour la réparer)
    problems: list[str] = field(default_factory=list)
    repaired: str = ""               # "" | "exact" | "approx" (voir _repair_pass)
    by_pieces: bool = False

    @property
    def needs_translation(self) -> bool:
        return prot.has_letters(self.source)


@dataclass
class _ChoiceJob:
    node: prot.Choice
    branches: list["_Unit"]


@dataclass
class _Unit:
    masked: prot.MaskedUnit
    parts: list[str]                 # [avant, phrase, séparateur, phrase, …, après]
    segments: dict[int, _Segment]
    choices: dict[int, _ChoiceJob]
    pound_active: bool               # branche de pluriel : un « # » brut y serait interprété
    selector: str | None = None      # branche : son sélecteur (« one », « other », « =0 »…)

    def iter_segments(self):
        yield from self.segments.values()
        for job in self.choices.values():
            for branch in job.branches:
                yield from branch.iter_segments()


@dataclass
class _Message:
    source: str
    nodes: list
    lenient: bool
    unit: _Unit
    warnings: list[str]


class TranslationService:
    """
    Service de traduction réutilisable (serveur HTTP, tests, scripts).
    `engine` : un moteur de `core/server_engines.py` (ou un moteur factice
    de test qui déclare une licence commerciale) déjà chargé ou non.
    """

    def __init__(
        self,
        engine,
        *,
        style: prot.MarkerStyle = prot.SOURCE_STYLE,
        brands: tuple[str, ...] = prot.DEFAULT_BRANDS,
        cache_size: int = 20_000,
        max_segment_chars: int = MAX_SEGMENT_CHARS,
    ) -> None:
        server_engines.ensure_engine_instance_allowed(engine)
        self.engine = engine
        self.style = style
        # Nouveaux essais et repli : marques et keep_terms en repères neutres,
        # pour que le modèle ne puisse plus les traduire.
        self.fallback_style = prot.SOURCE_STYLE_MASKED_TERMS if isinstance(style, prot.SourceStyle) else style
        self.brands = tuple(brands)
        self.cache_size = cache_size
        self.max_segment_chars = max_segment_chars
        self._cache: OrderedDict[tuple[str, str], str] = OrderedDict()
        self._lock = threading.Lock()
        self.last_stats: dict = {}

    # ------------------------------------------------------------ public

    def translate(
        self,
        texts: list[str],
        target: str,
        *,
        keep_terms: list[str] | tuple[str, ...] = (),
        glossary: dict[str, str] | None = None,
        split_sentences: bool = True,
    ) -> list[TranslationResult]:
        """Traduit `texts` (français) vers `target` (code ISO : de, it, en, pt)."""
        server_engines.ensure_engine_instance_allowed(self.engine)
        started = time.perf_counter()
        terms = prot.TermSet(brands=self.brands, keep_terms=keep_terms, glossary=glossary)
        messages = [self._prepare(text, terms, split_sentences) for text in texts]
        stats = {"texts": len(texts), "segments": 0, "model_inputs": 0, "repaired": 0, "pieces_segments": 0}

        with self._lock:
            segments = [seg for msg in messages for seg in msg.unit.iter_segments()]
            stats["segments"] = len(segments)
            self._first_pass(segments, target, stats)
            self._repair_pass([seg for seg in segments if seg.problems], target, stats)
            self._pieces_pass([seg for seg in segments if seg.problems], target, stats)

            results = [self._assemble(msg, target) for msg in messages]
            # Contrôle final en échec sur un texte traduit d'un bloc : nouvel
            # essai par morceaux pour ce texte, puis avertissement si ça persiste.
            retry = [
                index for index, (msg, (_text, ok, _warnings)) in enumerate(zip(messages, results))
                if not ok and any(not seg.by_pieces and seg.needs_translation for seg in msg.unit.iter_segments())
            ]
            if retry:
                again = []
                for index in retry:
                    for seg in messages[index].unit.iter_segments():
                        if seg.needs_translation and not seg.by_pieces:
                            seg.problems = ["contrôle final"]
                            again.append(seg)
                self._pieces_pass(again, target, stats)
                for index in retry:
                    results[index] = self._assemble(messages[index], target)

        final = []
        for msg, (text, _ok, warnings) in zip(messages, results):
            final.append(TranslationResult(text=text, warnings=list(dict.fromkeys(msg.warnings + warnings))))
        stats["seconds"] = round(time.perf_counter() - started, 3)
        stats["warnings"] = sum(1 for result in final if result.warnings)
        self.last_stats = stats
        return final

    # ------------------------------------------------------------ préparation

    def _prepare(self, text: str, terms: prot.TermSet, split: bool) -> _Message:
        warnings: list[str] = []
        try:
            nodes = prot.parse_message(text)
            lenient = False
        except prot.IcuSyntaxError as exc:
            nodes = prot.parse_lenient(text)
            lenient = True
            warnings.append(
                f"syntaxe ICU non reconnue ({exc}) : variables et balises protégées une à une, "
                "sans structure -- à relire"
            )
        unit = self._build_unit(nodes, terms, split, pound_active=False)
        return _Message(text, nodes, lenient, unit, warnings)

    def _build_unit(self, nodes: list, terms: prot.TermSet, split: bool, pound_active: bool,
                    selector: str | None = None) -> _Unit:
        masked = prot.mask_nodes(nodes, terms)
        if split:
            parts = prot.split_sentences(masked.canonical)
        else:
            lead = re.match(r"\s*", masked.canonical).group(0)
            body = masked.canonical[len(lead):].rstrip()
            parts = [lead, body, masked.canonical[len(lead) + len(body):]] if body else [masked.canonical]
        parts = self._split_long(parts)
        unit = _Unit(masked=masked, parts=parts, segments={}, choices={}, pound_active=pound_active,
                     selector=selector)
        for index in range(1, len(parts), 2):
            unit.segments[index] = _Segment(source=parts[index], unit=unit)
        for marker_index, mk in masked.markers.items():
            if mk.choice is not None:
                branch_pound = pound_active or mk.choice.kind != "select"
                unit.choices[marker_index] = _ChoiceJob(
                    mk.choice,
                    [self._build_unit(b.children, terms, split, branch_pound, b.prefix.strip())
                     for b in mk.choice.branches],
                )
        return unit

    def _split_long(self, parts: list[str]) -> list[str]:
        if all(len(parts[i]) <= self.max_segment_chars for i in range(1, len(parts), 2)):
            return parts
        result = [parts[0]]
        for index in range(1, len(parts), 2):
            pieces = _cut_long_sentence(parts[index], self.max_segment_chars)
            result.extend(pieces)
            result.append(parts[index + 1])
        return result

    # ------------------------------------------------------------ passes

    def _engine_translate(self, texts: list[str], target: str, stats: dict) -> list[str]:
        """Appel au moteur avec cache (cible, texte) -> sortie brute."""
        outputs: dict[str, str] = {}
        missing: list[str] = []
        for text in dict.fromkeys(texts):
            key = (target, text)
            if key in self._cache:
                self._cache.move_to_end(key)
                outputs[text] = self._cache[key]
            else:
                missing.append(text)
        if missing:
            stats["model_inputs"] += len(missing)
            for text, out in zip(missing, self.engine.translate_batch(missing, target)):
                outputs[text] = out
                self._cache[(target, text)] = out
                if len(self._cache) > self.cache_size:
                    self._cache.popitem(last=False)
        return [outputs[text] for text in texts]

    def _check(self, seg: _Segment, source: str, output: str) -> list[str]:
        problems = prot.marker_problems(source, output)
        if not problems:
            stray = _STRAY_MARKUP_RE.search(prot.MARKER_RE.sub("", output))
            if stray:
                problems.append(f"balise ou accolade ajoutée par le moteur ({stray.group(0)})")
            elif seg.unit.pound_active and "#" in prot.MARKER_RE.sub("", output):
                problems.append("« # » ajouté dans un pluriel")
        return problems

    def _first_pass(self, segments: list[_Segment], target: str, stats: dict) -> None:
        todo = []
        for seg in segments:
            if seg.needs_translation:
                todo.append(seg)
            else:
                seg.output = seg.source
        if not todo:
            return
        rendered = [self.style.render(seg.source, seg.unit.masked) for seg in todo]
        for seg, raw in zip(todo, self._engine_translate(rendered, target, stats)):
            output = self.style.parse(raw, seg.source, seg.unit.masked)
            problems = self._check(seg, seg.source, output)
            if problems:
                seg.problems = problems
                seg.first_output = output
                seg.output = None
            else:
                seg.output = output

    def _repair_pass(self, segments: list[_Segment], target: str, stats: dict) -> None:
        """
        Réparations qui gardent la phrase entière (vérifiées comme le reste ;
        en cas d'échec, la phrase passe au repli par morceaux) :
          - le seul défaut est une (ou des) balise(s) perdue(s) : le contenu
            de chaque balise est traduit seul, retrouvé dans la phrase
            traduite et entouré à nouveau ;
          - le seul défaut est une marque disparue (traduite par le modèle) :
            la phrase est retraduite avec les marques en repères neutres ;
          - le seul défaut est le `#` d'une branche de pluriel perdu (vu :
            « # chapitres » compris comme « nombre de chapitres ») : la
            branche est retraduite avec un vrai nombre à sa place (« 12
            chapitres »), remis en `#` ensuite.
        """
        candidates = []
        retry_candidates: list[tuple[_Segment, str]] = []
        requests: list[tuple[str, _Segment, str]] = []
        numbers: dict[int, tuple[int, str]] = {}
        for seg in segments:
            if seg.first_output is None:
                continue
            pairs = prot.lost_pairs(seg.source, seg.first_output)
            pound = (prot.lost_pound_only(seg.source, seg.first_output, seg.unit.masked)
                     if seg.unit.selector is not None else None)
            number = prot.pound_number(seg.unit.selector, seg.source) if pound is not None else None
            if pairs:
                candidates.append((seg, pairs))
                for index in pairs:
                    content = prot.pair_content(seg.source, index)
                    if prot.has_letters(content):
                        requests.append(("pair", seg, content))
            elif prot.lost_terms_only(seg.source, seg.first_output):
                retry_candidates.append((seg, "terms"))
                requests.append(("terms", seg, seg.source))
            elif number is not None:
                numbers[id(seg)] = (pound, number)
                retry_candidates.append((seg, "pound"))
                requests.append(("pound", seg, seg.source.replace(prot.marker("A", pound), number)))
        translated: dict[tuple[int, str], str] = {}
        retranslated: dict[int, str] = {}
        if requests:
            styles = [self.fallback_style if kind == "terms" else self.style for kind, _seg, _text in requests]
            rendered = [style.render(text.strip(), seg.unit.masked)
                        for style, (_kind, seg, text) in zip(styles, requests)]
            raws = self._engine_translate(rendered, target, stats)
            for style, (kind, seg, text), raw in zip(styles, requests, raws):
                output = style.parse(raw, seg.source if kind == "pound" else text.strip(), seg.unit.masked)
                if kind == "pound":
                    # Le nombre redevient `#` (cherché hors des repères internes,
                    # qui contiennent eux aussi des chiffres).
                    pound, number = numbers[id(seg)]
                    blanked = prot.MARKER_RE.sub(lambda m: " " * len(m.group()), output)
                    found = list(re.finditer(rf"(?<!\d){re.escape(number)}(?!\d)", blanked))
                    if len(found) != 1:
                        continue
                    output = output[:found[0].start()] + prot.marker("A", pound) + output[found[0].end():]
                if kind in ("terms", "pound"):
                    retranslated[id(seg)] = output
                elif not prot.marker_problems(text.strip(), output):
                    translated[(id(seg), text)] = output
        for seg, kind in retry_candidates:
            output = retranslated.get(id(seg))
            if output is not None and not self._check(seg, seg.source, output):
                seg.output = output
                seg.problems = []
                seg.repaired = kind
                stats["repaired"] += 1
        for seg, pairs in candidates:
            repaired = seg.first_output
            fuzzy = False
            for index in pairs:
                content = prot.pair_content(seg.source, index)
                needle = translated.get((id(seg), content)) if prot.has_letters(content) else content
                if not needle:
                    repaired = None
                    break
                exact = prot.wrap_span(repaired, needle, index)
                if exact is None:
                    exact = prot.wrap_span_fuzzy(repaired, needle, index)
                    fuzzy = fuzzy or exact is not None
                repaired = exact
                if repaired is None:
                    break
            if repaired is not None and not self._check(seg, seg.source, repaired):
                seg.output = repaired
                seg.problems = []
                seg.repaired = "approx" if fuzzy else "exact"
                stats["repaired"] += 1

    def _pieces_pass(self, segments: list[_Segment], target: str, stats: dict) -> None:
        """
        Repli : chaque morceau de texte entre deux repères structurels est
        traduit seul. Les termes protégés (marques, termes imposés) restent
        des repères dans le morceau ; si le moteur les perd encore, le
        morceau est retraduit avec le terme écrit en clair.
        """
        if not segments:
            return
        stats["pieces_segments"] += len(segments)
        plans = []
        requests: list[tuple[_Segment, str]] = []
        for seg in segments:
            plan = []
            for position, token in enumerate(prot.split_pieces(seg.source)):
                if position % 2 == 1:
                    plan.append(token)
                    continue
                lead, core, trail = prot.split_edges(token)
                if core and prot.has_letters(core):
                    plan.append((lead, core, trail))
                    requests.append((seg, core))
                else:
                    plan.append(token)
            plans.append(plan)

        style = self.fallback_style
        rendered = [style.render(core, seg.unit.masked) for seg, core in requests]
        raw_outputs = self._engine_translate(rendered, target, stats) if rendered else []
        translated: dict[tuple[int, str], str] = {}
        inline_requests: list[tuple[_Segment, str, str]] = []
        for (seg, core), raw in zip(requests, raw_outputs):
            output = style.parse(raw, core, seg.unit.masked)
            if self._check(seg, core, output):
                inline_requests.append((seg, core, _inline_terms(core, seg.unit.masked)))
            else:
                translated[(id(seg), core)] = output

        if inline_requests:
            raws = self._engine_translate(
                [style.render(text, seg.unit.masked) for seg, _c, text in inline_requests], target, stats)
            for (seg, core, text), raw in zip(inline_requests, raws):
                output = style.parse(raw, text, seg.unit.masked)
                translated[(id(seg), core)] = _reattach_terms(output, core, seg.unit.masked)

        for seg, plan in zip(segments, plans):
            out = []
            for item in plan:
                if isinstance(item, tuple):
                    lead, core, trail = item
                    out.append(lead + translated.get((id(seg), core), core) + trail)
                else:
                    out.append(item)
            seg.output = "".join(out)
            seg.by_pieces = True
            seg.problems = []

    # ------------------------------------------------------------ assemblage

    def _assemble_unit(self, unit: _Unit, target: str) -> str:
        choice_texts = {}
        for marker_index, job in unit.choices.items():
            branch_texts = [self._assemble_unit(branch, target) for branch in job.branches]
            choice_texts[marker_index] = (
                job.node.head
                + "".join(b.prefix + "{" + text + "}" for b, text in zip(job.node.branches, branch_texts))
                + job.node.tail
            )
        pieces = [unit.parts[0]]
        for index in range(1, len(unit.parts), 2):
            seg = unit.segments[index]
            output = seg.output if seg.output is not None else seg.source
            pieces.append(prot.finish_segment(output, seg.source, target))
            pieces.append(unit.parts[index + 1])
        return prot.unmask("".join(pieces), unit.masked, choice_texts, target)

    def _assemble(self, msg: _Message, target: str) -> tuple[str, bool, list[str]]:
        """Texte final, résultat du contrôle final (True si la structure est
        identique à la source) et avertissements de cet assemblage."""
        text = self._assemble_unit(msg.unit, target)
        warnings: list[str] = []
        for term in _missing_terms(msg, text, target):
            warnings.append(f"terme protégé ou imposé absent de la traduction : « {term} »")
        if any(seg.repaired == "approx" for seg in msg.unit.iter_segments()):
            warnings.append(
                "balise perdue par le moteur puis replacée par rapprochement : vérifier les mots mis en valeur"
            )
        if any(seg.by_pieces for seg in msg.unit.iter_segments()):
            warnings.append(
                "balises ou variables mal recopiées par le moteur : phrase retraduite par morceaux "
                "(contexte réduit), à relire"
            )
        ok, detail = _same_structure(msg, text)
        if not ok:
            warnings.append(f"contrôle final : variables ou balises différentes de la source ({detail})")
        return text, ok, warnings


# --------------------------------------------------------------------------
# Aides


def _cut_long_sentence(sentence: str, limit: int) -> list[str]:
    """[morceau, séparateur, morceau, …] : coupe une phrase trop longue sans
    jamais séparer une balise ouvrante de sa fermante."""
    if len(sentence) <= limit:
        return [sentence]
    for pattern in (r";\s+", r",\s+", r"\s+"):
        best = None
        for match in re.finditer(pattern, sentence[:limit]):
            head = sentence[:match.start() + 1]
            opened = set()
            for mk in prot.MARKER_RE.finditer(head):
                if mk.group(1) == "O":
                    opened.add(mk.group(2))
                elif mk.group(1) == "C":
                    opened.discard(mk.group(2))
            if not opened and match.start() > limit // 3:
                best = match
        if best is not None:
            cut_at = best.start() + (1 if pattern != r"\s+" else 0)
            first = sentence[:cut_at]
            separator = sentence[cut_at:best.end()]
            return [first, separator] + _cut_long_sentence(sentence[best.end():], limit)
    return [sentence]


def _inline_terms(core: str, masked: prot.MaskedUnit) -> str:
    """Remplace les repères de termes par leur texte final (dernier recours)."""
    def repl(match: re.Match) -> str:
        if match.group(1) != "T":
            return match.group(0)
        return masked.markers[int(match.group(2))].replacement
    return prot.MARKER_RE.sub(repl, core)


def _reattach_terms(output: str, core: str, masked: prot.MaskedUnit) -> str:
    """Après une traduction « en clair », remet un repère T sur la première
    occurrence de chaque terme retrouvé (pour la majuscule et le contrôle)."""
    for match in prot.MARKER_RE.finditer(core):
        if match.group(1) != "T":
            continue
        mk = masked.markers[int(match.group(2))]
        position = output.lower().find(mk.replacement.lower())
        if position >= 0:
            output = output[:position] + prot.marker("T", mk.index) + output[position + len(mk.replacement):]
    return output


def _missing_terms(msg: _Message, text: str, target: str) -> list[str]:
    missing = []
    haystack = text.lower()

    def walk(unit: _Unit) -> None:
        for mk in unit.masked.markers.values():
            if mk.kind != "T":
                continue
            expected = prot.swiss_german(mk.replacement) if target == "de" and mk.term_kind == "glossary" else mk.replacement
            if expected.lower() not in haystack:
                missing.append(expected)
        for job in unit.choices.values():
            for branch in job.branches:
                walk(branch)

    walk(msg.unit)
    return list(dict.fromkeys(missing))


def _same_structure(msg: _Message, text: str) -> tuple[bool, str]:
    if msg.lenient:
        source_tokens = sorted(n.raw for n in msg.nodes if isinstance(n, prot.Placeholder))
        output_tokens = sorted(n.raw for n in prot.parse_lenient(text) if isinstance(n, prot.Placeholder))
        if source_tokens == output_tokens:
            return True, ""
        return False, "éléments protégés différents"
    try:
        output_nodes = prot.parse_message(text)
    except prot.IcuSyntaxError as exc:
        return False, f"syntaxe ICU cassée : {exc}"
    source_sig = prot.structure_signature(msg.nodes)
    output_sig = prot.structure_signature(output_nodes)
    if source_sig == output_sig:
        return True, ""
    return False, prot.describe_signature_difference(source_sig, output_sig)
