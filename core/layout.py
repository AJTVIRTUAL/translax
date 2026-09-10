"""
Analyse de la MISE EN PAGE réelle d'un PDF (géométrie), là où
`extract.py` ne lisait que du texte à plat.

Pourquoi ce module existe -- un vrai défaut constaté, pas une amélioration
théorique. `extract.py` demande à PyMuPDF le texte trié par position
(`sort=True`), ce qui suffit pour un livre en UNE colonne. Sur un article
scientifique en DEUX colonnes, ce tri met côte à côte, sur la même ligne
logique, la colonne de gauche et celle de droite :

    « ability. The reason is that very conservative Christians scorn
      university entrance exams (UEEs; e.g., SAT, GRE), which are »

...soit la fin d'une phrase de gauche collée au début d'une phrase de
droite. Le texte devient illisible AVANT même d'être traduit -- et c'est la
cause réelle du symptôme signalé par l'utilisateur (11/09/2026) : un
paragraphe coupé page 4 dont la suite se trouve page 8, introuvable parce
que l'ordre de lecture lui-même était faux.

Ce que ce module produit, à partir de la géométrie de chaque page :

  1. **Ordre de lecture correct** (`_reading_blocks`), par découpe récursive
     « XY-cut » : on cherche d'abord une gouttière verticale franche (deux
     colonnes), sinon une coupure horizontale (bandeau pleine largeur :
     titre, tableau, figure). Un titre qui court sur les deux colonnes est
     donc lu AVANT elles, et chaque colonne est lue entièrement de haut en
     bas avant de passer à la suivante.

  2. **Tableaux reconnus et reconstruits** (`_build_table`), qu'ils soient
     imprimés normalement ou en PAYSAGE sur une page portrait -- ce dernier
     cas étant celui des grands tableaux de ce corpus, que PyMuPDF rend comme
     du texte pivoté (`line["dir"] == (0, -1)`) et que le tri par position
     éparpille en fragments sans rapport. Un seul algorithme sert aux deux :
     seuls changent les axes (`_TableAxes`). Lignes et colonnes sont
     reconstituées par leur position réelle, puis remises à plat en phrases
     « étiquette : valeur », lisibles et traduisibles une à la suite de
     l'autre.

  3. **Notes de bas de page repérées** (`_looks_like_footnote`), sur un
     signal structurel : un corps de texte nettement plus petit que la taille
     dominante de la page, situé en bas de colonne.

  4. **Rôle attribué à chaque région** (`Region.kind`), pour que `pipeline.py`
     puisse renvoyer tableaux et notes à la FIN du document (demande
     explicite de l'utilisateur) au lieu de les laisser couper un paragraphe
     en deux.

Le corps du texte reste séparé par des sauts de page (``\\f``), exactement
comme avant : `page_cleanup.py` et `segment.py` continuent de fonctionner
sans rien savoir de ce module.

Limite assumée : un PDF scanné (image sans couche texte) ne contient aucune
géométrie de texte exploitable -- ce module ne renvoie alors rien et
l'appelant retombe sur l'extraction classique ou l'OCR (bouton
« Traduire X », voir `core/vision_ocr.py`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median

# --- Seuils, tous calibrés sur des PDF réels du projet ---------------------

# Une ligne est PIVOTÉE si sa direction d'écriture n'est pas horizontale.
# PyMuPDF donne un vecteur unitaire : (1, 0) = normal, (0, -1) = pivoté de
# 90°. On teste la composante x plutôt qu'une égalité stricte, pour tolérer
# un texte très légèrement incliné.
ROTATED_DIR_MAX_X = 0.5

# Largeur minimale d'une gouttière (blanc vertical traversant) pour croire à
# deux vraies colonnes, en fraction de la largeur de la région analysée. Un
# article en deux colonnes en laisse typiquement 3 à 5 % ; l'espace entre
# deux mots d'une même ligne reste très en dessous.
MIN_GUTTER_RATIO = 0.022
# Une gouttière doit aussi laisser de la matière des DEUX côtés : sans ça,
# une simple marge (rien à gauche, tout à droite) passerait pour une colonne.
MIN_SIDE_SHARE = 0.15

# Coupure horizontale : un blanc franc entre deux bandes de contenu. Plus
# exigeant que l'interligne d'un paragraphe, pour ne pas redécouper un
# paragraphe en régions à chaque saut de ligne.
MIN_HORIZONTAL_GAP = 9.0

# Un tableau reconstruit doit avoir assez de lignes ET de colonnes pour être
# vraiment un tableau -- sinon c'est un bandeau pivoté isolé (le filigrane
# « Downloaded from... » présent sur chaque page de ce corpus est pivoté lui
# aussi, mais n'est pas un tableau).
MIN_TABLE_ROWS = 3
MIN_TABLE_COLUMNS = 2

# Regroupement des lignes d'un tableau pivoté : deux fragments dont les
# centres sont plus proches que cette tolérance (en points PDF) appartiennent
# à la même ligne du tableau.
ROW_CLUSTER_TOLERANCE = 6.0
# Idem pour les colonnes, sur l'autre axe (plus large : une cellule numérique
# centrée peut se décaler d'une ligne à l'autre).
COLUMN_CLUSTER_TOLERANCE = 14.0
# Fraction minimale des lignes de référence qui doivent confirmer une position
# pour qu'elle compte comme une vraie colonne (voir _column_centers) : les
# positions d'en-tête isolées, confirmées par une ou deux lignes seulement,
# sont ainsi écartées sans écarter une colonne réellement peu remplie.
MIN_COLUMN_SUPPORT = 0.2
# Part minimale de cellules courtes dans une région pour y voir un tableau.
MIN_TABLE_SHORT_SHARE = 0.5
# Un tableau NON pivoté doit avoir au moins trois colonnes distinctes : à deux,
# on ne le distingue plus d'une liste numérotée (section « Notes ») ni d'une
# formule en retrait, tous deux présents dans ce corpus.
MIN_TABLE_COLUMNS_FLAT = 3
# Part minimale de cellules a dominante chiffree pour croire a un tableau de
# donnees, et seuil a partir duquel une cellule compte comme « chiffree ».
MIN_TABLE_NUMERIC_SHARE = 0.20
MIN_CELL_DIGIT_SHARE = 0.30
# Largeur mediane d'une cellule, rapportee a la largeur de la region. Un
# tableau est fait de cellules courtes ; une ligne de prose remplit sa
# colonne. Mesure reelle sur le PDF de reference : 0,06 pour un tableau,
# 0,48 pour de la prose en deux colonnes -- le seuil est loin des deux.
MAX_TABLE_CELL_SHARE = 0.30

# Seuils de bascule vers l'analyse de mise en page (voir `is_layout_useful`).
MIN_PAGES_FOR_LAYOUT = 3
MIN_MULTI_COLUMN_SHARE = 0.40
MIN_TABLE_PAGE_SHARE = 0.20
# Nombre de pages examinées par la sonde rapide (voir `probe_pdf`).
PROBE_PAGES = 24

# Cellule purement numérique (« 96 », « 0.42 », « −.10 ») -- sert à repérer où
# l'en-tête s'arrête et où les données commencent. Le signe moins Unicode
# (U+2212) est celui réellement employé par ce PDF, pas un tiret ASCII.
NUMERIC_CELL_RE = re.compile(r'[\d.,−+<>%-]+')

# Note de bas de page : corps sensiblement plus petit que la taille dominante
# de la page. 0.92 laisse passer les variations normales d'une même fonte ;
# en dessous, c'est un autre niveau de texte.
FOOTNOTE_SIZE_RATIO = 0.92
# ...et situé dans le dernier quart de la hauteur de page.
FOOTNOTE_ZONE_RATIO = 0.72

# Légende de tableau/figure : reconnue par sa FORME, pas par sa position.
CAPTION_RE = re.compile(
    r"^\s*(table|tableau|figure|fig\.|exhibit|appendix|annexe)\s*\d+[.: \s]",
    re.IGNORECASE,
)


@dataclass
class Fragment:
    """Une ligne de texte réelle, avec sa géométrie."""

    text: str
    bbox: tuple[float, float, float, float]  # x0, y0, x1, y1 (y croît vers le BAS)
    size: float
    rotated: bool

    @property
    def x_center(self) -> float:
        return (self.bbox[0] + self.bbox[2]) / 2

    @property
    def y_center(self) -> float:
        return (self.bbox[1] + self.bbox[3]) / 2


@dataclass
class Region:
    """
    Un morceau de page auquel on a attribué un RÔLE. C'est ce rôle qui permet
    ensuite de renvoyer les tableaux à la fin sans toucher au corps du texte.
    """

    kind: str  # "body" | "table" | "footnote" | "caption"
    text: str
    page_index: int  # 0-indexé
    label: str = ""  # « Table 1. » pour un tableau, quand la légende a été trouvée

    @property
    def page_number(self) -> int:
        return self.page_index + 1


@dataclass
class LayoutReport:
    """Ce que l'analyse a réellement trouvé -- affichable à l'utilisateur."""

    total_pages: int = 0
    columns_per_page: list[int] = field(default_factory=list)
    tables_found: int = 0
    table_rows_total: int = 0
    footnotes_found: int = 0
    duplicate_notes_removed: int = 0
    pages_without_text: int = 0

    @property
    def multi_column_pages(self) -> int:
        return sum(1 for c in self.columns_per_page if c >= 2)

    def summary_lines(self) -> list[str]:
        lines: list[str] = []
        if self.multi_column_pages:
            lines.append(
                f"{self.multi_column_pages} page(s) sur {self.total_pages} en plusieurs colonnes — "
                "ordre de lecture reconstruit colonne par colonne."
            )
        if self.tables_found:
            lines.append(
                f"{self.tables_found} tableau(x) détecté(s) ({self.table_rows_total} lignes au total) — "
                "remis à plat et déplacés à la fin du document."
            )
        if self.footnotes_found:
            duplicates = (
                f" ({self.duplicate_notes_removed} doublon(s) écarté(s))"
                if self.duplicate_notes_removed else ""
            )
            lines.append(
                f"{self.footnotes_found} note(s) de bas de page repérée(s) et regroupée(s) "
                f"à la fin{duplicates}."
            )
        if self.pages_without_text:
            lines.append(
                f"{self.pages_without_text} page(s) sans couche texte (probablement scannées) — "
                "utiliser « Traduire X » pour les lire par OCR."
            )
        return lines


@dataclass
class LayoutDocument:
    regions: list[Region] = field(default_factory=list)
    report: LayoutReport = field(default_factory=LayoutReport)

    def body_text(self) -> str:
        """
        Le corps du texte seul, dans le bon ordre de lecture, avec un saut de
        page (``\\f``) entre pages -- le format exact qu'attendent déjà
        `page_cleanup.py` et `segment.py`.
        """
        by_page: dict[int, list[str]] = {}
        for r in self.regions:
            if r.kind == "body":
                by_page.setdefault(r.page_index, []).append(r.text)
        pages = [
            "\n\n".join(by_page.get(page_index, []))
            for page_index in range(self.report.total_pages)
        ]
        return "\f".join(pages)

    def deferred_regions(self) -> list[Region]:
        """Tableaux, légendes et notes -- tout ce qui doit passer à la fin."""
        return [r for r in self.regions if r.kind in {"table", "footnote", "caption"}]


# ---------------------------------------------------------------------------
# Lecture des fragments d'une page
# ---------------------------------------------------------------------------


def _page_fragments(page) -> list[Fragment]:
    """Toutes les lignes de texte d'une page, avec géométrie et rotation."""
    fragments: list[Fragment] = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:  # 0 = texte ; 1 = image
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = "".join(s.get("text", "") for s in spans).strip()
            if not text:
                continue
            direction = line.get("dir", (1.0, 0.0))
            sizes = [s.get("size", 0.0) for s in spans if s.get("text", "").strip()]
            fragments.append(
                Fragment(
                    text=text,
                    bbox=tuple(line["bbox"]),
                    size=max(sizes) if sizes else 0.0,
                    rotated=abs(direction[0]) < ROTATED_DIR_MAX_X,
                )
            )
    return fragments


