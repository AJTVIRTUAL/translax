"""
Bat un « pouls » à chaque pas de génération à l'intérieur d'UN segment, pour
distinguer « ce segment est juste long » de « le logiciel s'est figé
silencieusement » -- sans jamais influencer la traduction elle-même (voir
`core/translate.py`, `_HeartbeatCriteria`, où ce pouls est branché).

Pourquoi c'est nécessaire : `translate.Progress` (voir `core/translate.py`)
n'avance qu'une fois un segment ENTIER traduit. Pour un segment long ou un
modèle plus lent (1.3B, 3.3B), ça peut représenter plusieurs dizaines de
secondes, voire plus, sans le moindre signal -- impossible de savoir si ça
travaille encore ou si c'est bloqué. `Heartbeat` comble ce trou avec un
signal plus fin : chaque mot produit par NLLB (beam search compris) met à
jour un horodatage, lu depuis le thread d'interface (`TranslationWorker.
heartbeat`, voir `ui/worker.py`) pour répondre à « est-ce que ça avance ? »
-- bouton Reboost, ou vérification automatique après 15 minutes sans
activité apparente (voir `ui/main_window.py`).

Lecture/écriture d'un float et d'un int : atomique sous le GIL, pas besoin
de verrou pour cet usage lecture (thread principal) / écriture (thread de
travail) -- même raisonnement que `core/keep_awake.py`.
"""
from __future__ import annotations

import time


class Heartbeat:
    def __init__(self) -> None:
        self._last = time.monotonic()
        self._ticks = 0

    def beat(self) -> None:
        """Appelée à chaque pas de décodage (voir `translate._HeartbeatCriteria`)."""
        self._last = time.monotonic()
        self._ticks += 1

    def seconds_since_beat(self) -> float:
        """Temps écoulé depuis le dernier pas de génération observé."""
        return time.monotonic() - self._last

    def reset(self) -> None:
        self._last = time.monotonic()
        self._ticks = 0

    @property
    def ticks(self) -> int:
        """Nombre total de pas de décodage observés depuis la création ou le
        dernier `reset()` -- purement informatif, jamais utilisé pour décider
        d'un blocage (seul `seconds_since_beat()` sert à ça)."""
        return self._ticks
