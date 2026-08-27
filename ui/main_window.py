"""
Fenêtre principale de TRANSLAX : un seul écran, du fichier au résultat.

L'interface ne connaît qu'une fonction du moteur (`pipeline.run_job`), lancée
dans un thread par `ui.worker`. Tout ce qui suit n'est que de l'affichage et
de la gestion d'états de boutons.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPropertyAnimation,
    QSequentialAnimationGroup,
    QSize,
    Qt,
    QThread,
    QTimer,
    Slot,
)
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core import (
    cache_maintenance,
    extract,
    keep_awake,
    page_cleanup,
    pipeline,
    settings as settings_mod,
    state as state_mod,
    system_info,
    translate,
    updater,
    version,
    vision_ocr,
)
from core.languages import DEFAULT_SOURCE, DEFAULT_TARGET, LANGUAGES
from ui.titlebar import RESIZE_MARGIN, TITLE_BAR_HEIGHT, TitleBar
from ui.worker import TranslationWorker, UpdateCheckWorker, UpdateDownloadWorker

MAX_LOG_LINES = 800
PROGRESS_ANIMATION_MS = 220  # durée de la transition de la barre de progression : discrète, pas un effet voyant

# Trois écrans empilés (voir MainWindow._build_ui) -- indices dans
# self.pages, jamais des chiffres écrits en dur ailleurs dans le code.
PAGE_HUB = 0
PAGE_TRANSLATE = 1
PAGE_TOOLS = 2
PAGE_SETTINGS = 3
PAGE_FADE_MS = 160  # fondu de transition entre écrans : rapide, jamais un obstacle à l'usage

# Modèle présélectionné à l'écran (voir _build_ui) -- distinct de
# translate.DEFAULT_MODEL_KEY, voir le commentaire à son point d'usage.
UI_DEFAULT_MODEL_KEY = "600M-ct2"

# Moteurs sous licence CC-BY-NC (usage commercial interdit, voir SPEC.md
# §5 quaterdecies) -- gardés uniquement pour l'usage personnel de l'auteur.
# Utilisé pour : trier ces profils en fin de liste, les colorer en orange,
# et ouvrir un avertissement au clic (voir _build_ui / _on_model_selected).
PERSONAL_USE_ONLY_MODELS = {"600M", "1.3B", "3.3B", "600M-ct2"}
PERSONAL_USE_COLOR = "#e8912d"  # orange -- cohérent avec l'accent de styles.qss, jamais confondu avec une erreur (rouge)

# Demande explicite de l'utilisateur : 15 minutes PILE sans le moindre mot
# généré déclenche automatiquement la même vérification que le bouton
# Reboost. En dessous, seul un clic manuel donne une réponse -- rien
# n'interrompt ni ne relance la traduction, dans les deux cas.
HEARTBEAT_AUTO_THRESHOLD_S = 15 * 60
HEARTBEAT_CHECK_INTERVAL_MS = 10_000  # fréquence de la surveillance en arrière-plan


def _asset_path(filename: str) -> Path:
    """
    Chemin d'un fichier du dossier ui/, que l'app tourne depuis les sources
    ou depuis l'exécutable PyInstaller.

    En mode packagé, `Path(__file__)` peut pointer dans l'archive interne de
    l'exe plutôt que sur un vrai fichier extrait sur disque -- seul
    `sys._MEIPASS` (le dossier où PyInstaller déplie ses ressources au
    lancement) est fiable pour retrouver un fichier ajouté via `--add-data`.
    Même logique que `resource_path()` dans `main.py`, dupliquée ici plutôt
    qu'importée pour éviter un import circulaire (`main.py` importe déjà
    `ui.main_window`).
    """
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "ui" / filename
    return Path(__file__).resolve().parent / filename


def _svg_icon(filename: str) -> QIcon | None:
    """
    Charge une vraie icône SVG depuis ui/icons/ (demande explicite de
    l'utilisateur, 26/08/2026 : « pas d'emoji, de vraies icônes » --
    sinon texte seul). Renvoie None si le fichier est introuvable ou si
    Qt n'a pas pu produire la moindre image à partir (ex. plugin SVG
    absent dans l'exe gelé -- jamais garanti d'avance, contrairement aux
    formats bitmap déjà utilisés ailleurs dans ce fichier) : l'appelant
    laisse alors simplement le texte du bouton tel quel, sans icône ni
    emoji de repli -- exactement le choix demandé plutôt qu'une erreur.
    """
    path = _asset_path(f"icons/{filename}")
    if not path.exists():
        return None
    icon = QIcon(str(path))
    return None if icon.isNull() else icon


def _wrapped_label(text: str) -> QLabel:
    """
    QLabel avec retour à la ligne ET une politique de taille horizontale
    "Ignored" -- demande explicite de l'utilisateur, 26/08/2026 (page
    Paramètres responsive). Sans le `setSizePolicy` : Qt calcule le
    sizeHint d'un label à retour à la ligne sur son texte en UNE seule
    ligne, et ce sizeHint remonte jusqu'à forcer toute la page (dans une
    QScrollArea) à s'élargir en conséquence -- une carte avec un texte un
    peu long faisait passer toute la page Paramètres à plus de 3000 px de
    large, bien au-delà de la fenêtre réelle (bug réel rencontré et
    vérifié en rendant cette page responsive, pas une précaution
    théorique). `Ignored` dit à Qt d'utiliser la largeur que le
    conteneur donne, pas celle que le texte réclamerait sur une seule
    ligne, et de recalculer la hauteur en conséquence (heightForWidth).
    """
    label = QLabel(text)
    label.setWordWrap(True)
    label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    return label


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours} h {minutes:02d}"
    if minutes:
        return f"{minutes} min {secs:02d} s"
    return f"{secs} s"


def format_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("o", "Ko", "Mo", "Go"):
        if size < 1024 or unit == "Go":
            return f"{size:.0f} {unit}" if unit == "o" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} Go"


def open_in_explorer(path: Path) -> None:
    """Ouvre un fichier ou un dossier avec l'application par défaut."""
    path = Path(path)
    if sys.platform == "win32":
        os.startfile(str(path))  # noqa: S606 - chemin choisi par l'utilisateur
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


def reveal_in_explorer(path: Path) -> None:
    """
    Ouvre l'explorateur de fichiers sur le DOSSIER du fichier, avec le
    fichier lui-même sélectionné et surligné — pas juste « un dossier qui
    contient plein de choses », mais « voici où est votre traduction ».
    """
    path = Path(path)
    if sys.platform == "win32":
        # Popen avec une chaîne unique (pas de shell=True) : la ligne de
        # commande part telle quelle vers CreateProcess, exactement comme
        # taper la commande dans Exécuter. `explorer` retourne parfois un
        # code de sortie non nul même quand tout s'est bien passé -- on ne
        # le vérifie donc pas.
        subprocess.Popen(f'explorer /select,"{path}"')
    elif sys.platform == "darwin":
        subprocess.run(["open", "-R", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path.parent)], check=False)


class NoScrollComboBox(QComboBox):
    """
    QComboBox qui ignore la molette de la souris (demande explicite de
    l'utilisateur, 26/08/2026) : Qt change normalement la valeur
    sélectionnée au survol + scroll -- des changements accidentels rien
    qu'en faisant défiler la page autour du sélecteur. Le seul moyen de
    changer la valeur reste : cliquer pour ouvrir la liste, puis
    sélectionner dedans -- jamais la molette. Utilisée pour TOUS les
    sélecteurs de l'appli (langue, modèle, format de sortie...), pas
    seulement un cas particulier.

    `event.ignore()` (pas juste ne rien faire) : laisse l'évènement
    remonter au parent -- la molette continue de faire défiler la page
    autour du sélecteur (utile sur la page Paramètres, par exemple),
    plutôt que d'être purement et simplement avalée.
    """

    def wheelEvent(self, event) -> None:  # noqa: N802 - nom imposé par Qt
        event.ignore()


class Card(QFrame):
    """Bloc visuel avec un titre, pour découper l'écran en sections."""

    def __init__(self, title: str):
        super().__init__()
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 14, 20, 16)
        layout.setSpacing(10)
        heading = QLabel(title)
        heading.setObjectName("cardTitle")
        layout.addWidget(heading)
        self.body = QVBoxLayout()
        self.body.setSpacing(8)
        layout.addLayout(self.body)