# ---------------------------------------------------------------------------
# Ordre de lecture : decoupe recursive (« XY-cut »)
# ---------------------------------------------------------------------------


def _merge_spans(pairs: list[tuple[float, float]]) -> list[list[float]]:
    """Fusionne des intervalles qui se chevauchent, triés."""
    merged: list[list[float]] = []
    for start, end in sorted(pairs):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def _find_gutter(fragments: list[Fragment]) -> float | None:
    """
    Cherche une gouttière verticale : une bande que AUCUN fragment ne
    traverse, assez large pour séparer deux colonnes, avec de la matière des
    deux côtés. Retourne l'abscisse de coupure, ou None s'il n'y a qu'une
    colonne.
    """
    if len(fragments) < 4:
        return None  # trop peu de matière pour affirmer quoi que ce soit
    x0 = min(f.bbox[0] for f in fragments)
    x1 = max(f.bbox[2] for f in fragments)
    width = x1 - x0
    if width <= 0:
        return None

    merged = _merge_spans([(f.bbox[0], f.bbox[2]) for f in fragments])
    best_cut = None
    best_gap = MIN_GUTTER_RATIO * width
    for left, right in zip(merged, merged[1:]):
        gap = right[0] - left[1]
        if gap <= best_gap:
            continue
        cut = (left[1] + right[0]) / 2
        # Une vraie gouttière de colonnes a de la matière des deux côtés.
        share = sum(1 for f in fragments if f.x_center < cut) / len(fragments)
        if share < MIN_SIDE_SHARE or share > 1 - MIN_SIDE_SHARE:
            continue
        best_cut, best_gap = cut, gap
    return best_cut


