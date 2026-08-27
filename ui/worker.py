"""
Exécution de la traduction dans un thread séparé.

Sans ça, la fenêtre se fige pendant toute la durée du travail (des heures sur
un livre) : Qt ne peut plus repeindre, Windows affiche « ne répond pas », et
le bouton Stop devient inutilisable.

Le worker est un simple QObject déplacé dans un QThread. Il n'appelle qu'une
fonction du moteur, `pipeline.run_job`, et retransmet ses callbacks sous
forme de signaux Qt. Les signaux émis depuis un thread secondaire vers un
objet du thread principal sont mis en file d'attente automatiquement par
Qt : c'est ce qui rend la mise à jour de l'UI sûre ici.

Cas particulier : `on_page_cleanup`. Contrairement aux autres callbacks
(purement informatifs), celui-ci doit RENVOYER une décision de
l'utilisateur ("clean"/"original"/"cancel") -- et cette décision ne peut
venir que d'une boîte de dialogue, donc du thread principal. Le signal Qt
seul ne suffit pas ici : émettre ne bloque pas, alors que `pipeline.run_job`
(dans ce thread de travail) a besoin d'attendre la réponse avant de
continuer. Le pont est un `threading.Event` : `_on_page_cleanup` émet le
signal puis attend sur l'Event ; `set_cleanup_decision`, appelée depuis le
thread principal une fois la boîte de dialogue fermée, enregistre la
décision et débloque l'Event.

Piège rencontré en écrivant les tests (`tests/test_ui.py`) : connecter
`cleanup_review_needed` à une fonction Python ordinaire (pas une méthode
liée d'un QObject) laisse Qt dans l'incapacité de déterminer le thread du
« récepteur », et le fait basculer en connexion DIRECTE -- le gestionnaire
s'exécute alors de façon synchrone SUR CE THREAD DE TRAVAIL au lieu d'être
mis en file d'attente vers le thread principal, ce qui a fini par bloquer
tout le reste de la chaîne de signaux (`finished`, `thread.quit`). La
connexion réelle ci-dessous, vers `MainWindow._on_cleanup_review_needed`
(une vraie méthode de QObject), n'a pas ce problème -- mais un futur test
qui se connecterait à un simple callable devra passer par un vrai slot lui
aussi, pas par une fonction ou un lambda nu.

Même pont pour `on_vision_review` (bouton Traduire X, voir `core/vision_ocr.py`) :
`vision_review_needed`/`set_vision_decision`/`_vision_answered` reproduisent
exactement `cleanup_review_needed` et son `threading.Event` -- même besoin
(bloquer le thread de travail jusqu'à une décision utilisateur), même
solution, connectée vers une vraie méthode de QObject
(`MainWindow._on_vision_review_needed`) pour la même raison que ci-dessus.

Cas différent : `self.heartbeat` (voir `core/heartbeat.py`). Pas de signal
ici -- le thread principal le LIT directement (bouton Reboost, vérification
automatique après 15 minutes) pendant que ce worker continue d'écrire
dedans sur son propre thread. Aucune file d'attente Qt n'est nécessaire :
c'est un simple attribut (float + int), sûr en lecture/écriture concurrente
sous le GIL, jamais un point de synchronisation comme `_cleanup_answered`.
"""
from __future__ import annotations

import threading
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from core import heartbeat as heartbeat_mod
from core import pipeline
from core import updater


