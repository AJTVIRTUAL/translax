"""
Valeurs de design partagées par toute l'interface -- demande explicite de
l'utilisateur (11/11/2026) : « prends la valeur des coins arrondis de la
fenêtre et applique-la dans toute l'application (boutons, barres de
progression…) pour une harmonie ».

`RADIUS` est donc LE rayon de TRANSLAX : celui que Windows 11 applique à la
fenêtre elle-même (8 px, imposé par le système -- voir ui/window_effects.py).
Cartes, boutons, champs, listes, zone de dépôt, journal, bulles d'aide :
tous reprennent exactement cette valeur. Seules deux exceptions, pour une
raison de forme et pas de goût :

  - `RADIUS_SMALL` (moitié) pour les éléments minuscules -- une case de
    16 px avec 8 px de rayon deviendrait un rond (une case radio), une
    poignée de barre de défilement de 6 px de large aussi ;
  - les « pilules » (barre de progression, interrupteurs) : arrondi complet,
    hauteur / 2, la forme même de l'élément.

`ui/styles.qss` ne sait pas lire de variable : la valeur y est écrite en
dur (8px / 4px) -- `tests/test_theme.py` vérifie que chaque `border-radius`
de la feuille de style vaut bien l'une de ces deux valeurs, pour que
l'harmonie ne se perde pas au fil des retouches.
"""
from __future__ import annotations

RADIUS = 8
RADIUS_SMALL = RADIUS // 2

# Couleurs reprises de ui/styles.qss, pour les éléments dessinés à la main.
BACKGROUND = "#12151c"
SURFACE = "#1a1f2a"
FIELD = "#10141b"
BORDER = "#2b3444"
BORDER_SOFT = "#232b38"
TEXT = "#e6eaf2"
TEXT_SOFT = "#c3ccdb"
TEXT_MUTED = "#8b96a8"
ACCENT = "#3b7dfb"
ACCENT_LIGHT = "#4f8cff"
ACCENT_END = "#59d4c4"   # fin du dégradé de la barre de progression

# Grille d'espacement : multiples de 4 px, partout les mêmes.
PAGE_MARGINS = (28, 20, 28, 24)   # gauche, haut, droite, bas
PAGE_SPACING = 16                 # entre deux blocs d'une page
CARD_MARGINS = (20, 16, 20, 18)
CARD_SPACING = 12                 # entre le titre d'une carte et son contenu
ROW_SPACING = 10                  # entre deux lignes / deux éléments d'une ligne
FIELD_LABEL_WIDTH = 120           # colonne des libellés : alignée sur TOUTES les cartes

# Animations : courtes, jamais un obstacle à l'usage.
HOVER_IN_MS = 140
HOVER_OUT_MS = 220
TOGGLE_MS = 170
PAGE_TRANSITION_MS = 460