def _find_horizontal_split(fragments: list[Fragment]) -> float | None:
    """
    Cherche un blanc horizontal franc : une bande que rien ne traverse,
    séparant par exemple un titre pleine largeur du corps en colonnes.
    """
    if len(fragments) < 2:
        return None
    merged = _merge_spans([(f.bbox[1], f.bbox[3]) for f in fragments])
    best_cut = None
    best_gap = MIN_HORIZONTAL_GAP
    for top, bottom in zip(merged, merged[1:]):
        gap = bottom[0] - top[1]
        if gap > best_gap:
            best_cut, best_gap = (top[1] + bottom[0]) / 2, gap
    return best_cut


def _reading_blocks(fragments: list[Fragment], depth: int = 0) -> list[tuple[str, list[Fragment]]]:
    """
    Decoupe une page en REGIONS successives, dans l'ordre ou on les lit, en
    marquant chacune « table » ou « flow ».

    Meme decoupe recursive que decrite en tete de module : on privilegie la
    coupure VERTICALE (colonnes) quand une gouttiere franche existe, sinon une
    coupure HORIZONTALE (bandeau pleine largeur). C'est ce qui permet a un
    titre courant sur deux colonnes d'etre lu avant elles, et a chaque colonne
    d'etre lue entierement avant de passer a la suivante -- au lieu d'alterner
    gauche/droite ligne par ligne comme le faisait le tri par position.

    Le test « est-ce un tableau ? » passe AVANT toute coupure : un tableau a,
    lui aussi, des blancs verticaux entre ses colonnes, et se ferait couper en
    tranches inutilisables si on decoupait d'abord (constate sur le Tableau 2,
    page 9 du PDF de reference).

    `depth` borne la recursion : un PDF pathologique ne doit pas pouvoir faire
    exploser la pile.
    """
    if not fragments:
        return []
    ordered = sorted(fragments, key=lambda f: (f.bbox[1], f.bbox[0]))
    if len(fragments) == 1 or depth > 12:
        return [("flow", ordered)]
    if _looks_like_table(fragments):
        return [("table", ordered)]

    cut = _find_gutter(fragments)
    if cut is not None:
        left = [f for f in fragments if f.x_center < cut]
        right = [f for f in fragments if f.x_center >= cut]
        if left and right:
            return _reading_blocks(left, depth + 1) + _reading_blocks(right, depth + 1)

    cut = _find_horizontal_split(fragments)
    if cut is not None:
        top = [f for f in fragments if f.y_center < cut]
        bottom = [f for f in fragments if f.y_center >= cut]
        if top and bottom:
            return _reading_blocks(top, depth + 1) + _reading_blocks(bottom, depth + 1)

    return [("flow", ordered)]


def _reading_order(fragments: list[Fragment]) -> list[Fragment]:
    """Les fragments d'une page, à plat, dans le bon ordre de lecture."""
    ordered: list[Fragment] = []
    for _kind, region in _reading_blocks(fragments):
        ordered.extend(region)
    return ordered


