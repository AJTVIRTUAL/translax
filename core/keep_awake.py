"""
Empêche la mise en veille du système (et l'extinction de l'écran) pendant
qu'une traduction est en cours.

Pourquoi c'est nécessaire et pas juste confortable : une traduction peut
durer des heures. Si le système se met en VEILLE (pas juste l'écran qui
s'éteint), Windows/Mac suspendent tous les processus en cours d'exécution
— y compris celui-ci. La traduction ne plante pas, mais elle se fige
purement et simplement jusqu'au réveil de la machine, potentiellement des
heures plus tard sans que rien n'avance. Empêcher l'extinction de l'écran
seul n'aurait réglé qu'un problème de confort, pas celui-là.

Deux mécanismes, un par OS, tous deux déjà fournis par le système
(aucune dépendance supplémentaire à installer) :
  - Windows : `SetThreadExecutionState`, l'API Win32 officielle prévue
    pour ça (utilisée par les lecteurs vidéo, les gestionnaires de
    téléchargement...).
  - macOS   : `caffeinate`, l'utilitaire en ligne de commande intégré au
    système depuis toujours, lancé en arrière-plan pour la durée du job
    puis arrêté.

Sur les autres systèmes (Linux...), `start()` ne fait rien : pas de
mécanisme équivalent implémenté, mais ça ne bloque rien ni ne plante --
la traduction fonctionne simplement sans cette protection.
"""
from __future__ import annotations

import subprocess
import sys


class KeepAwake:
    """
    Utilisation :
        awake = KeepAwake()
        awake.start()   # au début d'une traduction
        ...
        awake.stop()    # à la fin (terminée, annulée, ou en erreur)

    Aussi utilisable comme context manager : `with KeepAwake(): ...`.
    """

    def __init__(self) -> None:
        self._active = False
        self._mac_process: subprocess.Popen | None = None

    @property
    def active(self) -> bool:
        return self._active

    def start(self) -> None:
        if self._active:
            return
        try:
            if sys.platform == "win32":
                self._start_windows()
            elif sys.platform == "darwin":
                self._start_mac()
        except Exception:
            # Ne doit jamais empêcher la traduction elle-même de démarrer :
            # au pire, la protection anti-veille est absente cette fois-ci.
            return
        self._active = True

    def stop(self) -> None:
        if not self._active:
            return
        try:
            if sys.platform == "win32":
                self._stop_windows()
            elif sys.platform == "darwin":
                self._stop_mac()
        finally:
            self._active = False

    def __enter__(self) -> "KeepAwake":
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    # ------------------------------------------------------------ Windows
    def _start_windows(self) -> None:
        import ctypes

        ES_CONTINUOUS = 0x80000000
        ES_SYSTEM_REQUIRED = 0x00000001
        ES_DISPLAY_REQUIRED = 0x00000002
        ctypes.windll.kernel32.SetThreadExecutionState(  # type: ignore[attr-defined]
            ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
        )

    def _stop_windows(self) -> None:
        import ctypes

        ES_CONTINUOUS = 0x80000000
        # Revient au comportement normal : Windows peut de nouveau décider
        # de mettre le système en veille selon ses propres réglages.
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)  # type: ignore[attr-defined]

    # ---------------------------------------------------------------- Mac
    def _start_mac(self) -> None:
        # -d : empêche l'extinction de l'écran
        # -i : empêche la veille système pour cause d'inactivité
        # Fonctionne que la machine soit sur secteur ou sur batterie -- le
        # rappel de brancher le chargeur (voir ui/main_window.py) est là
        # pour la consommation, pas parce que ce mécanisme l'exigerait.
        self._mac_process = subprocess.Popen(["caffeinate", "-d", "-i"])

    def _stop_mac(self) -> None:
        if self._mac_process is not None:
            self._mac_process.terminate()
            self._mac_process = None