class TranslationWorker(QObject):
    status = Signal(str)                # message d'étape
    progress = Signal(object)           # core.translate.Progress
    finished = Signal(object)           # core.pipeline.Result
    failed = Signal(str)                # message d'erreur lisible
    cleanup_review_needed = Signal(object)  # core.page_cleanup.PageCleanupReport
    vision_review_needed = Signal(object)   # core.vision_ocr.VisionOcrReport
    vision_progress = Signal(int, int, object)  # page_faite, total, core.vision_ocr.VisionOcrReport

    def __init__(self, job: pipeline.Job):
        super().__init__()
        self._job = job
        self._stop_requested = threading.Event()
        self._cleanup_decision: str | None = None
        self._cleanup_answered = threading.Event()
        # Même pont threading.Event que le nettoyage des pages, pour la même
        # raison : Traduire X doit ATTENDRE une décision de l'utilisateur
        # (continuer/annuler) avant de poursuivre vers la traduction.
        self._vision_decision: str | None = None
        self._vision_answered = threading.Event()
        # Lu depuis le thread principal (bouton Reboost, vérification
        # automatique) pendant que ce worker tourne sur son propre thread --
        # voir core/heartbeat.py pour pourquoi c'est sûr sans verrou.
        self.heartbeat = heartbeat_mod.Heartbeat()

    def request_stop(self) -> None:
        """Demande l'arrêt. Le moteur s'interrompt après le segment en cours
        (on ne coupe jamais une écriture en deux)."""
        self._stop_requested.set()

    @property
    def stop_requested(self) -> bool:
        return self._stop_requested.is_set()

    def set_cleanup_decision(self, decision: str) -> None:
        """Appelée depuis le thread principal (voir MainWindow) une fois
        que l'utilisateur a répondu à la boîte de dialogue de nettoyage des
        pages -- débloque `_on_page_cleanup`, qui attend dans le thread de
        travail."""
        self._cleanup_decision = decision
        self._cleanup_answered.set()

    def _on_page_cleanup(self, report) -> str:
        """
        Appelée par `pipeline.run_job`, dans CE thread de travail. Bloque
        jusqu'à ce que l'utilisateur ait répondu dans l'interface.
        """
        self._cleanup_answered.clear()
        self.cleanup_review_needed.emit(report)
        self._cleanup_answered.wait()
        return self._cleanup_decision or "cancel"

    def set_vision_decision(self, decision: str) -> None:
        """Appelée depuis le thread principal une fois que l'utilisateur a
        répondu au récapitulatif de l'extraction vision (Traduire X) --
        débloque `_on_vision_review`, qui attend dans le thread de travail."""
        self._vision_decision = decision
        self._vision_answered.set()

    def _on_vision_review(self, report) -> str:
        """Appelée par `pipeline.run_job` (Traduire X uniquement), dans CE
        thread de travail. Bloque jusqu'à la réponse de l'utilisateur."""
        self._vision_answered.clear()
        self.vision_review_needed.emit(report)
        self._vision_answered.wait()
        return self._vision_decision or "cancel"

    def _on_vision_progress(self, done: int, total: int, report) -> None:
        """
        Une page transcrite = un signe de vie, tout autant qu'un mot généré
        par NLLB -- sans ce battement, Reboost verrait 15 minutes
        d'« inactivité » dès qu'un livre un peu long passe par l'extraction
        vision (des appels réseau page par page, pas du tout le même
        rythme que la traduction), et le signalerait comme bloqué à tort.
        """
        self.heartbeat.beat()
        self.vision_progress.emit(done, total, report)

    @Slot()
    def run(self) -> None:
        try:
            result = pipeline.run_job(
                self._job,
                on_status=self.status.emit,
                on_progress=self.progress.emit,
                should_stop=self._stop_requested.is_set,
                on_page_cleanup=self._on_page_cleanup,
                heartbeat=self.heartbeat,
                on_vision_review=self._on_vision_review,
                on_vision_progress=self._on_vision_progress,
            )
        except Exception as exc:  # noqa: BLE001 - on remonte tout à l'UI
            traceback.print_exc()
            self.failed.emit(f"{type(exc).__name__} : {exc}")
        else:
            self.finished.emit(result)


class UpdateCheckWorker(QObject):
    """
    Interroge GitHub pour la dernière version publiée (voir
    core/updater.py), dans un thread séparé -- un appel réseau ne doit
    jamais geler l'interface (demande explicite de l'utilisateur,
    27/08/2026 : « chercher une mise à jour » dans les Paramètres).
    """
    finished = Signal(object)  # core.updater.ReleaseInfo
    failed = Signal(str)

    @Slot()
    def run(self) -> None:
        try:
            info = updater.check_latest_release()
        except updater.UpdateCheckError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 - jamais une exception brute jusqu'à l'UI
            traceback.print_exc()
            self.failed.emit(f"{type(exc).__name__} : {exc}")
        else:
            self.finished.emit(info)


class UpdateDownloadWorker(QObject):
    """
    Télécharge l'installeur d'une version choisie, dans un thread séparé
    -- plusieurs centaines de Mo, une opération bien trop longue pour le
    thread principal. `request_stop()` permet d'annuler proprement (voir
    core/updater.py::download_installer, qui efface le fichier partiel).
    """
    progress = Signal(int, int)   # fait, total (total peut être 0)
    finished = Signal(object)     # Path de l'installeur téléchargé
    failed = Signal(str)

    def __init__(self, release: "updater.ReleaseInfo", dest_path: Path):
        super().__init__()
        self._release = release
        self._dest_path = dest_path
        self._stop_requested = threading.Event()

    def request_stop(self) -> None:
        self._stop_requested.set()

    @Slot()
    def run(self) -> None:
        try:
            updater.download_installer(
                self._release.download_url,
                self._dest_path,
                on_progress=self.progress.emit,
                should_stop=self._stop_requested.is_set,
            )
        except updater.UpdateCheckError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self.failed.emit(f"{type(exc).__name__} : {exc}")
        else:
            self.finished.emit(self._dest_path)