def count_columns(fragments: list[Fragment], depth: int = 0) -> int:
    """
    Nombre de colonnes réellement détectées sur une page (diagnostic, et
    testable indépendamment de l'ordre de lecture).

    Suit exactement la même découpe récursive que `_reading_blocks` -- y
    compris la coupure HORIZONTALE. Sans elle, une page dont le titre courant
    traverse toute la largeur serait comptée « une colonne » alors que son
    corps en a bien deux : le bandeau pleine largeur bouche la gouttière tant
    qu'on ne l'a pas mis de côté.
    """
    horizontal = [f for f in fragments if not f.rotated]
    if not horizontal or depth > 12:
        return 1 if horizontal else 0

    cut = _find_gutter(horizontal)
    if cut is not None:
        left = [f for f in horizontal if f.x_center < cut]
        right = [f for f in horizontal if f.x_center >= cut]
        if left and right:
            return count_columns(left, depth + 1) + count_columns(right, depth + 1)

    cut = _find_horizontal_split(horizontal)
    if cut is not None:
        top = [f for f in horizontal if f.y_center < cut]
        bottom = [f for f in horizontal if f.y_center >= cut]
        if top and bottom:
            # Une page vaut le nombre de colonnes de sa bande la plus riche :
            # un titre pleine largeur au-dessus d'un corps en deux colonnes
            # reste une page à deux colonnes.
            return max(count_columns(top, depth + 1), count_columns(bottom, depth + 1))
    return 1


# ---------------------------------------------------------------------------
# Tableaux : reconstruction lignes / colonnes
# ---------------------------------------------------------------------------

# Un tableau se lit sur deux axes perpendiculaires. Selon qu'il est imprimé
# normalement ou en paysage (texte pivoté de 90°), ces axes ne sont pas les
# mêmes -- mais TOUT le reste de la reconstruction est identique. `_TableAxes`
# décrit donc simplement « où est l'axe des lignes, où est celui des
# colonnes », et un seul algorithme sert aux deux cas.
#
#   - tableau normal  : les lignes se succèdent vers le bas (y), les colonnes
#     vers la droite (x0 croissant) ;
#   - tableau pivoté  : la page étant portrait et le tableau paysage, les
#     lignes se succèdent vers la droite (x) et les colonnes vers le HAUT
#     (bbox[3] décroissant) -- d'où `column_reverse`.


@dataclass(frozen=True)
class _TableAxes:
    row_position: "callable"
    column_position: "callable"
    column_reverse: bool


_HORIZONTAL_AXES = _TableAxes(
    row_position=lambda f: f.y_center,
    column_position=lambda f: f.bbox[0],
    column_reverse=False,
)
_ROTATED_AXES = _TableAxes(
    row_position=lambda f: f.x_center,
    column_position=lambda f: f.bbox[3],
    column_reverse=True,
)


def _cluster(values: list[float], tolerance: float) -> list[float]:
    """
    Regroupe des positions proches et renvoie le centre de chaque groupe.
    Utilisé pour retrouver les lignes (puis les colonnes) d'un tableau à partir
    des positions réelles des cellules, plutôt que de supposer une grille
    régulière qui n'existe pas dans un tableau sans filets.
    """
    if not values:
        return []
    ordered = sorted(values)
    centers: list[float] = []
    group: list[float] = [ordered[0]]
    for value in ordered[1:]:
        if value - group[-1] <= tolerance:
            group.append(value)
        else:
            centers.append(median(group))
            group = [value]
    centers.append(median(group))
    return centers


def _nearest_index(value: float, centers: list[float]) -> int:
    return min(range(len(centers)), key=lambda i: abs(centers[i] - value))


def _cluster_rows(
    fragments: list[Fragment], axes: _TableAxes
) -> tuple[list[float], list[list[Fragment]]]:
    """Regroupe les fragments en lignes de tableau, le long de l'axe des lignes."""
    centers = _cluster([axes.row_position(f) for f in fragments], ROW_CLUSTER_TOLERANCE)
    rows: list[list[Fragment]] = [[] for _ in centers]
    for f in fragments:
        rows[_nearest_index(axes.row_position(f), centers)].append(f)
    return centers, rows


def _column_centers(rows: list[list[Fragment]], axes: _TableAxes) -> list[float]:
    """
    Retrouve la position des colonnes d'un tableau sans filets, à partir des
    seules positions réelles des cellules.

    Le point délicat, constaté sur le Tableau 1 du PDF de référence : un
    en-tête de colonne long (« Intelligence measure ») ne commence PAS au même
    endroit que les données de sa propre colonne (« UEE and GPA »), parce qu'il
    est composé sur plusieurs lignes. Un simple regroupement par proximité
    créait donc 11 colonnes là où il n'y en a que 9, et les données se
    retrouvaient orphelines de leur étiquette.

    La correction s'appuie sur le NOMBRE de lignes qui confirment chaque
    position : une vraie colonne est alimentée par la quasi-totalité des lignes
    du tableau (28 à 30 sur 30 dans ce cas), une position d'en-tête isolée par
    une ou deux seulement. On ne garde donc que les positions largement
    confirmées, et les cellules d'en-tête rejoignent ensuite la colonne réelle
    la plus proche.
    """
    if not rows:
        return []
    candidates = _cluster(
        [axes.column_position(f) for row in rows for f in row], COLUMN_CLUSTER_TOLERANCE
    )
    if not candidates:
        return []

    support = [0] * len(candidates)
    for row in rows:
        for index in {_nearest_index(axes.column_position(f), candidates) for f in row}:
            support[index] += 1

    threshold = max(2, int(len(rows) * MIN_COLUMN_SUPPORT))
    kept = [c for c, s in zip(candidates, support) if s >= threshold]
    kept.sort(reverse=axes.column_reverse)
    return kept