class ApiKeysDialog(QDialog):
    """
    Modal de saisie des clés API (demande explicite de l'utilisateur,
    25/08/2026) : le champ « Clé API Anthropic » de la page principale
    n'est plus directement modifiable, un clic dessus ouvre cette fenêtre
    à la place. Anthropic est la seule utilisée aujourd'hui (Traduire X) ;
    xAI (Grok) et OpenAI (ChatGPT) sont préparées pour de futures
    intégrations -- champs présents, stockés (voir core/settings.py), mais
    encore branchés à aucune fonctionnalité.
    """

    def __init__(self, anthropic_key: str, xai_key: str, openai_key: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Clés API")
        self.resize(440, 260)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        intro = QLabel(
            "Stockées uniquement sur cette machine, jamais transmises par TRANSLAX "
            "lui-même -- chaque fournisseur facture directement l'usage de sa propre clé."
        )
        intro.setWordWrap(True)
        intro.setObjectName("outputPreview")
        layout.addWidget(intro)

        self.anthropic_edit = self._key_row(layout, "Anthropic (Claude)", anthropic_key, "sk-ant-…")
        self.xai_edit = self._key_row(layout, "xAI (Grok)", xai_key, "xai-… (pas encore utilisé)")
        self.openai_edit = self._key_row(layout, "OpenAI (ChatGPT)", openai_key, "sk-… (pas encore utilisé)")

        layout.addStretch(1)

        button_row = QHBoxLayout()
        button_row.addStretch()
        cancel_btn = QPushButton("Annuler")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Enregistrer")
        save_btn.setObjectName("primary")
        save_btn.clicked.connect(self.accept)
        button_row.addWidget(cancel_btn)
        button_row.addWidget(save_btn)
        layout.addLayout(button_row)

    @staticmethod
    def _key_row(layout: QVBoxLayout, label_text: str, value: str | None, placeholder: str) -> QLineEdit:
        row = QHBoxLayout()
        row.setSpacing(10)
        label = QLabel(label_text)
        label.setFixedWidth(130)
        edit = QLineEdit()
        edit.setEchoMode(QLineEdit.Password)
        edit.setPlaceholderText(placeholder)
        if value:
            edit.setText(value)
        row.addWidget(label)
        row.addWidget(edit, 1)
        layout.addLayout(row)
        return edit


class ResumeJobsDialog(QDialog):
    """
    Liste TOUTES les traductions interrompues encore en attente (demande
    explicite de l'utilisateur, 26/08/2026 : plus seulement la dernière) --
    proposée à chaque démarrage tant qu'il en reste au moins une (voir
    `MainWindow._offer_resume_pending_jobs`), jusqu'à ce que chacune soit
    reprise jusqu'au bout ou abandonnée explicitement.

    « Abandonner » agit immédiatement (pas seulement à la fermeture du
    dialogue) : ces jobs ne sont pas en cours d'exécution ici (ce dialogue
    ne s'affiche qu'au démarrage, avant qu'aucun thread de traduction ne
    tourne), donc effacer leur état de reprise tout de suite est sans
    risque -- contrairement au bouton Stop rouge de la page Traduire, qui
    doit attendre que le thread en cours s'arrête réellement avant de
    faire la même chose (voir `MainWindow._on_finished`).
    """

    def __init__(self, resumable: list[tuple[dict, object, str]], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Traductions interrompues")
        self.resize(640, 440)
        # None tant qu'aucun choix n'est fait ; sinon
        # (kind, snapshot, job_state, original_label) -- kind vaut
        # "resume" ou "resume_other_engine".
        self.result_action: tuple[str, dict, object, str] | None = None
        self._entries = list(resumable)

        outer = QVBoxLayout(self)
        outer.setSpacing(10)
        self._intro = QLabel()
        self._intro.setWordWrap(True)
        outer.addWidget(self._intro)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._content = QWidget()
        self._list_layout = QVBoxLayout(self._content)
        self._list_layout.setSpacing(10)
        self._scroll.setWidget(self._content)
        outer.addWidget(self._scroll, 1)

        close_row = QHBoxLayout()
        close_row.addStretch()
        later_btn = QPushButton("Plus tard")
        later_btn.setToolTip("Ne rien faire pour l'instant -- reproposé au prochain démarrage.")
        later_btn.clicked.connect(self.reject)
        close_row.addWidget(later_btn)
        outer.addLayout(close_row)

        self._rebuild_rows()

    def _rebuild_rows(self) -> None:
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        if not self._entries:
            # Toutes traitées (abandonnées) pendant que ce dialogue était
            # ouvert -- plus rien à montrer.
            self.reject()
            return

        plural = "s" if len(self._entries) > 1 else ""
        self._intro.setText(
            f"{len(self._entries)} traduction{plural} interrompue{plural} en attente. "
            "Choisissez laquelle continuer, ou abandonnez celles que vous ne voulez plus reprendre."
        )
        for snapshot, job_state, original_label in self._entries:
            self._list_layout.addWidget(self._job_row(snapshot, job_state, original_label))
        self._list_layout.addStretch(1)

    def _job_row(self, snapshot: dict, job_state, original_label: str) -> QFrame:
        card = Card(Path(snapshot["input_path"]).name)
        card.body.addWidget(QLabel(
            f"{job_state.done}/{job_state.total} segments  ·  moteur d'origine : {original_label}"
        ))
        row = QHBoxLayout()
        row.setSpacing(8)
        resume_btn = QPushButton("Reprendre")
        resume_btn.setObjectName("primary")
        resume_btn.clicked.connect(lambda: self._choose("resume", snapshot, job_state, original_label))
        other_btn = QPushButton("Autre moteur…")
        other_btn.clicked.connect(
            lambda: self._choose("resume_other_engine", snapshot, job_state, original_label)
        )
        abandon_btn = QPushButton("Abandonner")
        abandon_btn.setObjectName("danger")
        abandon_btn.clicked.connect(lambda: self._abandon(snapshot))
        row.addWidget(resume_btn)
        row.addWidget(other_btn)
        row.addStretch(1)
        row.addWidget(abandon_btn)
        card.body.addLayout(row)
        return card

    def _choose(self, kind: str, snapshot: dict, job_state, original_label: str) -> None:
        self.result_action = (kind, snapshot, job_state, original_label)
        self.accept()

    def _abandon(self, snapshot: dict) -> None:
        state_mod.abandon(Path(snapshot["output_path"]), Path(snapshot["input_path"]))
        settings_mod.remove_pending_job(snapshot["output_path"])
        self._entries = [e for e in self._entries if e[0] is not snapshot]
        self._rebuild_rows()


class VisionReviewDialog(QDialog):
    """
    Récapitulatif de l'extraction vision (Traduire X), montré avant que la
    traduction du corps du livre ne commence -- demande explicite de
    l'utilisateur : tout voir d'un coup, dans une seule fenêtre scrollable,
    pour vérifier que la correction a été bien faite avant de continuer.

    Ne montre QUE les pages réellement corrigées ou signalées comme
    incertaines par le modèle (`PageResult.changed`/`.flagged`, voir
    `core/vision_ocr.py`) -- une page identique avant/après n'apporte rien
    à vérifier, et noierait les vraies corrections dans un livre de
    plusieurs centaines de pages. Le nombre de pages non montrées est
    affiché, jamais caché.
    """

    def __init__(self, report: vision_ocr.VisionOcrReport, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Vérification de l'extraction vision")
        self.resize(780, 640)

        outer = QVBoxLayout(self)
        outer.setSpacing(10)

        flagged_note = f", {report.flagged_count} signalée(s) comme incertaine(s)" if report.flagged_count else ""
        summary = QLabel(
            f"{report.changed_count} page(s) corrigée(s) sur {report.total_pages}{flagged_note} "
            f"— coût estimé ~{report.estimated_cost_usd():.2f} $."
        )
        summary.setWordWrap(True)
        outer.addWidget(summary)

        if report.failed_count:
            # Filtre de contenu Anthropic sur une ou plusieurs pages
            # précises (voir core/vision_ocr.py) -- pas une panne : le texte
            # original de ces pages est conservé, rien n'est perdu, mais ça
            # mérite d'être visible d'emblée, pas juste dans la liste plus bas.
            warning = QLabel(
                f"⚠ {report.failed_count} page(s) bloquée(s) par le filtre de contenu d'Anthropic -- "
                "le texte original (non corrigé par la vision) a été conservé pour ces pages-là, "
                "identifiées ci-dessous."
            )
            warning.setObjectName("keepAwake")
            warning.setWordWrap(True)
            outer.addWidget(warning)

        shown_pages = [p for p in report.pages if p.changed or p.flagged]
        hidden_count = report.total_pages - len(shown_pages)
        if hidden_count:
            note = QLabel(
                f"{hidden_count} page(s) identique(s) avant/après, non affichée(s) ci-dessous "
                "(rien à corriger dessus)."
            )
            note.setObjectName("outputPreview")
            note.setWordWrap(True)
            outer.addWidget(note)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setSpacing(14)

        if not shown_pages:
            empty = QLabel("Aucune correction à revoir : le texte existant était déjà propre.")
            empty.setWordWrap(True)
            content_layout.addWidget(empty)
        for page_result in shown_pages:
            content_layout.addWidget(self._page_card(page_result))
        content_layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        actions = QHBoxLayout()
        actions.addStretch()
        cancel_btn = QPushButton("Annuler")
        cancel_btn.setObjectName("danger")
        cancel_btn.clicked.connect(self.reject)
        continue_btn = QPushButton("Continuer la traduction")
        continue_btn.setObjectName("primary")
        continue_btn.clicked.connect(self.accept)
        actions.addWidget(cancel_btn)
        actions.addWidget(continue_btn)
        outer.addLayout(actions)

    @staticmethod
    def _page_card(page_result: vision_ocr.PageResult) -> QFrame:
        if page_result.vision_failed:
            flag_text = "   🚫 filtre de contenu — texte original conservé"
        elif page_result.flagged:
            flag_text = "   ⚠ passage incertain"
        else:
            flag_text = ""
        card = Card(
            f"Page {page_result.page_index + 1}"
            + (f" (imprimée : {page_result.printed_page_number})" if page_result.printed_page_number else "")
            + flag_text
        )

        before_label = QLabel("Avant (calque de texte existant)")
        before_label.setObjectName("outputPreview")
        card.body.addWidget(before_label)
        before_text = QPlainTextEdit(page_result.original_text)
        before_text.setReadOnly(True)
        before_text.setMaximumHeight(110)
        card.body.addWidget(before_text)

        after_label = QLabel("Après (vision IA)")
        after_label.setObjectName("outputPreview")
        card.body.addWidget(after_label)
        after_text = QPlainTextEdit(page_result.corrected_text)
        after_text.setReadOnly(True)
        after_text.setMaximumHeight(110)
        card.body.addWidget(after_text)

        return card


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        # Drapeau posé avant tout le reste : changer les drapeaux de fenêtre
        # après un premier affichage force Qt à recréer la fenêtre native
        # (flash, voire perte de l'état) -- ici il n'y a encore jamais eu de
        # show(), donc aucun risque.
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setWindowTitle("TRANSLAX")
        self.setMinimumSize(620, 480)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)  # nécessaire pour détecter le survol des bords (redimensionnement)

        self.source_path: Path | None = None
        # True pendant un changement PROGRAMMATIQUE du sélecteur de modèle
        # (restauration d'un job mémorisé, etc.) -- évite d'ouvrir
        # l'avertissement "usage personnel" (voir _on_model_selected) pour
        # un changement que l'utilisateur n'a pas lui-même cliqué.
        self._suppress_model_notice = False
        # Mémorisé d'un lancement à l'autre (voir core/settings.py) --
        # None si jamais réglé, ou si le dossier choisi a disparu depuis.
        self.output_dir: Path | None = settings_mod.get_default_output_dir()
        self.result_path: Path | None = None
        # Résultat de la dernière analyse de cache OCR/reprise (page
        # Paramètres, voir _scan_cache_dirs/_clear_found_caches) -- None
        # tant qu'aucune analyse n'a été lancée.
        self._cache_scan_result: cache_maintenance.CacheScanResult | None = None
        # Distingue Pause (reprenable) de Stop (abandon définitif) alors que
        # les deux passent par le même `worker.request_stop()` sous-jacent
        # (voir _pause/_stop/_on_finished, demande explicite de
        # l'utilisateur, 26/08/2026) -- remis à False dès que traité.
        self._abandon_requested = False
        # Clé (output_path str) utilisée pour enregistrer le job en cours
        # dans settings.pending_jobs -- mémorisée séparément de
        # `result.output_path` (voir _on_finished) car un titre traduit
        # peut renommer le fichier de sortie réel en cours de route
        # (voir core/state.py::resolve_output_path) : sans ça, retirer le
        # job de la liste d'attente à la fin chercherait la mauvaise clé.
        self._current_job_output_key: str | None = None
        self.thread: QThread | None = None
        self.worker: TranslationWorker | None = None
        # Mise à jour (page Paramètres, voir _check_for_update) -- dernière
        # version trouvée sur GitHub, mémorisée entre la vérification et le
        # clic sur "Mettre à jour" ; threads dédiés, distincts de
        # `self.thread`/`self.worker` (une vérification/téléchargement de
        # mise à jour n'a rien à voir avec une traduction en cours).
        self._latest_release: updater.ReleaseInfo | None = None
        self._update_check_thread: QThread | None = None
        self._update_check_worker: UpdateCheckWorker | None = None
        self._update_download_thread: QThread | None = None
        self._update_download_worker: UpdateDownloadWorker | None = None
        self._keep_awake = keep_awake.KeepAwake()
        self._auto_reboost_done = False

        # Surveille le pouls du worker (voir core/heartbeat.py) pendant
        # qu'une traduction tourne -- déclenche automatiquement la même
        # vérification que le bouton Reboost après 15 minutes pile sans
        # activité. Ne fait jamais rien d'autre : ni pause ni relance.
        self._heartbeat_timer = QTimer(self)
        self._heartbeat_timer.setInterval(HEARTBEAT_CHECK_INTERVAL_MS)
        self._heartbeat_timer.timeout.connect(self._check_heartbeat)

        self._build_ui()
        if self.output_dir is not None:
            self.output_edit.setText(str(self.output_dir))
        self._update_output_preview()
        self._set_running(False)
        self._size_to_screen()

        # Différé à après l'affichage de la fenêtre (délai de 0 = dès que la
        # boucle d'évènements tourne) : une boîte de dialogue modale avant
        # le premier show() apparaîtrait sans fenêtre visible derrière elle.
        QTimer.singleShot(0, self._offer_resume_pending_jobs)

    def _size_to_screen(self) -> None:
        """
        Ouvre à peu près à la taille du contenu, sans jamais dépasser
        l'écran. Le contenu est dans une zone défilante (voir `_build_ui`) :
        si l'écran est plus petit que ce que demande le contenu, une barre
        de défilement apparaît au lieu que les rangées se chevauchent —
        important sur un petit écran, et ça absorbe aussi tout ajout futur
        de réglages sans avoir à retoucher cette taille à chaque fois.
        """
        ideal = self._content.sizeHint()
        chrome_h = TITLE_BAR_HEIGHT + 2 * RESIZE_MARGIN
        chrome_w = 2 * RESIZE_MARGIN
        screen = self.screen()
        if screen is None:
            self.resize(max(860, ideal.width() + chrome_w + 20), 900)
            return
        available = screen.availableGeometry()
        width = min(ideal.width() + chrome_w + 24, available.width() - 60)
        height = min(ideal.height() + chrome_h + 24, available.height() - 60)
        self.resize(max(660, width), max(560, height))

    # ------------------------------------------------------------------ UI
    def _navigate_to(self, index: int) -> None:
        """
        Change d'écran avec un fondu simple (demande explicite de
        l'utilisateur) : fondu au noir sur l'écran courant, changement de
        page pendant que l'opacité est à zéro (donc invisible), puis fondu
        d'apparition sur le nouvel écran. Un seul effet réutilisé (pas un
        par écran) : posé sur `self.pages` lui-même, qui affiche toujours
        exactement l'écran courant.
        """
        if index == self.pages.currentIndex():
            return
        effect = QGraphicsOpacityEffect(self.pages)
        self.pages.setGraphicsEffect(effect)

        fade_out = QPropertyAnimation(effect, b"opacity", self)
        fade_out.setDuration(PAGE_FADE_MS)
        fade_out.setStartValue(1.0)
        fade_out.setEndValue(0.0)
        fade_in = QPropertyAnimation(effect, b"opacity", self)
        fade_in.setDuration(PAGE_FADE_MS)
        fade_in.setStartValue(0.0)
        fade_in.setEndValue(1.0)

        group = QSequentialAnimationGroup(self)
        group.addAnimation(fade_out)
        group.addAnimation(fade_in)
        fade_out.finished.connect(lambda: self.pages.setCurrentIndex(index))
        # Garde une référence tant que l'animation tourne (sinon le garbage
        # collector Python peut la libérer avant la fin -- PySide6 ne
        # possède pas cet objet lui-même) ; relâchée une fois terminée.
        self._page_animation = group
        group.finished.connect(lambda: setattr(self, "_page_animation", None))
        group.start()

    def _build_hub_page(self) -> None:
        """
        Écran d'accueil (demande explicite de l'utilisateur, 25/08/2026) :
        choisir le service avant même de voir le reste de l'interface, au
        lieu de tout afficher d'un coup sur un seul écran. Trois services,
        trois boutons -- rien d'autre ici, volontairement.
        """
        hub = QWidget()
        layout = QVBoxLayout(hub)
        layout.setContentsMargins(40, 60, 40, 60)
        layout.setSpacing(28)
        layout.addStretch(1)

        title = QLabel("TRANSLAX")
        title.setObjectName("appTitle")
        title.setAlignment(Qt.AlignCenter)
        subtitle = QLabel("Que voulez-vous faire ?")
        subtitle.setObjectName("appSubtitle")
        subtitle.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        def hub_button(text: str, description: str, slot) -> QPushButton:
            btn = QPushButton(text)
            btn.setObjectName("hubButton")
            btn.setMinimumHeight(64)
            btn.setToolTip(description)
            btn.clicked.connect(slot)
            return btn

        translate_btn = hub_button(
            "TRADUIRE DU VOLUME",
            "Traduire un document entier vers une autre langue.",
            self._go_to_translate,
        )
        extract_btn = hub_button(
            "EXTRAIRE AVEC ANALYSE",
            "Extraire et nettoyer un texte, sans le traduire.",
            self._go_to_extract,
        )
        undo_btn = hub_button(
            "ANNULER NETTOYAGE",
            "Annuler le nettoyage des titres et traits d'union sur un fichier déjà traduit.",
            self._go_to_tools,
        )
        layout.addWidget(translate_btn)
        layout.addWidget(extract_btn)
        layout.addWidget(undo_btn)

        # Lien secondaire, pas un quatrième "service" au même niveau que les
        # trois boutons ci-dessus (demande explicite de l'utilisateur,
        # 25/08/2026) : les Paramètres ne sont pas une action à lancer,
        # juste un endroit à consulter/régler.
        self.settings_link_button = QPushButton("⚙  Paramètres")
        self.settings_link_button.setObjectName("backButton")
        self.settings_link_button.clicked.connect(self._go_to_settings)
        settings_row = QHBoxLayout()
        settings_row.addStretch()
        settings_row.addWidget(self.settings_link_button)
        settings_row.addStretch()
        layout.addSpacing(8)
        layout.addLayout(settings_row)

        layout.addStretch(2)
        # Pas de numéro de version ici : celui déjà affiché sous `self.pages`
        # (voir la fin de _build_ui) reste visible sur les trois écrans,
        # inutile de le dupliquer.

        self.pages.addWidget(hub)  # index PAGE_HUB

    def _go_to_translate(self) -> None:
        self.extract_only_check.setChecked(False)
        self._navigate_to(PAGE_TRANSLATE)

    def _go_to_extract(self) -> None:
        self.extract_only_check.setChecked(True)
        self._navigate_to(PAGE_TRANSLATE)

    def _go_to_tools(self) -> None:
        self._navigate_to(PAGE_TOOLS)

    def _go_to_settings(self) -> None:
        # La détection matérielle ne se lance PLUS automatiquement en
        # arrivant sur cette page (demande explicite de l'utilisateur,
        # 26/08/2026) -- seulement au clic sur le bouton dédié (voir
        # _refresh_system_info) : ouvrir Paramètres ne doit rien déclencher
        # tout seul.
        self._navigate_to(PAGE_SETTINGS)

    def _refresh_system_info(self) -> None:
        """
        Relance la détection matérielle (voir core/system_info.py) et met à
        jour l'affichage -- jamais mise en cache, jamais automatique :
        UNIQUEMENT au clic du bouton dédié de cette carte (demande
        explicite de l'utilisateur, 26/08/2026 -- ouvrir la page Paramètres
        ne doit rien lancer tout seul).
        """
        info = system_info.detect()
        gpu_line = (
            f"GPU détecté : {info.gpu_name} (utilisable par PyTorch/CUDA)" if info.gpu_available
            else "GPU détecté : aucun utilisable par PyTorch/CUDA"
        )
        device_line = (
            "→ Précis, OPUS-MT et MADLAD-400 utiliseront ce GPU automatiquement."
            if info.gpu_available else
            "→ Précis, OPUS-MT et MADLAD-400 tourneront sur le CPU (aucun GPU CUDA détecté)."
        )
        turbo_note = "→ Turbo (CTranslate2) tourne TOUJOURS sur le CPU, même si un GPU est détecté ci-dessus."
        lines = [
            f"Système :  {info.os_name}",
            f"Processeur :  {info.cpu_name}  ({info.cpu_cores} cœurs logiques)",
            "",
            gpu_line,
            device_line,
            turbo_note,
        ]
        if not info.torch_available:
            lines.append("")
            lines.append("⚠ PyTorch n'est pas installé -- Précis/OPUS-MT/MADLAD-400 ne peuvent pas se charger du tout.")
        for note in info.detection_notes:
            lines.append("")
            lines.append(f"ℹ {note}")
        self.system_info_label.setText("\n".join(lines))

    def _scan_cache_dirs(self, root: Path | None) -> None:
        """
        Analyse `root` (ou le dossier de sortie par défaut si `root` est
        None) à la recherche de dossiers de cache `.translax` (voir
        core/cache_maintenance.py) -- jamais de suppression ici, juste un
        état des lieux avant de proposer de vider quoi que ce soit.
        """
        target = root or settings_mod.get_default_output_dir()
        if target is None:
            QMessageBox.information(
                self,
                "Aucun dossier de sortie par défaut",
                "Choisis d'abord un dossier de sortie par défaut (page Traduire), "
                "ou utilise « Choisir un autre dossier… » ci-dessous.",
            )
            return
        result = cache_maintenance.find_cache_dirs(target)
        self._cache_scan_result = result
        if result.count == 0:
            self.cache_status_label.setText(f"Aucun cache trouvé dans {target}.")
        else:
            size = cache_maintenance.format_size(result.total_bytes)
            self.cache_status_label.setText(
                f"{result.count} dossier(s) de cache trouvé(s) dans {target} ({size} au total)."
            )
        self.clear_cache_button.setEnabled(result.count > 0)

    def _pick_cache_scan_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Dossier à analyser")
        if folder:
            self._scan_cache_dirs(Path(folder))

    def _clear_found_caches(self) -> None:
        """
        Supprime les dossiers `.translax` trouvés par la dernière analyse
        -- ne touche jamais aux fichiers de sortie déjà traduits, seulement
        à l'état de reprise / cache OCR (voir core/cache_maintenance.py).
        """
        result = self._cache_scan_result
        if not result or not result.dirs:
            return
        box = QMessageBox(self)
        box.setWindowTitle("Vider les caches")
        box.setIcon(QMessageBox.Warning)
        box.setText(
            f"Supprimer {result.count} dossier(s) de cache "
            f"({cache_maintenance.format_size(result.total_bytes)}) ?\n\n"
            "Les traductions déjà terminées ne sont pas affectées -- seule la reprise "
            "automatique d'un job interrompu dans ces dossiers-là serait perdue."
        )
        box.addButton("Annuler", QMessageBox.RejectRole)
        confirm_btn = box.addButton("Supprimer", QMessageBox.AcceptRole)
        box.exec()
        if box.clickedButton() is not confirm_btn:
            return
        removed, errors = cache_maintenance.clear_cache_dirs(result.dirs)
        message = f"{removed} dossier(s) supprimé(s)."
        if errors:
            message += f"\n{len(errors)} erreur(s) rencontrée(s) (fichier en cours d'utilisation ?)."
        self.cache_status_label.setText(message)
        self.clear_cache_button.setEnabled(False)
        self._cache_scan_result = None

    def _check_for_update(self) -> None:
        """
        Interroge GitHub (voir core/updater.py), dans un thread séparé --
        demande explicite de l'utilisateur, 27/08/2026. Jamais automatique :
        uniquement au clic sur ce bouton précis, jamais au démarrage.
        """
        self.check_update_button.setEnabled(False)
        self.install_update_button.setVisible(False)
        self.update_status_label.setText("Vérification en cours…")

        self._update_check_thread = QThread(self)
        self._update_check_worker = UpdateCheckWorker()
        self._update_check_worker.moveToThread(self._update_check_thread)
        self._update_check_thread.started.connect(self._update_check_worker.run)
        self._update_check_worker.finished.connect(self._on_update_check_finished)
        self._update_check_worker.failed.connect(self._on_update_check_failed)
        self._update_check_worker.finished.connect(self._update_check_thread.quit)
        self._update_check_worker.failed.connect(self._update_check_thread.quit)
        self._update_check_thread.finished.connect(self._on_update_check_thread_finished)
        self._update_check_thread.start()

    @Slot(object)
    def _on_update_check_finished(self, info) -> None:
        self.check_update_button.setEnabled(True)
        self._latest_release = info
        if updater.is_newer(info.version, version.VERSION):
            notes = f"\n\n{info.notes}" if info.notes else ""
            self.update_status_label.setText(
                f"Nouvelle version disponible : {info.version} (actuelle : {version.VERSION}).{notes}"
            )
            self.install_update_button.setVisible(True)
        else:
            self.update_status_label.setText(f"Vous avez déjà la dernière version ({version.VERSION}).")
            self.install_update_button.setVisible(False)

    @Slot(str)
    def _on_update_check_failed(self, message: str) -> None:
        self.check_update_button.setEnabled(True)
        self.install_update_button.setVisible(False)
        self.update_status_label.setText(f"Impossible de vérifier : {message}")

    @Slot()
    def _on_update_check_thread_finished(self) -> None:
        if self._update_check_thread is not None:
            self._update_check_thread.deleteLater()
        self._update_check_thread = None
        self._update_check_worker = None

    def _install_update(self) -> None:
        """
        Demande confirmation puis télécharge l'installeur de la version
        trouvée -- demande explicite de l'utilisateur : « comme sur VS
        Code, on clique et tout le reste se lance » -- le téléchargement,
        le lancement de l'installeur et la fermeture de TRANSLAX
        s'enchaînent ensuite sans autre confirmation (voir
        _on_update_downloaded).
        """
        if self._latest_release is None:
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("Mettre à jour TRANSLAX")
        box.setText(f"Télécharger et installer la version {self._latest_release.version} ?")
        box.setInformativeText(
            "TRANSLAX se fermera automatiquement une fois le téléchargement terminé pour "
            "achever l'installation, puis se relancera tout seul."
        )
        box.addButton("Annuler", QMessageBox.RejectRole)
        confirm_btn = box.addButton("Mettre à jour", QMessageBox.AcceptRole)
        box.exec()
        if box.clickedButton() is not confirm_btn:
            return

        self.check_update_button.setEnabled(False)
        self.install_update_button.setEnabled(False)
        self.update_progress_bar.setVisible(True)
        self.update_progress_bar.setRange(0, 0)  # indéterminé tant que la taille totale n'est pas connue
        self.update_status_label.setText("Téléchargement…")

        dest = Path(tempfile.gettempdir()) / f"TRANSLAX-Setup-{self._latest_release.version}.exe"
        self._update_download_thread = QThread(self)
        self._update_download_worker = UpdateDownloadWorker(self._latest_release, dest)
        self._update_download_worker.moveToThread(self._update_download_thread)
        self._update_download_thread.started.connect(self._update_download_worker.run)
        self._update_download_worker.progress.connect(self._on_update_progress)
        self._update_download_worker.finished.connect(self._on_update_downloaded)
        self._update_download_worker.failed.connect(self._on_update_download_failed)
        self._update_download_worker.finished.connect(self._update_download_thread.quit)
        self._update_download_worker.failed.connect(self._update_download_thread.quit)
        self._update_download_thread.finished.connect(self._on_update_download_thread_finished)
        self._update_download_thread.start()

    @Slot(int, int)
    def _on_update_progress(self, done: int, total: int) -> None:
        if total > 0:
            self.update_progress_bar.setRange(0, total)
            self.update_progress_bar.setValue(done)
            self.update_status_label.setText(f"Téléchargement… {format_size(done)} / {format_size(total)}")
        else:
            self.update_status_label.setText(f"Téléchargement… {format_size(done)}")

    @Slot(object)
    def _on_update_downloaded(self, installer_path: Path) -> None:
        """
        Le téléchargement est terminé : lance l'installeur (silencieux, se
        relance tout seul -- voir installer/translax.iss) puis ferme
        TRANSLAX. Le petit délai avant `quit()` laisse le message
        s'afficher à l'écran -- sans lui, la fenêtre disparaîtrait avant
        que qui que ce soit n'ait le temps de le lire.
        """
        self.update_status_label.setText(
            "Téléchargement terminé -- installation en cours, TRANSLAX va se fermer puis se relancer…"
        )
        updater.launch_installer_and_quit(installer_path)
        QTimer.singleShot(1200, QApplication.instance().quit)

    @Slot(str)
    def _on_update_download_failed(self, message: str) -> None:
        self.update_progress_bar.setVisible(False)
        self.check_update_button.setEnabled(True)
        self.install_update_button.setEnabled(True)
        self.update_status_label.setText(f"Échec du téléchargement : {message}")

    @Slot()
    def _on_update_download_thread_finished(self) -> None:
        if self._update_download_thread is not None:
            self._update_download_thread.deleteLater()
        self._update_download_thread = None
        self._update_download_worker = None

    def _build_settings_page(self) -> None:
        """
        Écran Paramètres (demande explicite de l'utilisateur, 25/08/2026) :
        pensé pour accueillir plusieurs réglages au fil du temps -- pour
        l'instant, un seul : le diagnostic matériel (CPU/GPU réellement
        détectés et réellement utilisés par TRANSLAX), pour savoir si la
        puissance locale est vraiment exploitée plutôt que supposée.
        """
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        back_row = QHBoxLayout()
        back_btn = QPushButton("← Menu")
        back_btn.setObjectName("backButton")
        back_btn.clicked.connect(lambda: self._navigate_to(PAGE_HUB))
        back_row.addWidget(back_btn)
        back_row.addStretch()
        root.addLayout(back_row)

        title = QLabel("Paramètres")
        title.setObjectName("appTitle")
        subtitle = _wrapped_label("Réglages et diagnostics de TRANSLAX sur cette machine.")
        subtitle.setObjectName("appSubtitle")
        root.addWidget(title)
        root.addWidget(subtitle)

        hw_card = Card("Matériel réellement détecté et utilisé")
        hw_card.body.addWidget(_wrapped_label(
            "Ce que TRANSLAX détecte sur cette machine, et ce qu'il utiliserait vraiment "
            "pour traduire -- un indicateur réel, pas une estimation."
        ))
        self.system_info_label = _wrapped_label(
            "Détection non lancée -- cliquez sur « Analyser » ci-dessous."
        )
        self.system_info_label.setObjectName("outputPreview")
        hw_card.body.addWidget(self.system_info_label)

        refresh_row = QHBoxLayout()
        refresh_row.addStretch()
        self.refresh_system_info_button = QPushButton("Analyser")
        self.refresh_system_info_button.clicked.connect(self._refresh_system_info)
        refresh_row.addWidget(self.refresh_system_info_button)
        hw_card.body.addLayout(refresh_row)

        root.addWidget(hw_card)

        # Mises à jour (demande explicite de l'utilisateur, 27/08/2026 :
        # « comme sur VS Code, avec un bouton update où on clique et tout
        # le reste se lance ») -- voir core/updater.py (Releases GitHub du
        # dépôt public) et _check_for_update/_install_update plus bas.
        # Jamais de vérification automatique au démarrage : uniquement au
        # clic explicite sur « Chercher une mise à jour ».
        update_card = Card("Mises à jour")
        update_card.body.addWidget(_wrapped_label(
            f"Version installée : {version.VERSION}. Vérifie sur GitHub si une version plus "
            "récente est disponible."
        ))
        self.update_status_label = _wrapped_label("Vérification non lancée.")
        self.update_status_label.setObjectName("outputPreview")
        update_card.body.addWidget(self.update_status_label)

        self.update_progress_bar = QProgressBar()
        self.update_progress_bar.setRange(0, 100)
        self.update_progress_bar.setValue(0)
        self.update_progress_bar.setVisible(False)
        update_card.body.addWidget(self.update_progress_bar)

        update_buttons = QVBoxLayout()
        update_buttons.setSpacing(8)
        self.check_update_button = QPushButton("Chercher une mise à jour")
        self.check_update_button.clicked.connect(self._check_for_update)
        self.install_update_button = QPushButton("Mettre à jour")
        self.install_update_button.setObjectName("primary")
        self.install_update_button.setVisible(False)
        self.install_update_button.clicked.connect(self._install_update)
        update_buttons.addWidget(self.check_update_button)
        update_buttons.addWidget(self.install_update_button)
        update_card.body.addLayout(update_buttons)

        root.addWidget(update_card)

        about_card = Card("À propos de TRANSLAX")
        about_card.body.addWidget(_wrapped_label(
            "Ce que fait TRANSLAX, et pourquoi -- en bref."
        ))
        about_text = _wrapped_label(
            f"{version.version_string()}\n"
            "Éditeur :  AJTWS — Amilcar Joao\n\n"
            "TRANSLAX traduit des documents volumineux (livres, PDF scannés...) "
            "directement sur cette machine, sans dépendre d'un service de traduction "
            "payant : plusieurs moteurs (NLLB, CTranslate2 « Turbo », OPUS-MT, "
            "MADLAD-400) et un OCR local (PaddleOCR) pour le texte scanné, choisis "
            "selon l'usage visé -- commercial ou strictement personnel."
        )
        about_text.setObjectName("outputPreview")
        self.about_text_label = about_text
        about_card.body.addWidget(about_text)
        root.addWidget(about_card)

        api_card = Card("Clés API")
        api_card.body.addWidget(_wrapped_label(
            "Anthropic (Claude), xAI (Grok) et OpenAI (ChatGPT) -- stockées uniquement "
            "sur cette machine, jamais transmises par TRANSLAX lui-même."
        ))
        api_row = QHBoxLayout()
        api_row.addStretch()
        self.api_keys_settings_button = QPushButton("Gérer les clés API…")
        self.api_keys_settings_button.clicked.connect(self._open_api_keys_dialog)
        api_row.addWidget(self.api_keys_settings_button)
        api_card.body.addLayout(api_row)
        root.addWidget(api_card)

        cache_card = Card("Fichiers temporaires et cache OCR")
        cache_card.body.addWidget(_wrapped_label(
            "TRANSLAX crée un petit dossier caché « .translax » à côté de chaque "
            "fichier de sortie, pour reprendre une traduction interrompue sans tout "
            "refaire -- y compris le texte déjà extrait par OCR (cache JSON). Une fois "
            "une traduction terminée, ce dossier peut être supprimé sans danger."
        ))
        self.cache_status_label = _wrapped_label("Aucune analyse effectuée pour l'instant.")
        self.cache_status_label.setObjectName("outputPreview")
        cache_card.body.addWidget(self.cache_status_label)

        # QVBoxLayout (pas QHBoxLayout) : trois boutons aux libellés assez
        # longs pour déborder sur une fenêtre étroite -- empilés
        # verticalement, ils ne débordent jamais, quelle que soit la
        # largeur de la fenêtre (demande explicite de l'utilisateur, page
        # Paramètres responsive).
        cache_row = QVBoxLayout()
        cache_row.setSpacing(8)
        self.scan_default_cache_button = QPushButton("Analyser le dossier de sortie par défaut")
        self.scan_default_cache_button.clicked.connect(lambda: self._scan_cache_dirs(None))
        self.pick_cache_folder_button = QPushButton("Choisir un autre dossier…")
        self.pick_cache_folder_button.clicked.connect(self._pick_cache_scan_folder)
        self.clear_cache_button = QPushButton("Vider les caches trouvés")
        self.clear_cache_button.setObjectName("danger")
        self.clear_cache_button.setEnabled(False)
        self.clear_cache_button.clicked.connect(self._clear_found_caches)
        cache_row.addWidget(self.scan_default_cache_button)
        cache_row.addWidget(self.pick_cache_folder_button)
        cache_row.addWidget(self.clear_cache_button)
        cache_card.body.addLayout(cache_row)
        root.addWidget(cache_card)

        root.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        self.pages.addWidget(scroll)  # index PAGE_SETTINGS

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        # La marge réservée ici est le seul « bord » qui reste, une fois le
        # cadre natif Windows supprimé -- c'est elle qui permet de saisir la
        # fenêtre pour la redimensionner (voir _edge_at / mousePressEvent).
        outer.setContentsMargins(RESIZE_MARGIN, RESIZE_MARGIN, RESIZE_MARGIN, RESIZE_MARGIN)
        outer.setSpacing(0)

        self.title_bar = TitleBar(self)
        icon_path = _asset_path("icon.ico")
        if icon_path.exists():
            self.title_bar.set_icon(QIcon(str(icon_path)).pixmap(18, 18))
        self.title_bar.minimize_clicked.connect(self.showMinimized)
        self.title_bar.maximize_clicked.connect(self._toggle_maximized)
        self.title_bar.close_clicked.connect(self.close)
        outer.addWidget(self.title_bar)

        # Trois écrans (demande explicite de l'utilisateur, 25/08/2026) :
        # un hall d'accueil (PAGE_HUB) qui choisit le service, l'écran de
        # traduction existant (PAGE_TRANSLATE, contenu inchangé), et un
        # nouvel écran d'outils sans rapport avec la traduction elle-même
        # (PAGE_TOOLS -- pour l'instant : annuler le nettoyage). Un seul
        # widget empilé, pas trois fenêtres : la fenêtre elle-même, sa
        # taille, sa barre de titre restent les mêmes tout du long.
        self.pages = QStackedWidget()
        outer.addWidget(self.pages, 1)

        self._build_hub_page()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self.pages.addWidget(scroll)  # index PAGE_TRANSLATE

        self._content = QWidget()
        scroll.setWidget(self._content)

        root = QVBoxLayout(self._content)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        back_row = QHBoxLayout()
        self.back_to_hub_button = QPushButton("← Menu")
        self.back_to_hub_button.setObjectName("backButton")
        self.back_to_hub_button.clicked.connect(lambda: self._navigate_to(PAGE_HUB))
        back_row.addWidget(self.back_to_hub_button)
        back_row.addStretch()
        root.addLayout(back_row)

        title = QLabel("TRANSLAX")
        title.setObjectName("appTitle")
        subtitle = QLabel("Traduction locale de documents · modèle NLLB-200 sur votre machine")
        subtitle.setObjectName("appSubtitle")
        root.addWidget(title)
        root.addWidget(subtitle)

        # --- Document ---------------------------------------------------
        doc_card = Card("Document à traduire")
        self.drop_zone = QFrame()
        self.drop_zone.setObjectName("dropZone")
        drop_layout = QVBoxLayout(self.drop_zone)
        drop_layout.setContentsMargins(18, 22, 18, 22)
        drop_layout.setSpacing(6)

        self.file_label = QLabel("Glissez un fichier PDF, EPUB, TXT ou MD ici")
        self.file_label.setObjectName("fileName")
        self.file_label.setAlignment(Qt.AlignCenter)
        self.file_details = QLabel("ou utilisez le bouton Parcourir")
        self.file_details.setObjectName("fileDetails")
        self.file_details.setAlignment(Qt.AlignCenter)
        self.file_details.setWordWrap(True)  # sinon un chemin très long élargit toute la fenêtre
        drop_layout.addWidget(self.file_label)
        drop_layout.addWidget(self.file_details)

        browse_row = QHBoxLayout()
        browse_row.addStretch()
        self.browse_button = QPushButton("Parcourir…")
        self.browse_button.clicked.connect(self._pick_file)
        browse_row.addWidget(self.browse_button)
        browse_row.addStretch()

        doc_card.body.addWidget(self.drop_zone)
        doc_card.body.addLayout(browse_row)
        root.addWidget(doc_card)

        # --- Réglages ---------------------------------------------------
        settings = Card("Réglages")
        self.src_combo = NoScrollComboBox()
        self.tgt_combo = NoScrollComboBox()
        for code, name in LANGUAGES.items():
            self.src_combo.addItem(name, code)
            self.tgt_combo.addItem(name, code)
        self.src_combo.setCurrentIndex(self.src_combo.findData(DEFAULT_SOURCE))
        self.tgt_combo.setCurrentIndex(self.tgt_combo.findData(DEFAULT_TARGET))

        # Bouton d'inversion (demande explicite de l'utilisateur, 25/08/2026) :
        # remplace la flèche simple, purement décorative, par une flèche
        # double cliquable qui échange langue source et langue cible --
        # utile pour aller vite (ex. relire une traduction dans l'autre
        # sens) sans rouvrir les deux menus déroulants.
        self.swap_lang_button = QPushButton("⇄")
        self.swap_lang_button.setObjectName("swapLangButton")
        self.swap_lang_button.setFixedSize(28, 28)
        self.swap_lang_button.setToolTip("Inverser langue source et langue cible")
        self.swap_lang_button.setCursor(Qt.PointingHandCursor)
        self.swap_lang_button.clicked.connect(self._swap_languages)

        self.tgt_label = QLabel("Langue cible")

        lang_row = QHBoxLayout()
        lang_row.setSpacing(10)
        lang_row.addWidget(self._field_label("Langue source"))
        lang_row.addWidget(self.src_combo, 1)
        lang_row.addWidget(self.swap_lang_button)
        lang_row.addWidget(self.tgt_label)
        lang_row.addWidget(self.tgt_combo, 1)
        settings.body.addLayout(lang_row)

        self.model_combo = NoScrollComboBox()
        # Modèles à licence commerciale d'abord, modèles à usage personnel
        # uniquement (NLLB/Meta) en dernier, en orange -- demande explicite
        # de l'utilisateur (25/08/2026) pour ne jamais les proposer en
        # premier par inadvertance. Ordre relatif conservé au sein de
        # chaque groupe (celui de MODEL_INFO, jamais réordonné lui-même).
        commercial_keys = [k for k in translate.MODEL_INFO if k not in PERSONAL_USE_ONLY_MODELS]
        personal_keys = [k for k in translate.MODEL_INFO if k in PERSONAL_USE_ONLY_MODELS]
        for key in commercial_keys + personal_keys:
            info = translate.MODEL_INFO[key]
            self.model_combo.addItem(info.label, key)
            if key in PERSONAL_USE_ONLY_MODELS:
                idx = self.model_combo.count() - 1
                self.model_combo.setItemData(idx, QColor(PERSONAL_USE_COLOR), Qt.ForegroundRole)
        # Présélection à l'écran : "600M — Turbo" (demande explicite de
        # l'utilisateur, 25/08/2026) -- DÉLIBÉRÉMENT différent de
        # `translate.DEFAULT_MODEL_KEY` ("600M", inchangé), qui reste le
        # repli de `pipeline.Job`/tests/CLI quand aucun modèle n'est précisé
        # explicitement. Mélanger les deux casserait la quasi-totalité des
        # tests existants, qui construisent des Job() sans model_key en
        # s'appuyant sur ce repli precise/FakeEngine.
        #
        # Le réglage direct de l'index (sans passer par
        # _set_model_combo_silently) est volontaire ici : à la construction,
        # aucun signal n'a encore été connecté (ligne suivante), donc rien
        # ne peut se déclencher par erreur -- pas besoin de suppression.
        self.model_combo.setCurrentIndex(self.model_combo.findData(UI_DEFAULT_MODEL_KEY))
        self.model_combo.currentIndexChanged.connect(self._update_model_info)
        self.model_combo.currentIndexChanged.connect(self._on_model_selected)
        # OPUS-MT n'a pas de repo_id fixe : son texte d'info dépend aussi de
        # la paire de langues choisie (voir _update_model_info), contrairement
        # aux autres moteurs -- sans ces deux connexions, changer de langue
        # laisserait affiché un état obsolète (taille/présence de l'ANCIENNE
        # paire) si OPUS-MT est le moteur sélectionné.
        self.src_combo.currentIndexChanged.connect(self._update_model_info)
        self.tgt_combo.currentIndexChanged.connect(self._update_model_info)

        model_row = QHBoxLayout()
        model_row.setSpacing(10)
        model_row.addWidget(self._field_label("Modèle"))
        model_row.addWidget(self.model_combo, 1)
        settings.body.addLayout(model_row)

        self.model_info_label = QLabel()
        self.model_info_label.setObjectName("outputPreview")
        self.model_info_label.setWordWrap(True)
        settings.body.addWidget(self.model_info_label)
        self._update_model_info()

        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("Dossier du fichier source")
        self.output_edit.setReadOnly(True)
        self.output_button = QPushButton("Choisir…")
        self.output_button.clicked.connect(self._pick_output_dir)
        self.output_reset_button = QPushButton("Réinitialiser")
        self.output_reset_button.setToolTip(
            "Revenir au comportement par défaut : sortie dans le même dossier que le fichier source."
        )
        self.output_reset_button.clicked.connect(self._reset_output_dir)

        out_row = QHBoxLayout()
        out_row.setSpacing(10)
        out_row.addWidget(self._field_label("Dossier de sortie"))
        out_row.addWidget(self.output_edit, 1)
        out_row.addWidget(self.output_button)
        out_row.addWidget(self.output_reset_button)
        settings.body.addLayout(out_row)

        # Format de sortie (demande explicite de l'utilisateur, 25/08/2026) :
        # le .md reste TOUJOURS écrit et reste seul utilisé pour la reprise
        # (voir core/pipeline.py::Job.output_format) -- choisir "PDF" ne
        # fait qu'ajouter un export supplémentaire à la fin, jamais à la
        # place. Markdown reste le choix par défaut.
        self.output_format_combo = NoScrollComboBox()
        self.output_format_combo.addItem("Markdown (.md)", "md")
        self.output_format_combo.addItem("PDF (.pdf)", "pdf")
        self.output_format_combo.currentIndexChanged.connect(self._update_output_preview)

        format_row = QHBoxLayout()
        format_row.setSpacing(10)
        format_row.addWidget(self._field_label("Format de sortie"))
        format_row.addWidget(self.output_format_combo, 1)
        settings.body.addLayout(format_row)

        self.output_preview = QLabel()
        self.output_preview.setObjectName("outputPreview")
        self.output_preview.setWordWrap(True)  # sinon le chemin est rogné
        settings.body.addWidget(self.output_preview)

        self.cleanup_check = QCheckBox("Nettoyer les titres et les traits d'union à la fin")
        # Décoché par défaut (demande explicite de l'utilisateur, 25/08/2026) :
        # ce nettoyage reste réversible (voir core/postprocess.py::undo_cleanup,
        # outil "Annuler le nettoyage") mais ne doit plus s'appliquer sans
        # que l'utilisateur l'ait choisi pour CE job précis.
        self.cleanup_check.setChecked(False)
        settings.body.addWidget(self.cleanup_check)

        self.extract_only_check = QCheckBox(
            "Extraction seulement — texte source nettoyé, sans traduction"
        )
        self.extract_only_check.setToolTip(
            "Bénéficie du même nettoyage (en-têtes/pieds de page, vision IA pour Traduire X) "
            "sans traduire : utile pour juste récupérer un texte propre dans sa langue d'origine."
        )
        self.extract_only_check.toggled.connect(self._on_extract_only_toggled)
        settings.body.addWidget(self.extract_only_check)

        # Lecture seule + clic pour ouvrir ApiKeysDialog (demande explicite
        # de l'utilisateur, 25/08/2026) : plus modifiable directement dans
        # ce champ -- garde le même widget/nom (`api_key_edit`, encore lu
        # tel quel par `_start_vision`) pour ne rien casser côté logique,
        # juste la façon dont sa valeur est éditée qui change.
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        self.api_key_edit.setReadOnly(True)
        self.api_key_edit.setCursor(Qt.PointingHandCursor)
        self.api_key_edit.setPlaceholderText("Cliquer pour configurer… (uniquement pour Traduire X)")
        stored_key = settings_mod.get_anthropic_api_key()
        if stored_key:
            self.api_key_edit.setText(stored_key)
        self.api_key_edit.mousePressEvent = self._open_api_keys_dialog

        api_row = QHBoxLayout()
        api_row.setSpacing(10)
        api_row.addWidget(self._field_label("Clé API Anthropic"))
        api_row.addWidget(self.api_key_edit, 1)
        settings.body.addLayout(api_row)

        # Sélecteur (pas une case à cocher -- demande explicite de
        # l'utilisateur, 26/08/2026) : les 4 fournisseurs déjà évoqués pour
        # l'OCR/vision de Traduire X, tous dans la liste. Seuls PaddleOCR
        # et Anthropic sont RÉELLEMENT branchés à une fonctionnalité
        # aujourd'hui (voir core/vision_ocr.py) -- xAI/OpenAI n'ont que
        # leur clé API préparée (voir core/settings.py), aucun appel
        # réel : listés quand même (comme demandé), mais grisés/orange et
        # non sélectionnables, plutôt que de laisser croire qu'ils
        # marchent déjà. PaddleOCR reste le choix par défaut (index 0) --
        # gratuit, local, aucune clé requise, comportement inchangé.
        self.vision_model_combo = NoScrollComboBox()
        for key, label, available in (
            ("paddleocr", "PaddleOCR (local, gratuit)", True),
            ("anthropic", "Claude — Anthropic (payant, nécessite une clé)", True),
            ("xai", "Grok — xAI (bientôt disponible)", False),
            ("openai", "ChatGPT — OpenAI (bientôt disponible)", False),
        ):
            self.vision_model_combo.addItem(label, key)
            idx = self.vision_model_combo.count() - 1
            if not available:
                self.vision_model_combo.model().item(idx).setEnabled(False)
                # Même orange que les modèles à usage personnel du
                # sélecteur principal (PERSONAL_USE_COLOR) -- ici le sens
                # est « pas encore disponible », pas une question de
                # licence, mais le même signal visuel « à part » convient.
                self.vision_model_combo.setItemData(idx, QColor(PERSONAL_USE_COLOR), Qt.ForegroundRole)
        self.vision_model_combo.setToolTip(
            "PaddleOCR (par défaut) est gratuit et tourne entièrement sur cette machine, sans "
            "connexion internet. Claude comprend mieux le contexte sur les cas très difficiles, "
            "mais facture chaque page et nécessite une clé API personnelle. Grok et ChatGPT sont "
            "préparés pour une future intégration, pas encore utilisables."
        )
        vision_row = QHBoxLayout()
        vision_row.setSpacing(10)
        vision_row.addWidget(self._field_label("Modèle OCR (Traduire X)"))
        vision_row.addWidget(self.vision_model_combo, 1)
        settings.body.addLayout(vision_row)

        api_note = QLabel(
            "Traduire X utilise l'OCR local (PaddleOCR, gratuit, aucune clé requise) par défaut. "
            "La clé Anthropic ci-dessus ne sert que si « Claude » est choisi ci-dessus — crée la "
            "tienne sur console.anthropic.com si besoin. Traduire reste 100 % local et gratuit "
            "dans tous les cas. Cliquer sur le champ ci-dessus ouvre la fenêtre de saisie "
            "(prépare aussi xAI/OpenAI pour de futures intégrations)."
        )
        api_note.setObjectName("outputPreview")
        api_note.setWordWrap(True)
        settings.body.addWidget(api_note)

        root.addWidget(settings)

        # --- Actions ----------------------------------------------------
        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.translate_button = QPushButton("Traduire")
        self.translate_button.setObjectName("primary")
        self.translate_button.clicked.connect(self._start)
        self.translate_x_button = QPushButton("Traduire X")
        self.translate_x_button.setToolTip(
            "Comme Traduire, avec en plus une extraction par vision IA pour les scans "
            "difficiles -- nécessite internet et une clé API Anthropic (Réglages), facturée à l'usage."
        )
        self.translate_x_button.clicked.connect(self._start_vision)
        # Pause (vert, non destructif) et Stop (rouge, destructif) --
        # demande explicite de l'utilisateur, 26/08/2026 : l'ancien bouton
        # Stop unique interrompait déjà en laissant une reprise possible
        # (voir core/state.py) -- exactement ce que Pause fait maintenant,
        # inchangé. Stop est un NOUVEAU comportement : interrompre ET
        # abandonner définitivement (voir _stop/_pause plus bas).
        # Icônes SVG réelles, pas des emoji (demande explicite de
        # l'utilisateur, 26/08/2026) -- si Qt ne peut pas les charger (ex.
        # plugin SVG absent dans l'exe gelé), le bouton garde son texte
        # seul plutôt qu'un repli en emoji (voir _svg_icon).
        self.pause_button = QPushButton("Pause")
        self.pause_button.setObjectName("pauseButton")
        pause_icon = _svg_icon("pause.svg")
        if pause_icon is not None:
            self.pause_button.setIcon(pause_icon)
            self.pause_button.setIconSize(QSize(14, 14))
        self.pause_button.setToolTip(
            "Interrompt après le segment en cours, sans rien perdre -- vous pouvez fermer "
            "TRANSLAX et reprendre plus tard."
        )
        self.pause_button.clicked.connect(self._pause)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("danger")
        stop_icon = _svg_icon("stop.svg")
        if stop_icon is not None:
            self.stop_button.setIcon(stop_icon)
            self.stop_button.setIconSize(QSize(14, 14))
        self.stop_button.setToolTip(
            "Interrompt ET abandonne définitivement cette traduction (confirmation demandée) -- "
            "le texte déjà traduit reste sur le disque, mais elle ne sera plus proposée à la reprise."
        )
        self.stop_button.clicked.connect(self._stop)
        self.reboost_button = QPushButton("Reboost")
        self.reboost_button.setToolTip(
            "Vérifie si la traduction avance toujours -- ne met rien en pause, ne relance rien."
        )
        self.reboost_button.clicked.connect(lambda: self._reboost(automatic=False))
        actions.addWidget(self.translate_button, 2)
        actions.addWidget(self.translate_x_button, 2)
        actions.addWidget(self.pause_button, 1)
        actions.addWidget(self.stop_button, 1)
        actions.addWidget(self.reboost_button, 1)
        root.addLayout(actions)

        # Même hauteur pour les 5 boutons, reprise sur "Traduire" (demande
        # explicite de l'utilisateur -- ils étaient désynchronisés :
        # "primary"/"danger"/"pauseButton" ont un padding plus généreux que
        # le style de bouton par défaut dans styles.qss). `setFixedHeight`
        # plutôt que d'essayer d'harmoniser le padding des styles QSS
        # différents -- garantit un résultat pixel identique, quel que soit
        # le style.
        button_height = self.translate_button.sizeHint().height()
        for btn in (
            self.translate_button, self.translate_x_button,
            self.pause_button, self.stop_button, self.reboost_button,
        ):
            btn.setFixedHeight(button_height)

        # --- Progression ------------------------------------------------
        progress_card = Card("Progression")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        progress_card.body.addWidget(self.progress_bar)

        # Anime chaque avancée de la barre plutôt que de sauter directement
        # à la nouvelle valeur -- discret (courte durée, pas d'à-coup) mais
        # visible : la progression avance en glissant, pas par saccades.
        self._progress_animation = QPropertyAnimation(self.progress_bar, b"value", self)
        self._progress_animation.setDuration(PROGRESS_ANIMATION_MS)
        self._progress_animation.setEasingCurve(QEasingCurve.OutCubic)

        self.stats_label = QLabel("En attente d'un document.")
        self.stats_label.setObjectName("stats")
        progress_card.body.addWidget(self.stats_label)

        # Visible UNIQUEMENT pendant qu'une traduction tourne (voir
        # _set_running) : la mise en veille peut durer des heures, mieux
        # vaut le rappeler pendant tout le processus plutôt qu'une seule
        # fois au démarrage, facile à manquer.
        self.keep_awake_label = QLabel(
            "Mise en veille du système désactivée pendant la traduction — pensez à brancher votre chargeur."
        )
        self.keep_awake_label.setObjectName("keepAwake")
        self.keep_awake_label.setWordWrap(True)
        self.keep_awake_label.setVisible(False)
        progress_card.body.addWidget(self.keep_awake_label)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(MAX_LOG_LINES)
        self.log.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.log.setMinimumHeight(85)
        progress_card.body.addWidget(self.log, 1)

        result_row = QHBoxLayout()
        result_row.setSpacing(8)
        self.open_file_button = QPushButton("Ouvrir le fichier")
        self.open_file_button.clicked.connect(lambda: self._open(self.result_path))
        self.open_folder_button = QPushButton("Ouvrir le dossier")
        self.open_folder_button.setToolTip("Ouvre l'emplacement du fichier de sortie, sélectionné dans l'explorateur")
        self.open_folder_button.clicked.connect(self._open_output_location)
        self.open_file_button.setEnabled(False)   # rien à ouvrir tant qu'aucun
        self.open_folder_button.setEnabled(False)  # résultat n'existe
        result_row.addStretch()
        result_row.addWidget(self.open_file_button)
        result_row.addWidget(self.open_folder_button)
        progress_card.body.addLayout(result_row)
        root.addWidget(progress_card, 1)

        # Hors de la zone de défilement, délibérément : toujours visible en
        # bas de la fenêtre, jamais emporté quand on fait défiler le contenu.
        self.version_label = QLabel(version.version_string())
        self.version_label.setObjectName("versionLabel")
        self.version_label.setAlignment(Qt.AlignCenter)
        outer.addWidget(self.version_label)

        self._build_tools_page()
        self._build_settings_page()

        # Le hall d'accueil, pas l'écran de traduction, est le tout premier
        # écran vu au lancement (demande explicite de l'utilisateur).
        self.pages.setCurrentIndex(PAGE_HUB)

    def _build_tools_page(self) -> None:
        """
        Écran « Outils » (demande explicite de l'utilisateur, 25/08/2026) :
        pour tout ce qui n'est pas de la traduction à proprement parler --
        pour l'instant, annuler le nettoyage des titres/traits d'union sur
        un fichier déjà traduit (voir core/postprocess.py::undo_cleanup).
        """
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        back_row = QHBoxLayout()
        back_btn = QPushButton("← Menu")
        back_btn.setObjectName("backButton")
        back_btn.clicked.connect(lambda: self._navigate_to(PAGE_HUB))
        back_row.addWidget(back_btn)
        back_row.addStretch()
        root.addLayout(back_row)

        title = QLabel("Outils")
        title.setObjectName("appTitle")
        subtitle = QLabel("Fonctionnalités indépendantes de la traduction elle-même.")
        subtitle.setObjectName("appSubtitle")
        root.addWidget(title)
        root.addWidget(subtitle)

        undo_card = Card("Annuler le nettoyage des titres et traits d'union")
        undo_card.body.addWidget(QLabel(
            "Choisissez un fichier .md déjà traduit par TRANSLAX avec le nettoyage activé : "
            "restaure exactement le contenu tel qu'il était avant ce nettoyage."
        ))

        file_row = QHBoxLayout()
        self.undo_path_edit = QLineEdit()
        self.undo_path_edit.setPlaceholderText("Fichier .md à restaurer…")
        self.undo_path_edit.textChanged.connect(self._update_undo_status)
        browse_undo_btn = QPushButton("Parcourir…")
        browse_undo_btn.clicked.connect(self._pick_undo_file)
        file_row.addWidget(self.undo_path_edit, 1)
        file_row.addWidget(browse_undo_btn)
        undo_card.body.addLayout(file_row)

        self.undo_status_label = QLabel("")
        self.undo_status_label.setObjectName("outputPreview")
        self.undo_status_label.setWordWrap(True)
        undo_card.body.addWidget(self.undo_status_label)

        undo_row = QHBoxLayout()
        undo_row.addStretch()
        self.undo_button = QPushButton("Annuler le nettoyage")
        self.undo_button.setObjectName("primary")
        self.undo_button.setEnabled(False)
        self.undo_button.clicked.connect(self._on_undo_cleanup_clicked)
        undo_row.addWidget(self.undo_button)
        undo_card.body.addLayout(undo_row)

        root.addWidget(undo_card)
        root.addStretch(1)

        self.pages.addWidget(page)  # index PAGE_TOOLS

    def _pick_undo_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choisir un fichier traduit", "", "Markdown (*.md)")
        if path:
            self.undo_path_edit.setText(path)

    def _update_undo_status(self) -> None:
        from core import postprocess
        text = self.undo_path_edit.text().strip()
        if not text:
            self.undo_status_label.setText("")
            self.undo_button.setEnabled(False)
            return
        path = Path(text)
        if not path.exists():
            self.undo_status_label.setText("Fichier introuvable.")
            self.undo_button.setEnabled(False)
        elif postprocess.has_backup(path):
            self.undo_status_label.setText("Une sauvegarde d'avant nettoyage existe pour ce fichier : prêt à annuler.")
            self.undo_button.setEnabled(True)
        else:
            self.undo_status_label.setText(
                "Aucune sauvegarde trouvée pour ce fichier -- soit il n'a jamais été nettoyé, "
                "soit le nettoyage a déjà été annulé une fois."
            )
            self.undo_button.setEnabled(False)

    def _on_undo_cleanup_clicked(self) -> None:
        from core import postprocess
        path = Path(self.undo_path_edit.text().strip())
        undone = postprocess.undo_cleanup(path)
        # Instance explicite + .exec(), pas les méthodes statiques
        # QMessageBox.information()/.warning() : ces raccourcis n'exposent
        # pas le même .exec() que le reste de l'appli mocke dans les tests
        # (voir tests/test_ui.py) -- cohérent avec toutes les autres boîtes
        # de dialogue de ce fichier, pas juste une question de test.
        box = QMessageBox(self)
        if undone:
            box.setIcon(QMessageBox.Information)
            box.setWindowTitle("Nettoyage annulé")
            box.setText(f"« {path.name} » a été restauré tel qu'avant nettoyage.")
        else:
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("Rien à annuler")
            box.setText(f"Aucune sauvegarde disponible pour « {path.name} ».")
        box.addButton("Compris", QMessageBox.AcceptRole)
        box.exec()
        self._update_undo_status()

    # --------------------------------------------------- barre de titre
    def _toggle_maximized(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def changeEvent(self, event) -> None:  # noqa: N802 - API Qt
        """
        Garde l'icône Agrandir/Restaurer synchronisée avec l'état réel de la
        fenêtre -- pas seulement quand on clique le bouton, mais aussi après
        un double-clic sur la barre, un Win+Haut, ou un « snap » Windows.
        """
        if event.type() == QEvent.WindowStateChange:
            self.title_bar.set_maximized(self.isMaximized())
        super().changeEvent(event)

    # --------------------------------------------- redimensionnement (bords)
    def _edge_at(self, x: int, y: int) -> Qt.Edges:
        """Bord(s) de la fenêtre sous le curseur, dans la marge réservée par
        `_build_ui` (vide si la fenêtre est maximisée : rien à saisir)."""
        if self.isMaximized():
            return Qt.Edges()
        edges = Qt.Edges()
        width, height = self.width(), self.height()
        if x <= RESIZE_MARGIN:
            edges |= Qt.Edge.LeftEdge
        elif x >= width - RESIZE_MARGIN:
            edges |= Qt.Edge.RightEdge
        if y <= RESIZE_MARGIN:
            edges |= Qt.Edge.TopEdge
        elif y >= height - RESIZE_MARGIN:
            edges |= Qt.Edge.BottomEdge
        return edges

    @staticmethod
    def _cursor_for(edges: Qt.Edges) -> Qt.CursorShape:
        left = bool(edges & Qt.Edge.LeftEdge)
        right = bool(edges & Qt.Edge.RightEdge)
        top = bool(edges & Qt.Edge.TopEdge)
        bottom = bool(edges & Qt.Edge.BottomEdge)
        if (top and left) or (bottom and right):
            return Qt.SizeFDiagCursor
        if (top and right) or (bottom and left):
            return Qt.SizeBDiagCursor
        if left or right:
            return Qt.SizeHorCursor
        if top or bottom:
            return Qt.SizeVerCursor
        return Qt.ArrowCursor

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - API Qt
        # Ne s'exécute que quand le curseur survole MainWindow directement
        # (la marge de redimensionnement) : la barre de titre et le contenu,
        # eux, interceptent l'évènement avant qu'il n'arrive ici.
        point = event.position().toPoint()
        edges = self._edge_at(point.x(), point.y())
        self.setCursor(self._cursor_for(edges))
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - API Qt
        if event.button() == Qt.LeftButton:
            point = event.position().toPoint()
            edges = self._edge_at(point.x(), point.y())
            handle = self.windowHandle()
            if edges and handle is not None:
                handle.startSystemResize(edges)
                event.accept()
                return
        super().mousePressEvent(event)

    @staticmethod
    def _field_label(text: str) -> QLabel:
        """Libellé de champ à largeur fixe, pour aligner les lignes entre elles."""
        label = QLabel(text)
        label.setMinimumWidth(110)
        return label

    # -------------------------------------------------------- fichier source
    def _pick_file(self) -> None:
        start_dir = str(self.source_path.parent) if self.source_path else ""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choisir un document",
            start_dir,
            "Documents (*.pdf *.epub *.txt *.md *.markdown);;Tous les fichiers (*)",
        )
        if path:
            self._set_source(Path(path))

    def _set_source(self, path: Path) -> None:
        if not extract.is_supported(path):
            QMessageBox.warning(
                self,
                "Format non pris en charge",
                f"« {path.name} » n'est pas un PDF, un EPUB, un TXT ni un MD.",
            )
            return
        self.source_path = path
        self.result_path = None
        kind = {"": "texte"}.get(path.suffix.lower(), path.suffix.lower().lstrip("."))
        self.file_label.setText(path.name)
        self.file_details.setText(f"{kind.upper()} · {format_size(path.stat().st_size)} · {path.parent}")
        self.drop_zone.setProperty("filled", True)
        self._restyle(self.drop_zone)
        self._update_output_preview()
        self.stats_label.setText("Prêt à traduire.")
        self.open_file_button.setEnabled(False)
        self.open_folder_button.setEnabled(False)

    def _pick_output_dir(self) -> None:
        start = str(self.output_dir or (self.source_path.parent if self.source_path else Path.home()))
        folder = QFileDialog.getExistingDirectory(self, "Dossier de sortie", start)
        if folder:
            self.output_dir = Path(folder)
            self.output_edit.setText(folder)
            # Mémorisé pour les prochains lancements (demande explicite de
            # l'utilisateur) -- pas seulement pour cette session.
            settings_mod.set_default_output_dir(self.output_dir)
            self._update_output_preview()

    def _reset_output_dir(self) -> None:
        """Revient au comportement par défaut (sortie dans le même dossier
        que le fichier source) et efface le réglage mémorisé."""
        self.output_dir = None
        self.output_edit.clear()
        settings_mod.set_default_output_dir(None)
        self._update_output_preview()

    def _resolved_output(self) -> Path | None:
        if self.source_path is None:
            return None
        return pipeline.default_output_path(self.source_path, self.output_dir)

    def _update_output_preview(self) -> None:
        out = self._resolved_output()
        pdf_suffix = (
            f" + export PDF ({out.stem}.pdf)" if out is not None
            and self.output_format_combo.currentData() == "pdf" else ""
        )
        if out is None:
            self.output_preview.setText("Fichier de sortie : —")
        elif self.extract_only_check.isChecked():
            # Pas de traduction, donc pas de titre traduit ni de renommage --
            # le fichier garde le nom dérivé de la source, comme affiché ici.
            self.output_preview.setText(
                f"Fichier de sortie :  {out.name}{pdf_suffix}   (créé dans {out.parent} ; "
                "langue source conservée, aucun renommage)"
            )
        else:
            # Le nom définitif dépend du titre traduit -- connu et appliqué
            # dès le tout début de la traduction (voir pipeline.py), pas
            # seulement une fois terminé. Ce nom-ci n'est qu'un point de
            # départ affiché avant que la traduction n'ait commencé.
            self.output_preview.setText(
                f"Fichier de sortie :  {out.name}{pdf_suffix}   (créé dans {out.parent} ; "
                "renommé selon le titre traduit dès le début de la traduction)"
            )

    def _update_model_info(self) -> None:
        model_key = self.model_combo.currentData()
        info = translate.MODEL_INFO[model_key]
        src, tgt = self.src_combo.currentData(), self.tgt_combo.currentData()

        if info.engine == "opus-mt":
            # Pas de repo_id fixe (voir MODEL_INFO["opus-mt"]) : résolu ici
            # pour CETTE paire précise, potentiellement None si une des deux
            # langues n'a pas de correspondance ISO 639-1 connue.
            repo_id = translate.opus_mt_repo_id(src, tgt)
            if repo_id is None:
                presence = "paire non prise en charge par OPUS-MT (langue sans correspondance connue)"
            elif translate.is_model_ready(model_key, src, tgt):
                size_bytes = translate.cached_size_bytes(repo_id)
                presence = f"déjà téléchargé pour {src}->{tgt} ({format_size(size_bytes)})" if size_bytes \
                    else f"déjà téléchargé pour {src}->{tgt}"
            else:
                presence = f"à télécharger pour {src}->{tgt} — estimé ~{info.size_gb:.1f} Go, variable selon la paire"
        elif translate.is_model_ready(model_key, src, tgt):
            if info.engine == "fast":
                presence = "déjà converti"
            else:
                size_bytes = translate.cached_size_bytes(info.repo_id)
                presence = f"déjà téléchargé ({format_size(size_bytes)})" if size_bytes else "déjà téléchargé"
        else:
            marque = "estimé, " if info.size_is_estimate else ""
            if info.engine == "fast":
                presence = f"à convertir localement — {marque}~{info.size_gb:.1f} Go une fois converti"
            else:
                presence = f"à télécharger — {marque}~{info.size_gb:.1f} Go"
        self.model_info_label.setText(f"{info.description}   ·   {info.speed_note}   ·   {presence}")

    def _set_model_combo(self, model_key: str) -> None:
        """
        Change le modèle sélectionné SANS ouvrir l'avertissement d'usage
        personnel (voir `_on_model_selected`) -- pour toute restauration
        programmatique (reprise d'un job mémorisé, etc.), où ce choix a
        déjà été fait par l'utilisateur lors d'un lancement précédent, pas
        cliqué à l'instant.
        """
        idx = self.model_combo.findData(model_key)
        if idx < 0:
            return
        self._suppress_model_notice = True
        try:
            self.model_combo.setCurrentIndex(idx)
        finally:
            self._suppress_model_notice = False

    def _on_model_selected(self, index: int) -> None:
        """
        Avertissement d'usage personnel (demande explicite de l'utilisateur,
        25/08/2026) : cliquer sur un des profils NLLB/Meta ("le modèle
        Facebook") ouvre un rappel clair de la licence CC-BY-NC -- usage
        commercial interdit, contrairement à OPUS-MT/MADLAD-400 (voir
        SPEC.md §5 quaterdecies). Ne s'affiche que sur un vrai clic
        utilisateur, jamais lors d'une restauration programmatique (voir
        `_set_model_combo` / `_suppress_model_notice`).
        """
        if self._suppress_model_notice:
            return
        key = self.model_combo.itemData(index)
        if key not in PERSONAL_USE_ONLY_MODELS:
            return
        info = translate.MODEL_INFO[key]
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Usage strictement personnel")
        box.setText(f"« {info.label} » est un modèle Meta (NLLB-200), pas un moteur commercial.")
        box.setInformativeText(
            "Licence CC-BY-NC 4.0 : usage commercial explicitement interdit. Ce profil est gardé "
            "dans TRANSLAX uniquement pour un usage personnel -- jamais pour un service vendu à des "
            "tiers.\n\nPour une utilisation commerciale, choisissez OPUS-MT ou MADLAD-400 "
            "(licences CC-BY 4.0 / Apache 2.0, en haut de la liste)."
        )
        box.addButton("Compris", QMessageBox.AcceptRole)
        box.exec()

    def _swap_languages(self) -> None:
        """
        Inverse langue source et langue cible (demande explicite de
        l'utilisateur, 25/08/2026, "un switch qui s'effectue correctement").
        Passe par les codes (`currentData`/`findData`) plutôt que par les
        index de position : les deux menus sont peuplés à l'identique
        (voir plus haut), donc les index correspondent déjà, mais raisonner
        en codes reste correct même si ça changeait un jour, et ne plante
        jamais si un code venait à manquer dans l'autre menu (findData
        renvoie alors -1, ignoré).
        """
        src_code = self.src_combo.currentData()
        tgt_code = self.tgt_combo.currentData()
        new_src_idx = self.src_combo.findData(tgt_code)
        new_tgt_idx = self.tgt_combo.findData(src_code)
        if new_src_idx >= 0:
            self.src_combo.setCurrentIndex(new_src_idx)
        if new_tgt_idx >= 0:
            self.tgt_combo.setCurrentIndex(new_tgt_idx)

    def _on_extract_only_toggled(self, checked: bool) -> None:
        """
        Extraction seulement (demande explicite de l'utilisateur) : réutilise
        exactement Traduire/Traduire X (mêmes boutons, même pipeline jusqu'à
        la segmentation, voir `pipeline.Job.extract_only`) -- ce bascule ne
        fait qu'ajuster ce qui n'a plus de sens dans ce mode : langue cible
        et modèle NLLB (aucun des deux n'intervient sans traduction), et les
        libellés des boutons pour ne jamais afficher « Traduire » sur une
        action qui ne traduit pas.
        """
        self.translate_button.setText("Extraire" if checked else "Traduire")
        self.translate_x_button.setText("Extraire X" if checked else "Traduire X")
        self.tgt_combo.setEnabled(not checked)
        self.tgt_label.setEnabled(not checked)
        self.swap_lang_button.setEnabled(not checked)
        self.model_combo.setEnabled(not checked)
        self.model_info_label.setEnabled(not checked)
        self._update_output_preview()

    # ------------------------------------------------------ glisser-déposer
    def dragEnterEvent(self, event) -> None:  # noqa: N802 - API Qt
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if urls and extract.is_supported(Path(urls[0].toLocalFile())):
            event.acceptProposedAction()
            self.drop_zone.setProperty("hover", True)
            self._restyle(self.drop_zone)

    def dragLeaveEvent(self, event) -> None:  # noqa: N802 - API Qt
        self.drop_zone.setProperty("hover", False)
        self._restyle(self.drop_zone)

    def dropEvent(self, event) -> None:  # noqa: N802 - API Qt
        self.drop_zone.setProperty("hover", False)
        self._restyle(self.drop_zone)
        urls = event.mimeData().urls()
        if urls:
            self._set_source(Path(urls[0].toLocalFile()))
            event.acceptProposedAction()

    @staticmethod
    def _restyle(widget: QWidget) -> None:
        """Force Qt à réappliquer la feuille de style après un setProperty."""
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    # ---------------------------------------------------------- traduction
    def _start(self) -> None:
        self._start_impl(force_resume=False)

    def _start_vision(self) -> None:
        """
        Bouton Traduire X -- même chemin que Traduire (`_start_impl`), avec
        en plus l'extraction par OCR en amont (voir `core/vision_ocr.py`).

        Le fournisseur vient du sélecteur `vision_model_combo` (demande
        explicite de l'utilisateur, 26/08/2026 -- remplace l'ancienne case
        à cocher). PaddleOCR (par défaut, index 0) est gratuit, local,
        aucune clé requise -- comportement inchangé. La clé API Anthropic
        n'est exigée que si « Claude » est le choix sélectionné.
        """
        if self.source_path is not None and self.source_path.suffix.lower() != ".pdf":
            QMessageBox.information(
                self,
                "Traduire X : PDF uniquement",
                "Traduire X sert à corriger un scan PDF mal reconnu -- un fichier .epub/.txt/.md "
                "n'a pas d'image à relire, son texte est déjà la référence. Utilise Traduire.",
            )
            return

        vision_provider = self.vision_model_combo.currentData()
        if vision_provider != "anthropic":
            # Grok/ChatGPT sont désactivés dans le sélecteur (voir
            # _build_ui) -- injoignables par un clic normal, mais ce
            # garde-fou évite de lancer un job avec un fournisseur non
            # implémenté si jamais sélectionné autrement (ex. un futur
            # test direct sur le combo).
            if vision_provider not in ("paddleocr", "anthropic"):
                QMessageBox.warning(
                    self, "Pas encore disponible",
                    f"« {self.vision_model_combo.currentText()} » n'est pas encore implémenté -- "
                    "choisis PaddleOCR ou Claude (Anthropic).",
                )
                return
            self._start_impl(force_resume=False, use_vision=True, vision_provider="paddleocr")
            return

        api_key = self.api_key_edit.text().strip()
        if not api_key:
            QMessageBox.warning(
                self,
                "Clé API requise",
                "Le mode Claude (Anthropic) de Traduire X a besoin d'une clé API -- renseigne-la "
                "dans Réglages ▸ Clé API Anthropic (créée sur console.anthropic.com), ou choisis "
                "PaddleOCR dans le sélecteur pour rester sur l'OCR local, gratuit.",
            )
            self._open_api_keys_dialog()  # ouvre directement la saisie, le champ n'étant plus modifiable en place
            return
        self._start_impl(force_resume=False, use_vision=True, vision_provider="anthropic", api_key=api_key)

    def _open_api_keys_dialog(self, event=None) -> None:
        """
        Ouvre ApiKeysDialog -- branché sur le clic du champ (lecture seule)
        de la page principale, et rappelé directement si Traduire X est
        cliqué sans clé déjà enregistrée. `event=None` : accepté pour
        pouvoir être assigné tel quel comme `mousePressEvent` du champ
        (Qt appelle alors cette méthode avec l'évènement de clic), tout en
        restant appelable sans argument depuis un bouton/menu classique.
        """
        dialog = ApiKeysDialog(
            self.api_key_edit.text(),
            settings_mod.get_xai_api_key() or "",
            settings_mod.get_openai_api_key() or "",
            parent=self,
        )
        if dialog.exec() == QDialog.Accepted:
            anthropic_key = dialog.anthropic_edit.text().strip()
            self.api_key_edit.setText(anthropic_key)
            settings_mod.set_anthropic_api_key(anthropic_key)
            settings_mod.set_xai_api_key(dialog.xai_edit.text().strip())
            settings_mod.set_openai_api_key(dialog.openai_edit.text().strip())

    def _start_impl(
        self, force_resume: bool, use_vision: bool = False,
        vision_provider: str = "paddleocr", api_key: str | None = None,
    ) -> None:
        """
        `force_resume=True` : utilisé UNIQUEMENT par `_offer_resume_pending_jobs`
        (proposition automatique au démarrage) -- saute la boîte de dialogue
        « fichier déjà existant, reprendre/écraser ? » puisque l'utilisateur
        vient déjà de répondre « Reprendre » à la proposition automatique ;
        la revoir juste après serait redondant. `state.can_resume` a de
        toute façon déjà été vérifié avant de proposer quoi que ce soit.

        `use_vision=True` : utilisé UNIQUEMENT par `_start_vision` (bouton
        Traduire X) -- extraction par vision IA avant la traduction, voir
        `core/vision_ocr.py`. Tout le reste de cette méthode est identique
        aux deux boutons, comme demandé : rien n'est réinventé pour Traduire X.
        """
        if self.source_path is None:
            QMessageBox.information(self, "Aucun document", "Choisissez d'abord un fichier à traduire.")
            return
        if not self.source_path.exists():
            QMessageBox.warning(self, "Fichier introuvable", f"{self.source_path} n'existe plus.")
            return

        extract_only = self.extract_only_check.isChecked()
        src = self.src_combo.currentData()
        tgt = self.tgt_combo.currentData()
        model_key = self.model_combo.currentData()
        # Langue cible et modèle NLLB n'interviennent pas sans traduction --
        # ni la comparaison des langues, ni la confirmation de téléchargement
        # (des Go pour un modèle qui ne servirait à rien ici) n'ont de sens.
        if not extract_only:
            if src == tgt:
                QMessageBox.warning(self, "Langues identiques", "La langue source et la langue cible sont les mêmes.")
                return
            if not self._confirm_model_download(model_key, src, tgt):
                return

        out_path = self._resolved_output()
        resume_mode = "auto" if force_resume else self._resolve_conflict(out_path)
        if resume_mode is None:
            return

        cleanup = self.cleanup_check.isChecked()
        job = pipeline.Job(
            input_path=self.source_path,
            output_path=out_path,
            src_lang=src,
            tgt_lang=tgt,
            model_key=model_key,
            cleanup=cleanup,
            resume=resume_mode,
            use_vision_ocr=use_vision,
            vision_provider=vision_provider,
            anthropic_api_key=api_key,
            extract_only=extract_only,
            output_format=self.output_format_combo.currentData(),
        )

        # Mémorisé pour proposer automatiquement de reprendre ce job au
        # prochain lancement de l'appli s'il ne se termine pas normalement
        # (voir _offer_resume_pending_jobs / core/settings.py). Écrit ICI, avant
        # même que la traduction ne démarre : si l'appli plante juste après,
        # ce repère doit déjà être sur le disque.
        #
        # `use_vision_ocr`/la clé API ne sont volontairement PAS mémorisés
        # ici : une reprise saute toujours directement à la traduction des
        # segments (voir pipeline.run_job, la reprise ne repasse jamais par
        # l'extraction), donc ce réglage ne changerait jamais rien au
        # comportement d'une reprise -- inutile de dupliquer la clé API dans
        # ce fichier en plus du réglage dédié. Un « Traduire X » interrompu
        # PENDANT l'extraction vision elle-même (avant toute segmentation)
        # n'est pas encore suivi par ce mécanisme : un nouveau clic sur
        # Traduire X reprend quand même sans repayer les pages déjà faites,
        # grâce au cache propre à core/vision_ocr.py -- juste pas proposé
        # automatiquement au démarrage dans ce cas précis.
        # `add_pending_job` (pas `set_last_job`, remplacé -- demande
        # explicite de l'utilisateur, 26/08/2026) : plusieurs traductions
        # interrompues peuvent coexister maintenant, pas seulement la
        # dernière -- voir _offer_resume_pending_jobs. Indexé par
        # `str(out_path)`, mémorisé ici pour que _on_finished retire la
        # BONNE entrée même si le fichier a été renommé entre-temps.
        self._current_job_output_key = str(out_path)
        settings_mod.add_pending_job({
            "input_path": str(self.source_path),
            "output_path": str(out_path),
            "src_lang": src,
            "tgt_lang": tgt,
            "model_key": model_key,
            "cleanup": cleanup,
            "output_format": self.output_format_combo.currentData(),
        })

        self.log.clear()
        self.progress_bar.setRange(0, 0)  # indéterminé pendant le chargement
        self.result_path = out_path
        self._append_log(f"Source : {self.source_path}")
        self._append_log(f"Sortie : {out_path}")
        self._set_running(True)

        self.thread = QThread(self)
        self.worker = TranslationWorker(job)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.status.connect(self._on_status)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.worker.failed.connect(self._on_failed)
        self.worker.cleanup_review_needed.connect(self._on_cleanup_review_needed)
        self.worker.vision_review_needed.connect(self._on_vision_review_needed)
        self.worker.vision_progress.connect(self._on_vision_progress)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.finished.connect(self._on_thread_finished)
        self.thread.start()

    def _apply_job_snapshot(self, snapshot: dict, *, apply_model: bool = True) -> None:
        """
        Repeuple les champs de l'interface à partir d'un job mémorisé (voir
        core/settings.py), pour que `_start_impl(force_resume=True)` relance
        exactement la même configuration que celle interrompue.

        `apply_model=False` : laisse le sélecteur de modèle tel qu'il est
        déjà (au lieu de le remettre sur celui du job d'origine) -- sert à
        « Reprendre avec un autre moteur » (voir `_offer_resume_pending_jobs`) :
        rien n'empêche techniquement de reprendre un job interrompu avec un
        moteur différent (les segments déjà traduits ne sont jamais
        retouchés, seuls les suivants passeront par le nouveau moteur -- vu
        que `core/pipeline.py::run_job` lit `job.model_key`, jamais
        `job_state.model`, pour décider quel moteur charger). La langue
        source/cible, elle, reste toujours reprise telle quelle : la
        changer casserait la cohérence du document (mélange de langues
        cible dans un même fichier), donc pas exposé comme un choix ici.
        """
        self._set_source(Path(snapshot["input_path"]))
        out_path = Path(snapshot["output_path"])
        self.output_dir = out_path.parent
        self.output_edit.setText(str(self.output_dir))
        self._update_output_preview()

        src_idx = self.src_combo.findData(snapshot.get("src_lang"))
        if src_idx >= 0:
            self.src_combo.setCurrentIndex(src_idx)
        tgt_idx = self.tgt_combo.findData(snapshot.get("tgt_lang"))
        if tgt_idx >= 0:
            self.tgt_combo.setCurrentIndex(tgt_idx)
        if apply_model:
            self._set_model_combo(snapshot.get("model_key"))
        self.cleanup_check.setChecked(bool(snapshot.get("cleanup", False)))
        format_idx = self.output_format_combo.findData(snapshot.get("output_format", "md"))
        if format_idx >= 0:
            self.output_format_combo.setCurrentIndex(format_idx)

    def _offer_resume_pending_jobs(self) -> None:
        """
        Proposition automatique au démarrage (voir __init__) de reprendre
        UNE des traductions interrompues -- TOUTES celles encore en
        attente, pas seulement la dernière (demande explicite de
        l'utilisateur, 26/08/2026) : reproposée à CHAQUE démarrage tant
        qu'il en reste au moins une, jusqu'à ce que chacune soit reprise
        jusqu'au bout ou abandonnée explicitement (voir ResumeJobsDialog).

        Chaque job mémorisé qui n'est plus réellement reprenable (terminé
        entre-temps, fichier source introuvable, état corrompu…) est
        silencieusement retiré de la liste, sans déranger l'utilisateur
        pour rien -- ne montre un job que si `state.can_resume` le
        confirme réellement.
        """
        resumable: list[tuple[dict, state_mod.JobState, str]] = []
        for snapshot in settings_mod.get_pending_jobs():
            input_path = Path(snapshot.get("input_path", ""))
            stored_out_path = snapshot.get("output_path")
            if not input_path.exists() or not stored_out_path:
                if stored_out_path:
                    settings_mod.remove_pending_job(stored_out_path)
                continue

            # Redirige vers le vrai fichier si ce job avait déjà été
            # renommé selon son titre traduit (voir core/state.py) --
            # même logique que celle utilisée par pipeline.run_job.
            real_out_path = state_mod.resolve_output_path(Path(stored_out_path), input_path)
            job_state = state_mod.can_resume(real_out_path, input_path)
            if job_state is None:
                settings_mod.remove_pending_job(stored_out_path)
                continue

            original_model_key = snapshot.get("model_key")
            original_label = translate.MODEL_INFO[original_model_key].label \
                if original_model_key in translate.MODEL_INFO else (original_model_key or "?")
            resumable.append((snapshot, job_state, original_label))

        if not resumable:
            return

        dialog = ResumeJobsDialog(resumable, parent=self)
        dialog.exec()
        if dialog.result_action is None:
            return  # « Plus tard » ou fermeture : reproposé au prochain lancement
        kind, snapshot, job_state, original_label = dialog.result_action

        if kind == "resume_other_engine":
            # Ne PAS reprendre le moteur d'origine (voir apply_model=False)
            # et ne PAS démarrer tout de suite : l'utilisateur doit d'abord
            # choisir dans le sélecteur existant, puis cliquer Traduire
            # lui-même -- ce second clic retombe sur `_resolve_conflict`,
            # qui redétecte ce même job interrompu et propose sa propre
            # reprise, cette fois avec le moteur alors sélectionné (voir
            # `core/pipeline.py::run_job`, qui lit `job.model_key` -- jamais
            # `job_state.model` -- pour choisir quel moteur charger).
            self._apply_job_snapshot(snapshot, apply_model=False)
            self._append_log(
                f"Traduction reprise jusqu'à {job_state.done}/{job_state.total} segments "
                f"(moteur d'origine : « {original_label} »). Choisissez un moteur dans le "
                "sélecteur ci-dessus, puis cliquez sur Traduire pour continuer avec."
            )
            return

        self._apply_job_snapshot(snapshot)
        self._start_impl(force_resume=True)

    def _confirm_model_download(self, model_key: str, src_lang: str | None = None, tgt_lang: str | None = None) -> bool:
        """
        Avertit avant de lancer le téléchargement (moteur « precise »), la
        conversion locale (moteur « fast », voir FastEngine) ou la
        résolution d'un modèle OPUS-MT pas encore prêt. Retourne False si
        l'utilisateur annule.

        Un modèle déjà prêt (cas du 600M par défaut) ne déclenche aucune
        boîte de dialogue — l'avertissement ne sert que pour une vraie
        attente de plusieurs Go téléchargés ou de plusieurs minutes de
        conversion. `src_lang`/`tgt_lang` ne servent qu'à OPUS-MT (voir
        translate.OpusMtEngine) : les autres moteurs les ignorent.
        """
        info = translate.MODEL_INFO[model_key]
        if translate.is_model_ready(model_key, src_lang, tgt_lang):
            return True

        if info.engine == "opus-mt" and translate.opus_mt_repo_id(src_lang, tgt_lang) is None:
            # Langue absente de la table ISO 639-1 (voir core/languages.py) :
            # inutile de proposer un téléchargement qui échouera à coup sûr,
            # message direct plutôt qu'une confirmation trompeuse.
            QMessageBox.warning(
                self, "Paire non prise en charge",
                f"OPUS-MT ne connaît pas la correspondance ISO 639-1 pour {src_lang} ou {tgt_lang}. "
                "Choisissez un autre moteur pour cette paire de langues.",
            )
            return False

        marque = " (estimation)" if info.size_is_estimate else ""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("Conversion du modèle" if info.engine == "fast" else "Téléchargement du modèle")
        box.setText(f"Le modèle « {info.label} » n'est pas encore prêt sur cette machine.")
        if info.engine == "opus-mt":
            # Contrairement aux autres moteurs, "Télécharger" ici est une
            # TENTATIVE, pas une garantie : Helsinki-NLP ne publie pas
            # toutes les paires (voir OpusMtEngine.load()). Message honnête
            # plutôt que de promettre un résultat qu'on ne peut pas garantir
            # avant d'avoir vraiment essayé.
            box.setInformativeText(
                f"Il devra être téléchargé pour la paire {src_lang} -> {tgt_lang} — estimé "
                f"~{info.size_gb:.1f} Go{marque}, variable selon la paire choisie. Helsinki-NLP "
                "ne publie pas un modèle pour absolument toutes les combinaisons : si celle-ci "
                "n'existe pas, un message clair l'indiquera au lancement plutôt que d'échouer "
                "silencieusement.\n\nContinuer ?"
            )
            action_btn = box.addButton("Télécharger", QMessageBox.AcceptRole)
        elif info.engine == "fast":
            # Conversion locale (CPU, une seule fois) -- mesuré : ~77 s pour
            # le 600M sur cette machine, poids d'origine déjà en cache. Si
            # les poids d'origine ne sont pas non plus en cache, la
            # conversion les télécharge d'abord elle-même (comportement de
            # TransformersConverter, pas quelque chose que TRANSLAX gère
            # séparément) -- d'où la formulation qui couvre les deux cas.
            box.setInformativeText(
                f"Il devra être converti au format CTranslate2 — une opération locale d'environ "
                f"{info.size_gb:.1f} Go{marque} sur le disque, qui prend quelques minutes la "
                "première fois (et télécharge d'abord les poids d'origine si besoin). Cette "
                "conversion ne se fait qu'une fois : elle sera conservée pour les prochaines "
                "traductions.\n\nContinuer ?"
            )
            action_btn = box.addButton("Convertir", QMessageBox.AcceptRole)
        else:
            box.setInformativeText(
                f"Il devra être téléchargé — environ {info.size_gb:.1f} Go{marque} — avant de "
                "pouvoir traduire. Le téléchargement ne se fait qu'une fois : il sera conservé "
                "pour les prochaines traductions.\n\nContinuer ?"
            )
            action_btn = box.addButton("Télécharger", QMessageBox.AcceptRole)
        box.addButton("Annuler", QMessageBox.RejectRole)
        box.exec()
        return box.clickedButton() is action_btn

    @Slot(object)
    def _on_cleanup_review_needed(self, report: page_cleanup.PageCleanupReport) -> None:
        """
        Répond à `TranslationWorker.cleanup_review_needed`, émis depuis le
        thread de travail lorsque `core/page_cleanup.py` a détecté des
        en-têtes/pieds de page répétés ou des numéros de page. Cette
        méthode tourne sur le thread PRINCIPAL (Qt met en file d'attente
        les signaux inter-threads) : c'est ici, et seulement ici, qu'une
        vraie boîte de dialogue peut s'afficher.

        Le thread de travail reste bloqué (`threading.Event`, voir
        `ui/worker.py`) jusqu'à ce que `set_cleanup_decision` soit appelée
        ci-dessous — indispensable pour ne jamais toucher au fichier avant
        que l'utilisateur ait tranché.
        """
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle("Nettoyage des pages détecté")
        box.setText(
            f"{report.lines_removed} ligne(s) d'en-tête/pied de page ou de numérotation "
            f"détectées sur {report.total_pages} pages."
        )
        box.setInformativeText("\n".join(report.summary_lines()))
        clean_btn = box.addButton("Utiliser la version nettoyée", QMessageBox.AcceptRole)
        original_btn = box.addButton("Utiliser l'original", QMessageBox.DestructiveRole)
        box.addButton("Annuler", QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is clean_btn:
            decision = "clean"
        elif clicked is original_btn:
            decision = "original"
        else:
            decision = "cancel"
        if self.worker is not None:
            self.worker.set_cleanup_decision(decision)

    @Slot(object)
    def _on_vision_review_needed(self, report: vision_ocr.VisionOcrReport) -> None:
        """
        Répond à `TranslationWorker.vision_review_needed` (Traduire X) --
        même principe que `_on_cleanup_review_needed` ci-dessus : tourne sur
        le thread principal, le thread de travail reste bloqué jusqu'à
        `set_vision_decision`. Demande explicite de l'utilisateur : tout
        voir d'un coup, scrollable, avant de continuer vers la traduction.
        """
        dialog = VisionReviewDialog(report, self)
        decision = "continue" if dialog.exec() == QDialog.Accepted else "cancel"
        if self.worker is not None:
            self.worker.set_vision_decision(decision)

    @Slot(int, int, object)
    def _on_vision_progress(self, done: int, total: int, report: vision_ocr.VisionOcrReport) -> None:
        """Progression de l'extraction vision (Traduire X), AVANT que la
        traduction des segments ne commence -- réutilise la même barre,
        par pages plutôt que par segments le temps de cette étape."""
        self.progress_bar.setRange(0, total)
        self._animate_progress_to(done)
        self.stats_label.setText(
            f"Extraction vision : page {done}/{total}   ·   "
            f"{report.flagged_count} signalée(s) incertaine(s)   ·   "
            f"~{report.estimated_cost_usd():.2f} $ estimé"
        )

    def _resolve_conflict(self, out_path: Path) -> str | None:
        """
        Décide quoi faire si le fichier de sortie existe déjà.

        Retourne le mode de reprise à passer au job ("auto" ou "restart"),
        ou None si l'utilisateur annule.
        """
        if not out_path.exists():
            return "auto"

        resumable = state_mod.can_resume(out_path, self.source_path)
        if resumable is not None:
            box = QMessageBox(self)
            box.setWindowTitle("Traduction interrompue")
            box.setText(
                f"Une traduction de ce document a été interrompue à "
                f"{resumable.done} / {resumable.total} segments."
            )
            box.setInformativeText("Reprendre là où elle s'est arrêtée, ou tout recommencer ?")
            resume_btn = box.addButton("Reprendre", QMessageBox.AcceptRole)
            restart_btn = box.addButton("Recommencer", QMessageBox.DestructiveRole)
            box.addButton("Annuler", QMessageBox.RejectRole)
            box.exec()
            clicked = box.clickedButton()
            if clicked is resume_btn:
                return "auto"
            if clicked is restart_btn:
                return "restart"
            return None

        existing = state_mod.load_state(out_path)
        same_source = existing is not None and existing.source_hash == state_mod.source_hash(self.source_path)
        if existing is not None and existing.finished and same_source:
            box = QMessageBox(self)
            box.setWindowTitle("Déjà traduit")
            box.setText(f"« {out_path.name} » a déjà été traduit entièrement ({existing.total} segments).")
            redo_btn = box.addButton("Refaire", QMessageBox.DestructiveRole)
            open_btn = box.addButton("Ouvrir le fichier", QMessageBox.AcceptRole)
            box.addButton("Annuler", QMessageBox.RejectRole)
            box.exec()
            clicked = box.clickedButton()
            if clicked is redo_btn:
                return "restart"
            if clicked is open_btn:
                self.result_path = out_path
                self.open_file_button.setEnabled(True)
                self.open_folder_button.setEnabled(True)
                self._open(out_path)
            return None

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Le fichier existe déjà")
        box.setText(f"« {out_path.name} » existe déjà dans le dossier de sortie et provient d'un autre document.")
        box.setInformativeText("L'écraser ?")
        overwrite_btn = box.addButton("Écraser", QMessageBox.DestructiveRole)
        box.addButton("Annuler", QMessageBox.RejectRole)
        box.exec()
        return "restart" if box.clickedButton() is overwrite_btn else None

    def _pause(self) -> None:
        """
        Interrompt après le segment en cours SANS rien détruire (demande
        explicite de l'utilisateur, 26/08/2026) : tout ce qu'il faut pour
        reprendre plus tard reste sur le disque (voir _on_finished) --
        c'est le comportement historique du bouton Stop, désormais séparé
        du vrai Stop (abandon définitif, voir _stop ci-dessous). Aucune
        confirmation nécessaire : rien n'est perdu.
        """
        if self.worker is not None:
            self.worker.request_stop()
            self.pause_button.setEnabled(False)
            self.stop_button.setEnabled(False)
            self._append_log("Pause demandée — fin du segment en cours…")

    def _stop(self) -> None:
        """
        Interrompt ET abandonne définitivement ce job (demande explicite de
        l'utilisateur, 26/08/2026) -- contrairement à Pause, plus rien à
        reprendre au prochain lancement (voir core/state.py::abandon,
        appelé depuis _on_finished une fois le thread réellement arrêté).
        Confirmation demandée : destructif, pas d'annulation possible
        ensuite.
        """
        if self.worker is None:
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Arrêter et abandonner")
        box.setText("Arrêter cette traduction et l'abandonner définitivement ?")
        box.setInformativeText(
            "Le texte déjà traduit reste sur le disque, mais cette traduction ne sera plus "
            "proposée à la reprise -- utilisez Pause pour l'interrompre sans l'abandonner."
        )
        box.addButton("Annuler", QMessageBox.RejectRole)
        confirm_btn = box.addButton("Abandonner", QMessageBox.DestructiveRole)
        box.exec()
        if box.clickedButton() is not confirm_btn:
            return
        self._abandon_requested = True
        self.worker.request_stop()
        self.pause_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self._append_log("Arrêt demandé — abandon définitif de cette traduction…")

    def _reboost_message(self, elapsed: float) -> str:
        """
        Formule le verdict pour le temps écoulé depuis le dernier mot généré.
        Purement indicatif -- ne déclenche jamais aucune action sur la
        traduction elle-même, quel que soit le verdict.
        """
        if elapsed < 60:
            return f"actif — dernier mot généré il y a {int(elapsed)} s."
        minutes = int(elapsed // 60)
        if elapsed < HEARTBEAT_AUTO_THRESHOLD_S:
            return (
                f"actif — ce segment prend plus de temps que d'habitude "
                f"({minutes} min sans nouveau mot ; normal pour un segment "
                "long ou un modèle plus lourd)."
            )
        return (
            f"aucune activité détectée depuis {minutes} min — le logiciel "
            "semble bloqué. Vous pouvez patienter encore, ou fermer "
            "l'application et la relancer : la reprise repartira là où la "
            "traduction s'est arrêtée."
        )

    def _reboost(self, automatic: bool) -> None:
        """
        Lit `worker.heartbeat` et affiche le verdict dans le journal -- ne
        touche à rien d'autre. Appelée par un clic sur le bouton Reboost, ou
        automatiquement par `_check_heartbeat` après 15 minutes pile sans
        activité (voir HEARTBEAT_AUTO_THRESHOLD_S).
        """
        if self.worker is None:
            self._append_log("Reboost : aucune traduction en cours.")
            return
        elapsed = self.worker.heartbeat.seconds_since_beat()
        prefix = "Vérification automatique (15 min sans activité apparente)" if automatic else "Reboost"
        self._append_log(f"{prefix} : {self._reboost_message(elapsed)}")

    @Slot()
    def _check_heartbeat(self) -> None:
        """
        Tourne en arrière-plan (QTimer) pendant toute traduction -- voir
        _set_running. Ne fait rien tant que 15 minutes ne sont pas écoulées
        depuis le dernier mot généré ; au-delà, déclenche `_reboost` UNE
        SEULE fois par blocage (re-armé dès que l'activité reprend), pour ne
        pas spammer le journal toutes les 10 secondes si ça reste bloqué.
        """
        if self.worker is None:
            return
        elapsed = self.worker.heartbeat.seconds_since_beat()
        if elapsed >= HEARTBEAT_AUTO_THRESHOLD_S:
            if not self._auto_reboost_done:
                self._auto_reboost_done = True
                self._reboost(automatic=True)
        else:
            self._auto_reboost_done = False

    # -------------------------------------------------------------- signaux
    @Slot(str)
    def _on_status(self, message: str) -> None:
        self._append_log(message)
        self.stats_label.setText(message)

    def _animate_progress_to(self, value: int) -> None:
        """Glisse la barre vers `value` plutôt que d'y sauter directement —
        un segment de plus se voit avancer, pas apparaître d'un coup."""
        self._progress_animation.stop()
        self._progress_animation.setStartValue(self.progress_bar.value())
        self._progress_animation.setEndValue(value)
        self._progress_animation.start()

    @Slot(object)
    def _on_progress(self, p: translate.Progress) -> None:
        if self.progress_bar.maximum() != p.total:
            self.progress_bar.setRange(0, p.total)
            self.progress_bar.setValue(p.done)  # premier segment : pas d'animation depuis une valeur factice
        else:
            self._animate_progress_to(p.done)
        self.stats_label.setText(
            f"{p.percent:.1f} %   ·   {p.done} / {p.total} segments   ·   "
            f"{p.rate:.0f} s/segment   ·   reste ≈ {format_duration(p.eta)}"
        )
        snippet = " ".join(p.translated_text.split())[:70]
        self._append_log(f"[{p.done}/{p.total}] {len(p.source_text.split())} mots → {snippet}…")

    @Slot(object)
    def _on_finished(self, result: pipeline.Result) -> None:
        # Le PDF (s'il a été demandé et généré) devient le fichier "principal"
        # pour Ouvrir/Ouvrir le dossier -- c'est ce que l'utilisateur a
        # explicitement demandé comme format de sortie ; le .md, toujours
        # écrit lui aussi, reste accessible par le dossier ouvert.
        self.result_path = result.pdf_path or result.output_path
        self.progress_bar.setRange(0, max(1, result.total_segments))
        self.progress_bar.setValue(result.translated_segments)
        if result.cancelled:
            for note in result.notes:
                self._append_log(note)
            if self._abandon_requested:
                # Stop (rouge) : contrairement à Pause, efface aussi l'état
                # de reprise -- ce job ne sera plus jamais proposé (voir
                # core/state.py::abandon). Le fichier de sortie partiel,
                # lui, n'est jamais touché.
                state_mod.abandon(result.output_path, self.source_path)
                if self._current_job_output_key is not None:
                    settings_mod.remove_pending_job(self._current_job_output_key)
                self._abandon_requested = False
                self.stats_label.setText("Traduction abandonnée.")
            else:
                self.stats_label.setText(
                    f"Interrompu (Pause) à {result.translated_segments} / {result.total_segments} — "
                    "relancer reprendra ici."
                )
        else:
            if result.cleanup_report is not None:
                self._append_log(result.cleanup_report.summary())
            for note in result.notes:  # ex. un échec d'export PDF, non bloquant
                self._append_log(note)
            self._append_log(f"Terminé : {result.output_path}")
            if result.pdf_path is not None:
                self._append_log(f"Export PDF : {result.pdf_path}")
            verbe = "extraits" if self.extract_only_check.isChecked() else "traduits"
            self.stats_label.setText(
                f"Terminé — {result.translated_segments} segments {verbe} dans {self.result_path.name}"
            )
            # Terminé pour de bon (pas interrompu) : plus rien à proposer de
            # reprendre au prochain lancement.
            if self._current_job_output_key is not None:
                settings_mod.remove_pending_job(self._current_job_output_key)
        self.open_file_button.setEnabled(result.output_path.exists())
        self.open_folder_button.setEnabled(result.output_path.exists())

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self._append_log(f"ERREUR : {message}")
        self.stats_label.setText("Échec de la traduction.")
        QMessageBox.critical(self, "Erreur", message)

    @Slot()
    def _on_thread_finished(self) -> None:
        self._set_running(False)
        if self.thread is not None:
            self.thread.deleteLater()
        self.thread = None
        self.worker = None

    # --------------------------------------------------------------- divers
    def _append_log(self, message: str) -> None:
        self.log.appendPlainText(message)
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def _open(self, path: Path | None) -> None:
        if path is None or not Path(path).exists():
            QMessageBox.information(self, "Rien à ouvrir", "Le fichier n'existe pas (encore).")
            return
        open_in_explorer(path)

    def _open_output_location(self) -> None:
        """Emplacement du fichier .md de sortie, sélectionné dans l'explorateur."""
        if self.result_path is None or not self.result_path.exists():
            QMessageBox.information(self, "Rien à ouvrir", "Le fichier de sortie n'existe pas (encore).")
            return
        reveal_in_explorer(self.result_path)

    def _set_running(self, running: bool) -> None:
        for widget in (
            self.browse_button,
            self.src_combo,
            self.tgt_combo,
            self.model_combo,
            self.output_button,
            self.output_reset_button,
            self.cleanup_check,
            self.extract_only_check,
            self.translate_button,
            self.translate_x_button,
            self.api_key_edit,
            # Format de sortie et modèle OCR : verrouillés pendant qu'une
            # traduction tourne (demande explicite de l'utilisateur,
            # 26/08/2026) -- les changer en cours de route n'aurait aucun
            # effet sur le job déjà lancé, autant l'interdire plutôt que de
            # laisser croire que ça changerait quelque chose.
            self.output_format_combo,
            self.vision_model_combo,
        ):
            widget.setEnabled(not running)
        self.pause_button.setEnabled(running)
        self.stop_button.setEnabled(running)
        self.reboost_button.setEnabled(running)
        self.setAcceptDrops(not running)
        if not running:
            self.progress_bar.setRange(0, self.progress_bar.maximum() or 100)
            # `tgt_combo`/`model_combo` viennent d'être réactivés ci-dessus
            # sans condition -- si l'extraction seule est cochée, ils
            # doivent rester désactivés (aucun des deux n'a de sens dans ce
            # mode) : ré-applique cette règle par-dessus le déverrouillage
            # général plutôt que de la dupliquer ici.
            self._on_extract_only_toggled(self.extract_only_check.isChecked())

        if running:
            self._auto_reboost_done = False
            self._heartbeat_timer.start()
        else:
            self._heartbeat_timer.stop()

        # Actif UNIQUEMENT pendant qu'une traduction tourne réellement (pas
        # tant que la fenêtre est ouverte, pas sur une durée choisie à
        # l'avance) : une traduction peut durer des heures, et si le
        # système se met en veille pendant ce temps, le processus est
        # suspendu avec lui -- pas juste l'écran qui s'éteint.
        if running:
            self._keep_awake.start()
        else:
            self._keep_awake.stop()
        self.keep_awake_label.setVisible(running)

    def closeEvent(self, event) -> None:  # noqa: N802 - API Qt
        if self.thread is not None and self.thread.isRunning():
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Question)
            box.setWindowTitle("Traduction en cours")
            box.setText("Une traduction est en cours. L'interrompre et quitter ?")
            box.setInformativeText(
                "Le travail déjà fait est conservé : relancer reprendra où ça s'est arrêté."
            )
            quit_btn = box.addButton("Quitter", QMessageBox.DestructiveRole)
            box.addButton("Annuler", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is not quit_btn:
                event.ignore()
                return
            if self.worker is not None:
                self.worker.request_stop()
            self.thread.quit()
            self.thread.wait(60000)
            # Sur Mac, le processus `caffeinate` ne mourrait pas tout seul
            # avec la fenêtre -- il resterait à empêcher la veille pour
            # rien après la fermeture forcée de l'appli.
            self._keep_awake.stop()
            self._heartbeat_timer.stop()
        event.accept()
