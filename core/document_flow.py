"""
Remise en ordre du FIL du texte, une fois les pages extraites.

Ce module intervient après `extract.py`/`layout.py`/`vision_ocr.py` et après
`page_cleanup.py`, sur du texte encore découpé en pages (séparateur ``\\f``).
Il ne s'occupe plus de géométrie -- seulement de ce qui se lit :

  1. **Recoller un paragraphe coupé entre deux pages** (`stitch_pages`), même
     si des pages entières s'intercalent. Cas réel signalé par l'utilisateur
     (11/09/2026) sur l'article « Relation between Intelligence and
     religiosity » : un paragraphe s'interrompt page 4 sur « … Only one
     coding », trois pages de tableaux suivent, et la phrase reprend page 8 sur
     « variable (goal of study) involved a subjective judgment… ». Les deux
     morceaux doivent redevenir UN seul paragraphe, sans quoi le moteur de
     traduction traduit deux moitiés de phrase sans lien -- et le lecteur ne
     retrouve jamais la suite.

  2. **Écarter les répétitions inutiles** (`drop_repeated_lines`) que
     `page_cleanup.py` laisse passer : ce dernier ne retient comme candidat
     qu'une ligne d'au plus 6 mots, ce qui suffit pour « 12 | Chapitre 3 »
     mais laisse entrer un filigrane de dépôt comme « Downloaded from
     pdf.highwire.org by guest on August 12, 2013 » (9 mots), présent sur les
     30 pages du même article. Le critère est ici différent, et c'est ce qui le
     rend sûr : peu importe la longueur, une ligne qui se répète à l'identique
     sur une large majorité des pages n'est jamais du texte d'auteur.

  3. **Repérer les explications de bas de page** (`looks_like_footnote_text`)
     sur des signaux de TEXTE, pour les sources où la géométrie n'existe pas --
     un OCR (bouton « Traduire X ») rend des lignes, pas des coordonnées.
     `layout.py` fait le même travail bien plus finement quand le PDF a une
     vraie couche texte ; les deux se complètent au lieu de se remplacer.

Tout est appliqué aux DEUX boutons : « Traduire » (extraction PDF) comme
« Traduire X » (OCR/vision), puisque ce module ne travaille que sur du texte.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Une ligne qui se répète sur au moins cette proportion des pages est un
# artefact de mise en page (filigrane, mention de dépôt, bandeau d'éditeur),
# jamais une phrase d'auteur -- quelle que soit sa longueur.
REPEAT_PAGE_SHARE = 0.6
# ...avec un minimum absolu, pour ne pas conclure sur un document de 3 pages.
MIN_REPEAT_PAGES = 4

# Un paragraphe qui se termine par l'un de ces caractères est fini. Les
# guillemets et parenthèses fermants sont acceptés APRÈS la ponctuation
# (« … fin de citation." »), d'où le nettoyage préalable dans `_is_unfinished`.
SENTENCE_ENDINGS = (".", "!", "?", ":", ";", "…")
CLOSING_MARKS = "\"')]»”’"

# Un titre n'est jamais la suite de quoi que ce soit : court, sans ponctuation
# finale. Sert à ne pas recoller un paragraphe avec le titre qui le suit.
HEADING_MAX_WORDS = 12

# Nombre maximal de blocs que l'on accepte d'enjamber pour retrouver la suite
# d'un paragraphe coupé (voir `_is_skippable`). Borné : au-delà, on préfère
# laisser la phrase coupée plutôt que de risquer de réordonner le document.
MAX_LOOKAHEAD_BLOCKS = 60
# Part de chiffres au-delà de laquelle une ligne est tenue pour une ligne de
# tableau plutôt que pour de la prose.
MIN_TABLE_DIGIT_SHARE = 0.12

# Fragment ignorable entre deux moitiés de paragraphe : numéro de page isolé,
# reliquat de numérotation, ligne d'un ou deux caractères.
IGNORABLE_RE = re.compile(r"^[\s\d.,;:|·•\-–—()\[\]ivxlcdmIVXLCDM]{0,12}$")

# Appel de note en début de ligne (« 1. », « a », « ² ») suivi d'une
# explication -- signal textuel d'une note de bas de page quand la géométrie
# n'est pas disponible.
FOOTNOTE_START_RE = re.compile(r"^\s*(\[\d{1,3}\]|\(\d{1,3}\)|\d{1,3}[.)]\s|[¹²³⁰-₟]+)")
FOOTNOTE_KEYWORD_RE = re.compile(
    r"^\s*(note|notes|remarque|source|sources|voir aussi|see also|ibid|op\. cit)\b[.: ]",
    re.IGNORECASE,
)


@dataclass
class FlowReport:
    """Ce qui a réellement été modifié -- affichable, jamais silencieux."""

    paragraphs_stitched: int = 0
    repeated_lines_removed: int = 0
    repeated_patterns: list[str] = field(default_factory=list)
    footnotes_detected: int = 0

    def summary_lines(self) -> list[str]:
        lines: list[str] = []
        if self.paragraphs_stitched:
            lines.append(
                f"{self.paragraphs_stitched} paragraphe(s) coupé(s) entre deux pages "
                "recollé(s) avec leur suite."
            )
        if self.repeated_lines_removed:
            shown = ", ".join(f"« {p[:48]} »" for p in self.repeated_patterns[:2])
            lines.append(
                f"{self.repeated_lines_removed} ligne(s) répétée(s) sur presque toutes les "
                f"pages retirée(s){' : ' + shown if shown else ''}."
            )
        if self.footnotes_detected:
            lines.append(f"{self.footnotes_detected} note(s) de bas de page repérée(s) dans le texte.")
        return lines


# ---------------------------------------------------------------------------
# 1. Répétitions inutiles
# ---------------------------------------------------------------------------


def _normalize(line: str) -> str:
    """
    Ramene une ligne a une forme comparable : espaces reduits, casse ignoree.

    Les chiffres sont volontairement CONSERVES tels quels. Les neutraliser
    (« page 4 » et « page 5 » ramenes a « page # ») paraissait plus malin, et
    c'est un vrai piege : le texte courant aussi differe parfois par un seul
    nombre. Un test du projet le montre noir sur blanc -- cinq paragraphes
    « Paragraph number 1/2/3… of the paginated test document » deviennent alors
    cinq copies de la meme ligne, et disparaissent tous les cinq. Une
    suppression a l'aveugle de vrai contenu est bien plus grave que le peu
    qu'on y gagnait : les en-tetes numerotes (« 12 | P a g e ») sont deja
    traites par `page_cleanup.py`, qui retire le numero AVANT de comparer et
    tolere en plus le bruit d'OCR. Ce filtre-ci ne vise que les repetitions
    STRICTEMENT identiques, celles que l'autre laisse passer parce qu'elles
    sont trop longues.
    """
    return re.sub(r"\s+", " ", line).strip().lower()


def drop_repeated_lines(text: str, report: FlowReport | None = None) -> str:
    """
    Retire les lignes qui reviennent à l'identique sur une large majorité des
    pages -- complément volontairement DIFFÉRENT de `page_cleanup.py`.

    `page_cleanup` regarde la POSITION (les bords de page) et exige une ligne
    courte ; ce filtre-ci regarde la FRÉQUENCE et se moque de la longueur.
    C'est ce qui lui permet d'attraper « Downloaded from pdf.highwire.org by
    guest on August 12, 2013 » (9 mots, donc trop long pour l'autre filtre),
    sans risque : aucune phrase d'auteur ne se répète mot pour mot sur 60 %
    des pages d'un document.
    """
    if "\f" not in text:
        return text
    pages = text.split("\f")
    if len(pages) < MIN_REPEAT_PAGES:
        return text

    seen: dict[str, set[int]] = {}
    for index, page in enumerate(pages):
        for line in page.split("\n"):
            key = _normalize(line)
            if key:
                seen.setdefault(key, set()).add(index)

    threshold = max(MIN_REPEAT_PAGES, int(len(pages) * REPEAT_PAGE_SHARE))
    banned = {key for key, hits in seen.items() if len(hits) >= threshold}
    if not banned:
        return text

    removed = 0
    cleaned_pages = []
    for page in pages:
        kept = []
        for line in page.split("\n"):
            if _normalize(line) in banned:
                removed += 1
                continue
            kept.append(line)
        cleaned_pages.append("\n".join(kept))

    if report is not None:
        report.repeated_lines_removed += removed
        report.repeated_patterns.extend(sorted(banned)[:4])
    return "\f".join(cleaned_pages)


# ---------------------------------------------------------------------------
# 2. Notes de bas de page (signal textuel)
# ---------------------------------------------------------------------------


def looks_like_footnote_text(paragraph: str) -> bool:
    """
    Repère une explication de bas de page sur sa seule FORME.

    Sert quand aucune géométrie n'est disponible (texte OCR du bouton
    « Traduire X », ou source .txt) : `layout.py` fait mieux dès qu'un PDF a une
    vraie couche texte, en comparant la taille de corps.

    Volontairement prudent : un paragraphe long est du texte courant, même s'il
    commence par un numéro -- une liste numérotée d'auteur ne doit jamais être
    prise pour une note.
    """
    text = paragraph.strip()
    if not text or len(text.split()) > 60:
        return False
    if FOOTNOTE_KEYWORD_RE.match(text):
        return True
    return bool(FOOTNOTE_START_RE.match(text)) and len(text.split()) <= 40


# ---------------------------------------------------------------------------
# 3. Recollage d'un paragraphe coupé entre deux pages
# ---------------------------------------------------------------------------


def _is_unfinished(paragraph: str) -> bool:
    """
    Ce paragraphe s'arrete-t-il en plein milieu d'une phrase ?

    On retire d'abord les guillemets/parentheses fermants, pour que
    « ... de la theorie." » compte bien comme termine.

    Un trait d'union final tranche a lui seul, avant meme le garde-fou de
    longueur : « ... highly intelli- » est une cesure typographique, donc un mot
    coupe en deux, donc forcement une phrase inachevee -- meme si le fragment
    est court. Sans cette priorite, une ligne courte terminee par une cesure
    etait prise pour un titre et sa fin restait orpheline.
    """
    text = paragraph.rstrip().rstrip(CLOSING_MARKS).rstrip()
    if not text:
        return False
    if text.endswith("-") and not text.endswith("--"):
        return True
    if text.endswith(SENTENCE_ENDINGS):
        return False
    # Un titre court n'est pas un paragraphe inacheve : il n'a simplement pas
    # de point final.
    return len(text.split()) > HEADING_MAX_WORDS


def _is_continuation(paragraph: str) -> bool:
    """
    Ce paragraphe reprend-il une phrase commencée ailleurs ?

    Signal principal : il commence par une minuscule -- aucun paragraphe
    d'auteur ne commence ainsi. Un chiffre ou une parenthèse fermante en tête
    (« (2008) was… », « 63 studies… ») compte aussi comme une reprise.
    """
    text = paragraph.lstrip()
    if not text:
        return False
    first = text[0]
    if first.islower():
        return True
    return first in ")]»" or (first.isdigit() and len(text.split()) > HEADING_MAX_WORDS)


def _is_ignorable(paragraph: str) -> bool:
    """Reliquat sans contenu entre deux moities de paragraphe : numero de page
    isole, tiret de separation, debris de numerotation."""
    return bool(IGNORABLE_RE.match(paragraph.strip()))


def _is_skippable(paragraph: str) -> bool:
    """
    Ce bloc peut-il etre ENJAMBE pour aller chercher la suite d'un paragraphe
    coupe plus loin ?

    C'est la partie « en ignorant les elements qui sont entre eux » de la
    demande de l'utilisateur. Elle sert surtout au bouton « Traduire X » : dans
    le chemin PDF ordinaire, `layout.py` a deja sorti les tableaux du corps du
    texte, mais un OCR ne rend que des lignes -- les lignes de tableau restent
    alors physiquement entre la fin d'un paragraphe et sa suite.

    Volontairement etroit. Un bloc n'est enjambable que s'il ne peut PAS etre
    de la prose courante :
      - un debris (numero de page isole) ;
      - une note de bas de page reconnue comme telle ;
      - un titre court, sans ponctuation finale ;
      - une ligne de tableau : soit deja mise a plat par `layout.py`
        (« Study : Bender (1968) ; Total n : 96 »), soit dense en chiffres.
    Tout le reste -- c'est-a-dire toute vraie phrase -- arrete la recherche :
    enjamber un vrai paragraphe reviendrait a reordonner le document, ce qui
    serait bien pire que de laisser une phrase coupee.
    """
    text = paragraph.strip()
    if not text:
        return True
    if _is_ignorable(text) or looks_like_footnote_text(text):
        return True
    words = text.split()
    if len(words) <= HEADING_MAX_WORDS and not text.endswith(SENTENCE_ENDINGS):
        return True
    if text.count(" : ") >= 2 and text.count(" ; ") >= 1:
        return True  # ligne de tableau remise a plat par layout.py
    digits = sum(1 for c in text if c.isdigit())
    return digits >= max(4, len(text) * MIN_TABLE_DIGIT_SHARE)


def stitch_pages(text: str, report: FlowReport | None = None) -> str:
    """
    Recolle les paragraphes coupés par un saut de page, en sautant ce qui
    s'intercale entre les deux moitiés.

    C'est la demande centrale de l'utilisateur (11/09/2026) : « tu devras
    reconnaître et savoir qu'un texte n'est pas fini encore, et chercher sa
    suite exacte en explorant les pages qui suivent et ainsi les mettre
    ensemble en ignorant les éléments qui sont entre eux ».

    Deux garde-fous, pour ne jamais recoller à tort :
      - la première moitié doit vraiment finir en plein milieu d'une phrase
        (`_is_unfinished`) -- pas seulement manquer de point, sinon tous les
        titres seraient absorbés par le paragraphe précédent ;
      - la seconde doit vraiment ressembler à une reprise (`_is_continuation`),
        c'est-à-dire commencer par une minuscule dans l'immense majorité des
        cas.
    Si aucune suite plausible n'est trouvée, le paragraphe est laissé
    exactement tel quel : ce module ne perd jamais de texte, il n'en déplace
    que ce dont il est sûr.

    Les sauts de page sont conservés (``\\f``) : `segment.py` continue de
    recevoir le format qu'il attend.
    """
    pages = text.split("\f")
    # Chaque page devient une liste de paragraphes, en gardant la trace de la
    # page d'origine pour pouvoir reconstruire le découpage à la fin.
    paragraphs: list[list[str]] = [
        [p for p in re.split(r"\n\s*\n", page) if p.strip()] for page in pages
    ]

    flat: list[tuple[int, int]] = [
        (page_index, para_index)
        for page_index, page in enumerate(paragraphs)
        for para_index in range(len(page))
    ]

    stitched = 0
    consumed: set[tuple[int, int]] = set()
    for position, (page_index, para_index) in enumerate(flat):
        if (page_index, para_index) in consumed:
            continue
        current = paragraphs[page_index][para_index]
        if not _is_unfinished(current):
            continue

        # On explore vers l'avant en enjambant ce qui ne peut PAS être de la
        # prose courante (débris, notes, titres courts, lignes de tableau --
        # voir `_is_skippable`). Toute vraie phrase, elle, arrête la
        # recherche : passer par-dessus reviendrait à réordonner le document.
        #
        # La boucle continue tant que le paragraphe recollé reste inachevé :
        # un même paragraphe peut être coupé en TROIS morceaux ou plus (deux
        # pages de tableaux, puis une figure), et s'arrêter au premier
        # recollage laisserait la fin orpheline.
        for next_page, next_para in flat[position + 1 : position + 1 + MAX_LOOKAHEAD_BLOCKS]:
            if (next_page, next_para) in consumed:
                continue
            candidate = paragraphs[next_page][next_para]
            if not _is_continuation(candidate):
                # Ni la suite, ni quelque chose qu'on ait le droit d'enjamber :
                # le texte est passé à autre chose, la phrase restera coupée.
                if _is_skippable(candidate):
                    continue
                break
            current = _join(current, candidate)
            paragraphs[page_index][para_index] = current
            paragraphs[next_page][next_para] = ""
            consumed.add((next_page, next_para))
            stitched += 1
            if not _is_unfinished(current):
                break

    if report is not None:
        report.paragraphs_stitched += stitched

    # Le saut de page est précédé d'un retour à la ligne, et ce détail n'en est
    # pas un : `segment.py` remplace ensuite chaque « \f » par un simple retour
    # à la ligne, puis découpe les paragraphes sur les LIGNES VIDES. Recoller
    # les pages avec « \f » seul collerait donc la dernière ligne d'une page à
    # la première de la suivante, et tout le document finirait en un unique
    # paragraphe géant (constaté sur un test du projet : 5 paragraphes réduits
    # à 1). Avec « \n\f », la ligne vide survit à la substitution.
    return "\n\f".join(
        "\n\n".join(p for p in page if p.strip()) for page in paragraphs
    )


def _join(first: str, second: str) -> str:
    """
    Recolle deux moitiés en réparant la césure : « intelli- » + « gence »
    redonne « intelligence », pas « intelli- gence ».
    """
    left = first.rstrip()
    right = second.lstrip()
    if left.endswith("-") and not left.endswith("--"):
        return left[:-1] + right
    return f"{left} {right}"


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------


def repair(text: str, drop_repeats: bool = True) -> tuple[str, FlowReport]:
    """
    Enchaîne les corrections dans le seul ordre qui fonctionne : retirer
    d'abord les répétitions, recoller ensuite.

    L'ordre compte vraiment. Un filigrane répété au bas de chaque page
    s'intercale physiquement entre la fin d'un paragraphe et sa suite ; tant
    qu'il est là, il est le « premier paragraphe réel » rencontré par
    `stitch_pages`, qui renonce alors à recoller. Nettoyé d'abord, la suite
    devient bien le paragraphe suivant.

    `drop_repeats=False` désactive le retrait des lignes répétées, sans toucher
    au recollage : c'est ce que demande l'utilisateur qui a répondu
    « original » au rapport de nettoyage des pages (voir `pipeline.run_job`).
    Il a alors explicitement choisi de garder les en-têtes et pieds de page ;
    les retirer ici par une autre porte reviendrait à passer outre sa décision.
    """
    report = FlowReport()
    cleaned = drop_repeated_lines(text, report) if drop_repeats else text
    return stitch_pages(cleaned, report), report