def _is_group_heading(cells: list[str], next_cells: list[str] | None) -> bool:
    """
    Distingue un INTITULÉ DE GROUPE (une étude qui se subdivise en « Study 1 »,
    « Study 2 »…) d'une simple SUITE de cellule renvoyée à la ligne. Les deux se
    présentent pareil : une ligne dont seule la première colonne est remplie.

    Deux signaux typographiques réels du document, jamais une supposition :
      - l'intitulé de groupe se termine par « : », marque universelle d'une
        liste qui suit (« Blanchard-Fields… October 2011: ») ;
      - ou bien la ligne suivante est INDENTÉE (le PDF place une cadratin
        U+2003 devant « Study 1 »), ce qui la désigne comme sous-entrée --
        c'est le cas de « Symington (1935) », qui ne se termine pourtant par
        aucun deux-points.
    """
    if cells[0].strip().endswith(":"):
        return True
    if next_cells is None:
        return False
    following = next_cells[0]
    return bool(following) and following[:1] in {" ", " ", "\t", " "}


def _merge_wrapped_rows(table: list[list[str]]) -> list[list[str]]:
    """
    Recolle les lignes de tableau coupées par un retour à la ligne.

    Une cellule trop longue pour sa colonne (« Carothers, Borkowski, Burke /
    Lefever, and Whitman (2005); S. S. Carothers, personal communication, /
    September 2011 ») est rendue par PyMuPDF comme plusieurs lignes successives
    dont une seule porte les données. Sans ce recollage, le nom de l'étude
    arrive tronqué dans la traduction et des lignes fantômes (« Study :
    (2008) ») viennent s'intercaler entre deux vraies lignes.

    Chaque cellule est recollée dans SA colonne : « design » revient ainsi à la
    fin de « WAIS-R Vocabulary and Block », pas au bout du nom de l'étude.
    """
    if not table:
        return table
    width = max(len(r) for r in table)
    full = max(sum(1 for c in r if c.strip()) for r in table)

    merged: list[list[str]] = []
    for index, cells in enumerate(table):
        filled = sum(1 for c in cells if c.strip())
        following = table[index + 1] if index + 1 < len(table) else None
        is_sparse = 0 < filled < full
        previous_complete = bool(merged) and sum(1 for c in merged[-1] if c.strip()) >= full

        if is_sparse and previous_complete and not _is_group_heading(cells, following):
            for column in range(width):
                addition = cells[column].strip() if column < len(cells) else ""
                if not addition:
                    continue
                current = merged[-1][column]
                merged[-1][column] = f"{current} {addition}".strip() if current else addition
            continue
        merged.append(list(cells))
    return merged


def _build_table(
    fragments: list[Fragment], axes: _TableAxes
) -> tuple[list[list[str]], str] | None:
    """
    Reconstruit un tableau à partir de ses fragments et de ses deux axes.

    Retourne (lignes, légende) ou None si ça ne ressemble pas à un tableau.
    """
    if len(fragments) < MIN_TABLE_ROWS:
        return None
    if _numeric_share(fragments) < MIN_TABLE_NUMERIC_SHARE:
        return None  # voir _numeric_share : sinon une page scannee passe pour un tableau

    row_centers, rows = _cluster_rows(fragments, axes)
    if len(row_centers) < MIN_TABLE_ROWS:
        return None

    # Seules les lignes les mieux remplies servent de référence pour situer les
    # colonnes : une ligne d'intitulé de groupe fausserait la grille.
    widest = max(len(r) for r in rows)
    if widest < MIN_TABLE_COLUMNS:
        return None
    column_centers = _column_centers([r for r in rows if len(r) == widest], axes)
    if len(column_centers) < MIN_TABLE_COLUMNS:
        return None

    table: list[list[str]] = []
    caption = ""
    for row in rows:
        cells = [""] * len(column_centers)
        for f in sorted(row, key=lambda f: -axes.column_position(f) if axes.column_reverse
                        else axes.column_position(f)):
            index = _nearest_index(axes.column_position(f), column_centers)
            cells[index] = f"{cells[index]} {f.text}".strip() if cells[index] else f.text
        if not any(c.strip() for c in cells):
            continue
        joined = " ".join(c for c in cells if c).strip()
        # La légende (« Table 1.  Overview… ») est une ligne isolée, hors grille.
        if not caption and CAPTION_RE.match(joined) and sum(1 for c in cells if c) == 1:
            caption = joined
            continue
        table.append(cells)

    table = _merge_wrapped_rows(table)
    if len(table) < MIN_TABLE_ROWS:
        return None
    return table, caption


def _numeric_share(fragments: list[Fragment]) -> float:
    """
    Part des cellules a dominante chiffree.

    C'est le garde-fou qui separe un vrai tableau de donnees d'une page de
    livre SCANNE, dont l'OCR produit des fragments courts et epars qui imitent
    a s'y meprendre la geometrie d'un tableau. Mesure sur les documents reels
    de l'utilisateur, l'ecart ne laisse aucune place au doute :

        vrais tableaux (article)      0,35 a 0,72
        pages de livre scanne         0,01 a 0,07

    La contrepartie est assumee et vaut d'etre dite : un tableau PUREMENT
    textuel, sans chiffres, ne sera pas reconnu comme tel et restera traite
    comme du texte courant. C'est le bon sens du risque -- un tableau laisse en
    prose se lit encore, alors qu'une page de roman decoupee en fausses lignes
    de tableau serait, elle, illisible.
    """
    if not fragments:
        return 0.0
    numeric = 0
    for fragment in fragments:
        text = fragment.text.strip()
        if not text:
            continue
        digits = sum(1 for c in text if c.isdigit())
        if digits / len(text) >= MIN_CELL_DIGIT_SHARE:
            numeric += 1
    return numeric / len(fragments)


def _looks_like_table(fragments: list[Fragment]) -> bool:
    """
    Une région de prose, ou un tableau NON pivoté ?

    Deux signaux conjoints, mesurés sur le PDF de référence plutôt que
    supposés -- il en faut vraiment DEUX, chacun pris seul se trompant sur un
    cas réel de ce document :

      1. **Des cellules courtes.** Une ligne de prose occupe toute la largeur
         de sa colonne ; une cellule de tableau est brève. Part de fragments
         courts mesurée ici : 0,88 à 0,98 dans les tableaux, 0,00 à 0,29 dans
         la prose. Ce test seul se fait piéger par la section « Notes » en fin
         d'article (0,92) : ses appels de note (« 1. », « 2. ») sont courts,
         mais le texte qui suit ne l'est pas.

      2. **Au moins trois colonnes distinctes.** C'est ce qui sépare un vrai
         tableau (5 à 12 colonnes ici) d'une liste numérotée à deux niveaux
         comme cette même section « Notes » (2 colonnes), ou d'une formule
         mise en retrait (2 colonnes également). Le prix de ce garde-fou est
         assumé : un tableau à deux colonnes seulement sera traité comme du
         texte courant -- il reste lisible, alors qu'une liste de notes
         convertie en fausses lignes de tableau ne le serait pas.

    Ce test est appliqué AVANT toute découpe en colonnes (voir
    `_reading_blocks`) : un tableau contient lui-même des blancs verticaux
    entre ses colonnes, et se ferait sinon couper en tranches inutilisables
    avant d'avoir pu être reconnu (constaté sur le Tableau 2, page 9).
    """
    if len(fragments) < MIN_TABLE_ROWS * 2:
        return False
    width = max(f.bbox[2] for f in fragments) - min(f.bbox[0] for f in fragments)
    if width <= 0:
        return False

    short = sum(1 for f in fragments if (f.bbox[2] - f.bbox[0]) < MAX_TABLE_CELL_SHARE * width)
    if short < len(fragments) * MIN_TABLE_SHORT_SHARE:
        return False

    if _numeric_share(fragments) < MIN_TABLE_NUMERIC_SHARE:
        return False

    _, rows = _cluster_rows(fragments, _HORIZONTAL_AXES)
    if len(rows) < MIN_TABLE_ROWS:
        return False
    aligned = [r for r in rows if len(r) >= MIN_TABLE_COLUMNS] or rows
    return len(_column_centers(aligned, _HORIZONTAL_AXES)) >= MIN_TABLE_COLUMNS_FLAT


def _split_header(table: list[list[str]]) -> tuple[list[str], list[list[str]]]:
    """
    Sépare l'en-tête du corps. Un en-tête peut tenir sur PLUSIEURS lignes
    (« Number of items in » / « religiosity measure ») : on prend les premières
    lignes tant qu'elles ne contiennent aucune valeur purement numérique, signe
    qu'on est déjà dans les données.
    """
    header: list[str] = []
    start = 0
    for row in table[:3]:
        if any(NUMERIC_CELL_RE.fullmatch(c.strip()) for c in row if c.strip()):
            break
        merged = []
        for index, cell in enumerate(row):
            previous = header[index] if index < len(header) else ""
            merged.append(f"{previous} {cell}".strip())
        header = merged
        start += 1
    if not header:
        return [""] * len(table[0]), table
    return header, table[start:]


def _linearize_table(table: list[list[str]], caption: str, page_number: int) -> str:
    """
    Remet un tableau à plat : une ligne de texte par ligne de tableau, chaque
    valeur précédée de son en-tête de colonne -- « Study : Bender (1968) ;
    Total n : 96 ; … ».

    C'est la demande explicite de l'utilisateur (11/09/2026) : « re classer les
    données des tableaux et les ré organiser autrement, c'est-à-dire en ligne
    une à la suite des autres tout en étant cohérent ». Une ligne de tableau
    lue telle quelle (« Bender (1968) 96 1.0 UEE and GPA… ») ne veut rien dire
    une fois traduite ; étiquetée, elle redevient une phrase que le moteur de
    traduction peut traiter et qu'un lecteur peut comprendre.

    Une ligne qui n'a qu'une seule cellule (intitulé de groupe) est écrite telle
    quelle, sans étiquette : elle annonce les lignes suivantes.
    """
    header, body = _split_header(table)
    lines: list[str] = [f"## {caption}" if caption else f"## Tableau (page {page_number})"]
    for row in body:
        filled = [(i, c.strip()) for i, c in enumerate(row) if c.strip()]
        if not filled:
            continue
        if len(filled) == 1:
            lines.append(filled[0][1])
            continue
        parts = []
        for index, cell in filled:
            label = header[index].strip() if index < len(header) else ""
            parts.append(f"{label} : {cell}" if label else cell)
        lines.append(" ; ".join(parts))
    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# Notes de bas de page
# ---------------------------------------------------------------------------


def _looks_like_footnote(fragment: Fragment, body_size: float, page_height: float) -> bool:
    """
    Une note de bas de page se reconnaît à DEUX signaux conjoints, jamais un
    seul : un corps nettement plus petit que le texte courant, ET une position
    dans le bas de la page. La taille seule attraperait les indices et exposants
    du corps du texte ; la position seule attraperait la dernière phrase d'un
    paragraphe qui finit en bas de colonne.
    """
    if body_size <= 0:
        return False
    if fragment.size >= body_size * FOOTNOTE_SIZE_RATIO:
        return False
    return fragment.bbox[1] >= page_height * FOOTNOTE_ZONE_RATIO


# ---------------------------------------------------------------------------
# Assemblage : une page -> des régions
# ---------------------------------------------------------------------------


def _join_lines(lines: list[str]) -> str:
    """
    Recolle les lignes d'un paragraphe en réparant la césure de fin de ligne
    (« intelli- / gence » -> « intelligence »), qui sinon survivrait jusqu'au
    moteur de traduction sous forme de deux morceaux de mot.
    """
    text = ""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if not text:
            text = line
        elif text.endswith("-") and not text.endswith(("--", " -")):
            text = text[:-1] + line
        else:
            text += " " + line
    return re.sub(r"\s+", " ", text).strip()


def _group_paragraphs(fragments: list[Fragment]) -> list[str]:
    """
    Recolle les lignes consécutives en paragraphes.

    Les fragments arrivent déjà dans le bon ordre de lecture ; on coupe entre
    deux lignes quand l'écart vertical dépasse nettement l'interligne courant,
    ou quand on repart vers le haut (changement de colonne).
    """
    if not fragments:
        return []
    gaps = [
        b.bbox[1] - a.bbox[3]
        for a, b in zip(fragments, fragments[1:])
        if b.bbox[1] > a.bbox[1] and abs(b.bbox[0] - a.bbox[0]) < 40
    ]
    typical = median(gaps) if gaps else 2.0
    threshold = max(typical + 3.0, typical * 2.2)

    paragraphs: list[str] = []
    current: list[str] = [fragments[0].text]
    for previous, fragment in zip(fragments, fragments[1:]):
        same_flow = fragment.bbox[1] >= previous.bbox[1] - 2
        if not same_flow or fragment.bbox[1] - previous.bbox[3] > threshold:
            paragraphs.append(_join_lines(current))
            current = [fragment.text]
        else:
            current.append(fragment.text)
    paragraphs.append(_join_lines(current))
    return [p for p in paragraphs if p.strip()]


def _rotated_regions(
    fragments: list[Fragment], page_index: int, report: LayoutReport
) -> list[Region]:
    """
    Traite le texte pivoté d'une page : un vrai tableau paysage, ou un simple
    bandeau vertical.
    """
    built = _build_table(fragments, _ROTATED_AXES)
    if built is not None:
        table, caption = built
        report.tables_found += 1
        report.table_rows_total += len(table)
        return [
            Region(
                kind="table",
                text=_linearize_table(table, caption, page_index + 1),
                page_index=page_index,
                label=caption,
            )
        ]
    # Pivoté mais pas un tableau : un filigrane de dépôt, un numéro de revue
    # dans la marge... Jamais jeté en silence -- rangé avec les notes, où il ne
    # coupera aucun paragraphe.
    text = _join_lines([f.text for f in _reading_order(fragments)])
    if not text:
        return []
    report.footnotes_found += 1
    return [Region(kind="footnote", text=text, page_index=page_index)]


def _analyze_page(
    fragments: list[Fragment],
    height: float,
    body_size: float,
    page_index: int,
    report: LayoutReport,
) -> list[Region]:
    """
    Une page -> ses regions.

    `body_size` est la taille de corps du DOCUMENT, pas de la page. La nuance a
    une vraie consequence, constatee sur les pages 5 a 7 du PDF de reference :
    ces pages ne contiennent qu'un tableau paysage, un numero de page et un
    filigrane de depot. Mesuree sur la page seule, la taille « dominante » y
    devient celle du filigrane lui-meme, qui cesse alors d'etre reconnu comme
    note de bas de page et retombe en plein milieu du corps du texte -- juste
    entre un paragraphe coupe et sa suite, empechant precisement le recollage
    que ce projet cherche a faire.
    """
    if not fragments:
        report.pages_without_text += 1
        report.columns_per_page.append(0)
        return []

    rotated = [f for f in fragments if f.rotated]
    horizontal = [f for f in fragments if not f.rotated]

    regions: list[Region] = []
    if rotated:
        regions.extend(_rotated_regions(rotated, page_index, report))

    if not horizontal:
        report.columns_per_page.append(0)
        return regions

    report.columns_per_page.append(count_columns(horizontal))

    for kind, area in _reading_blocks(horizontal):
        # Un tableau NON pivoté (le Tableau 2 de ce PDF, imprimé normalement en
        # pleine largeur au-dessus des deux colonnes) doit être reconnu ici,
        # avant d'être recollé en « paragraphes » où ses cellules formeraient
        # une bouillie de chiffres.
        if kind == "table":
            built = _build_table(area, _HORIZONTAL_AXES)
            if built is not None:
                table, caption = built
                report.tables_found += 1
                report.table_rows_total += len(table)
                regions.append(
                    Region(
                        kind="table",
                        text=_linearize_table(table, caption, page_index + 1),
                        page_index=page_index,
                        label=caption,
                    )
                )
                continue

        body = [f for f in area if not _looks_like_footnote(f, body_size, height)]
        notes = [f for f in area if _looks_like_footnote(f, body_size, height)]

        for paragraph in _group_paragraphs(body):
            kind = "caption" if CAPTION_RE.match(paragraph) else "body"
            regions.append(Region(kind=kind, text=paragraph, page_index=page_index))
        for paragraph in _group_paragraphs(notes):
            regions.append(Region(kind="footnote", text=paragraph, page_index=page_index))
            report.footnotes_found += 1

    return regions


def analyze_pdf(path: Path, on_progress=None) -> LayoutDocument:
    """
    Point d'entree : un PDF -> ses regions, dans le bon ordre de lecture.

    Deux temps, et c'est necessaire : on lit d'abord toutes les pages, ce qui
    permet de mesurer la taille de corps du document entier, puis seulement on
    attribue les roles. Une page pauvre en texte (une page de tableau, par
    exemple) ne fournit pas a elle seule de reference fiable -- voir
    `_analyze_page`.

    `on_progress(page_courante, total_pages)` est appele page par page, comme
    `extract.extract_text`.
    """
    try:
        import pymupdf
    except ImportError:  # PyMuPDF anterieur a 1.24
        import fitz as pymupdf

    document = LayoutDocument()
    pages: list[tuple[float, list[Fragment]]] = []
    with pymupdf.open(path) as pdf:
        document.report.total_pages = pdf.page_count
        for number, page in enumerate(pdf, start=1):
            pages.append((page.rect.height, _page_fragments(page)))
            if on_progress is not None:
                on_progress(number, pdf.page_count)

    sizes = [f.size for _h, frags in pages for f in frags if not f.rotated and f.size > 0]
    body_size = median(sizes) if sizes else 0.0

    for page_index, (height, fragments) in enumerate(pages):
        document.regions.extend(
            _analyze_page(fragments, height, body_size, page_index, document.report)
        )
    document.regions = _drop_duplicate_notes(document.regions, document.report)
    return document


def _drop_duplicate_notes(regions: list[Region], report: LayoutReport) -> list[Region]:
    """
    Ne garde qu'un exemplaire d'une note repetee a l'identique de page en page.

    Un filigrane de depot (« Downloaded from pdf.highwire.org by guest on
    August 12, 2013 ») est correctement ecarte du corps du texte page apres
    page, mais se retrouvait alors recopie 27 fois dans la section des notes --
    un doublon inutile, exactement ce que l'utilisateur demande d'eliminer. La
    premiere occurrence est conservee (l'information n'est jamais perdue), les
    suivantes sont comptees et retirees.
    """
    seen: set[str] = set()
    kept: list[Region] = []
    for region in regions:
        if region.kind != "footnote":
            kept.append(region)
            continue
        key = re.sub(r"\d+", "#", re.sub(r"\s+", " ", region.text).strip().lower())
        if key in seen:
            report.duplicate_notes_removed += 1
            continue
        seen.add(key)
        kept.append(region)
    report.footnotes_found = sum(1 for r in kept if r.kind == "footnote")
    return kept


def is_layout_useful(document: LayoutDocument) -> bool:
    """
    L'analyse geometrique doit-elle REMPLACER l'extraction classique ?

    Deliberement exigeant, et pour une raison concrete : les livres deja
    traduits avec succes par ce projet doivent continuer a suivre exactement le
    chemin eprouve. Mesure sur la bibliotheque reelle de l'utilisateur, un seuil
    laxiste (« au moins une page a deux colonnes ») faisait basculer des romans
    entiers sur le nouveau chemin pour 2 pages sur 278 -- en l'occurrence une
    page de titre et une table des matieres, jamais du vrai texte en colonnes.

    On exige donc que le document soit MAJORITAIREMENT concerne :
      - une vraie part de pages en plusieurs colonnes (un article scientifique
        l'est de bout en bout : 30 pages sur 30 dans le cas de reference) ;
      - ou des tableaux sur une part significative des pages.
    Un livre dont seules la couverture et le sommaire sont mis en colonnes
    reste sur l'extraction classique, ou il n'a jamais pose probleme.
    """
    report = document.report
    pages = max(1, report.total_pages)
    if pages < MIN_PAGES_FOR_LAYOUT:
        return False
    if report.multi_column_pages >= pages * MIN_MULTI_COLUMN_SHARE:
        return True
    return report.tables_found >= pages * MIN_TABLE_PAGE_SHARE


def probe_pdf(path: Path, max_pages: int = PROBE_PAGES) -> LayoutDocument:
    """
    Verdict RAPIDE sur l'interet d'analyser la mise en page, sur un echantillon
    de pages reparties dans tout le document.

    Sans cette sonde, un livre de 300 pages payait l'analyse complete (jusqu'a
    30 secondes mesurees sur la bibliotheque de l'utilisateur) avant qu'on ne
    decide... de ne pas s'en servir et de repartir sur l'extraction classique.
    La sonde ramene ce cout a une poignee de pages ; l'analyse complete n'a plus
    lieu que lorsqu'elle va reellement servir.

    L'echantillon saute la premiere page : couverture ou page de titre, elle est
    rarement representative de la mise en page du corps.
    """
    try:
        import pymupdf
    except ImportError:  # PyMuPDF anterieur a 1.24
        import fitz as pymupdf

    document = LayoutDocument()
    with pymupdf.open(path) as pdf:
        total = pdf.page_count
        document.report.total_pages = total
        start = 1 if total > 2 else 0
        step = max(1, (total - start) // max_pages)
        sampled = list(range(start, total, step))[:max_pages]
        if not sampled:
            return document
        for index in sampled:
            fragments = [f for f in _page_fragments(pdf[index]) if not f.rotated]
            document.report.columns_per_page.append(count_columns(fragments))
            rotated = [f for f in _page_fragments(pdf[index]) if f.rotated]
            if rotated and _build_table(rotated, _ROTATED_AXES) is not None:
                document.report.tables_found += 1
            elif any(kind == "table" for kind, _ in _reading_blocks(fragments)):
                document.report.tables_found += 1
    # Le verdict se lit sur l'echantillon, pas sur le document entier : on
    # ramene donc le total au nombre de pages reellement examinees.
    document.report.total_pages = len(document.report.columns_per_page)
    return document
