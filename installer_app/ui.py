"""
Fenêtres de l'installeur -- l'identité visuelle de TRANSLAX, pas celle de
Windows. Réutilise réellement `ui/titlebar.py` (barre de titre custom déjà
construite et éprouvée pour l'application principale), pas une copie.

Deux fenêtres, un seul « coffrage » commun (`_Shell`) : barre de titre,
taille fixe (un installeur ne se redimensionne pas -- convention standard,
y compris chez l'ancien installeur Inno Setup), empilement de pages
(`QStackedWidget`) pour avancer dans le fil sans ouvrir plusieurs fenêtres.

Toute la mécanique réelle (copie de fichiers, raccourcis, registre) vit
dans `operations.py` et tourne sur un `QThread` séparé : l'interface ne
gèle jamais pendant la copie du payload (~500 Mo, plusieurs secondes).
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtGui import QCursor, QGuiApplication, QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from installer_app import operations
from ui.titlebar import TITLE_BAR_HEIGHT, TitleBar

WINDOW_WIDTH = 560
WINDOW_HEIGHT = 460

ABOUT_TEXT = (
    "TRANSLAX traduit des documents volumineux (livres, PDF scannés...) "
    "directement sur cette machine, sans dépendre d'un service de "
    "traduction payant."
)


def _format_size(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("o", "Ko", "Mo", "Go"):
        if value < 1024 or unit == "Go":
            return f"{value:.0f} {unit}" if unit == "o" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} Go"


def _panel() -> QFrame:
    frame = QFrame()
    frame.setObjectName("panel")
    return frame


# ---------------------------------------------------------------------------
# Fenêtre commune : barre de titre + pages empilées, taille fixe
# ---------------------------------------------------------------------------


class _Shell(QWidget):
    def __init__(self, title: str) -> None:
        super().__init__()
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setWindowTitle(title)
        self.setFixedSize(WINDOW_WIDTH, WINDOW_HEIGHT)

        icon_path = operations.bundled_icon_path()
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)  # liseré 1px, purement cosmétique : pas de redimensionnement à saisir
        outer.setSpacing(0)

        self.title_bar = TitleBar(self, title)
        if icon_path.exists():
            self.title_bar.set_icon(QIcon(str(icon_path)).pixmap(18, 18))
        # Pas de bouton Agrandir : une fenêtre à taille fixe n'a rien à
        # agrandir -- masqué plutôt que retiré, pour ne pas toucher à la
        # mise en page interne de TitleBar (voir ui/titlebar.py).
        self.title_bar.maximize_button.setVisible(False)
        self.title_bar.minimize_clicked.connect(self.showMinimized)
        self.title_bar.close_clicked.connect(self.close)
        outer.addWidget(self.title_bar)

        self.pages = QStackedWidget()
        outer.addWidget(self.pages, 1)

        self._center_on_screen()

    def _center_on_screen(self) -> None:
        """
        Centre la fenêtre sur l'écran contenant le curseur.

        Sans appel explicite, Qt peut placer une fenêtre sans parent à une
        position hérité d'un état antérieur (bureau virtuel différent,
        moniteur débranché depuis) -- constaté réellement en testant cette
        fenêtre : elle s'ouvrait hors de l'écran visible (coordonnées Y
        négatives), invisible bien que réellement lancée. Se baser sur
        l'écran du CURSEUR plutôt que l'écran "primaire" gère aussi le cas
        d'une machine multi-écrans où le principal n'est pas celui utilisé
        à l'instant.
        """
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        x = available.x() + (available.width() - self.width()) // 2
        y = available.y() + (available.height() - self.height()) // 2
        self.move(x, y)

    def add_page(self, widget: QWidget) -> int:
        return self.pages.addWidget(widget)


def _header(title: str, subtitle: str) -> QVBoxLayout:
    layout = QVBoxLayout()
    layout.setSpacing(2)
    title_label = QLabel(title)
    title_label.setObjectName("wizardTitle")
    layout.addWidget(title_label)
    subtitle_label = QLabel(subtitle)
    subtitle_label.setObjectName("wizardSubtitle")
    layout.addWidget(subtitle_label)
    return layout


# ---------------------------------------------------------------------------
# SETUP : Bienvenue -> Progression -> Fin
# ---------------------------------------------------------------------------


class InstallWorker(QThread):
    """Copie le payload, crée les raccourcis, enregistre la
    désinstallation -- hors du thread d'interface."""

    progress = Signal(int, int)
    status = Signal(str)
    finished_ok = Signal(object)   # operations.Paths
    failed = Signal(str)

    def __init__(self, install_dir: Path, version: str, create_desktop_shortcut: bool) -> None:
        super().__init__()
        self._install_dir = install_dir
        self._version = version
        self._create_desktop_shortcut = create_desktop_shortcut
        self._stop_requested = False

    def request_stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:  # noqa: D102 - API QThread
        try:
            self.status.emit("Fermeture de TRANSLAX si nécessaire…")
            if not operations.close_running_translax():
                self.failed.emit(
                    "TRANSLAX est toujours en cours d'exécution et n'a pas pu être fermé "
                    "automatiquement -- ferme-le manuellement puis relance l'installation."
                )
                return

            paths = operations.paths_for(self._install_dir)

            self.status.emit("Copie des fichiers de l'application…")
            operations.copy_with_progress(
                operations.bundled_app_exe_path(), paths.exe_path,
                on_progress=lambda done, total: self.progress.emit(done, total),
                should_stop=lambda: self._stop_requested,
            )

            icon_src = operations.bundled_icon_path()
            if icon_src.exists():
                paths.icon_path.write_bytes(icon_src.read_bytes())

            uninstaller_src = operations.bundled_uninstaller_path()
            if uninstaller_src is not None:
                operations.copy_with_progress(uninstaller_src, paths.uninstall_exe_path)

            self.status.emit("Création des raccourcis…")
            if self._create_desktop_shortcut:
                operations.create_shortcut(
                    operations.desktop_shortcut_path(), paths.exe_path, paths.icon_path,
                    "Traduire des documents localement avec TRANSLAX",
                )
            operations.create_shortcut(
                operations.start_menu_shortcut_path(), paths.exe_path, paths.icon_path,
                "Traduire des documents localement avec TRANSLAX",
            )

            self.status.emit("Enregistrement de la désinstallation…")
            operations.register_uninstall_entry(
                self._version, self._install_dir, paths.icon_path, paths.uninstall_exe_path
            )
            operations.cleanup_legacy_inno_entry()

            self.status.emit("Terminé.")
            self.finished_ok.emit(paths)
        except operations.InstallError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 - jamais une trace brute affichée à l'utilisateur
            self.failed.emit(f"Erreur inattendue : {exc}")


class WelcomePage(QWidget):
    install_requested = Signal(Path, bool)
    cancel_requested = Signal()

    def __init__(self, version: str) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(16)

        root.addLayout(_header("Installer TRANSLAX", f"Version {version} — installation locale, sans compte."))

        body = QLabel(ABOUT_TEXT)
        body.setObjectName("wizardBody")
        body.setWordWrap(True)
        root.addWidget(body)

        panel = _panel()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(16, 14, 16, 14)
        panel_layout.setSpacing(10)

        location_label = QLabel("EMPLACEMENT D'INSTALLATION")
        location_label.setObjectName("sectionLabel")
        panel_layout.addWidget(location_label)

        location_row = QHBoxLayout()
        self.location_edit = QLineEdit(str(operations.default_install_dir()))
        self.location_edit.setReadOnly(True)
        location_row.addWidget(self.location_edit, 1)
        browse_button = QPushButton("Parcourir…")
        browse_button.setObjectName("browseButton")
        browse_button.clicked.connect(self._browse)
        location_row.addWidget(browse_button)
        panel_layout.addLayout(location_row)

        self.desktop_checkbox = QCheckBox("Créer un raccourci sur le Bureau")
        self.desktop_checkbox.setChecked(True)
        panel_layout.addWidget(self.desktop_checkbox)

        root.addWidget(panel)
        root.addStretch(1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel_button = QPushButton("Annuler")
        cancel_button.clicked.connect(self.cancel_requested)
        buttons.addWidget(cancel_button)
        install_button = QPushButton("Installer")
        install_button.setObjectName("primary")
        install_button.clicked.connect(self._on_install_clicked)
        buttons.addWidget(install_button)
        root.addLayout(buttons)

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Choisir le dossier d'installation", self.location_edit.text())
        if chosen:
            # TRANSLAX vit dans son propre sous-dossier, jamais directement
            # dans le dossier choisi -- sans ça, désinstaller supprimerait
            # tout le contenu de ce dossier, pas seulement TRANSLAX.
            self.location_edit.setText(str(Path(chosen) / operations.APP_NAME))

    def _on_install_clicked(self) -> None:
        self.install_requested.emit(Path(self.location_edit.text()), self.desktop_checkbox.isChecked())


class ProgressPage(QWidget):
    def __init__(self, title: str) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 60, 28, 24)
        root.setSpacing(14)
        root.addStretch(1)

        title_label = QLabel(title)
        title_label.setObjectName("wizardTitle")
        root.addWidget(title_label)

        self.status_label = QLabel("Préparation…")
        self.status_label.setObjectName("statusLabel")
        root.addWidget(self.status_label)

        from PySide6.QtWidgets import QProgressBar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # indéterminé tant qu'aucune taille réelle n'est connue
        self.progress_bar.setTextVisible(False)
        root.addWidget(self.progress_bar)

        root.addStretch(2)

    @Slot(str)
    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    @Slot(int, int)
    def set_progress(self, done: int, total: int) -> None:
        if total > 0:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(done)
            self.status_label.setText(f"Copie des fichiers de l'application… {_format_size(done)} / {_format_size(total)}")
        else:
            self.progress_bar.setRange(0, 0)


class FinishPage(QWidget):
    finish_requested = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 60, 28, 24)
        root.setSpacing(14)
        root.addStretch(1)

        title_label = QLabel("Installation terminée")
        title_label.setObjectName("wizardTitle")
        title_label.setStyleSheet("color: #7fdb8f;")  # succès -- même vert que pauseButton dans ui/styles.qss
        root.addWidget(title_label)

        body = QLabel("TRANSLAX est prêt à être utilisé.")
        body.setObjectName("wizardSubtitle")
        root.addWidget(body)

        root.addStretch(1)

        self.launch_checkbox = QCheckBox("Lancer TRANSLAX maintenant")
        self.launch_checkbox.setChecked(True)
        root.addWidget(self.launch_checkbox)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        finish_button = QPushButton("Terminer")
        finish_button.setObjectName("primary")
        finish_button.clicked.connect(lambda: self.finish_requested.emit(self.launch_checkbox.isChecked()))
        buttons.addWidget(finish_button)
        root.addLayout(buttons)


class ErrorPage(QWidget):
    close_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 60, 28, 24)
        root.setSpacing(14)
        root.addStretch(1)

        title_label = QLabel("L'installation a échoué")
        title_label.setObjectName("wizardTitle")
        title_label.setStyleSheet("color: #ec6a63;")
        root.addWidget(title_label)

        self.message_label = QLabel("")
        self.message_label.setObjectName("wizardBody")
        self.message_label.setWordWrap(True)
        root.addWidget(self.message_label)
        root.addStretch(1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close_button = QPushButton("Fermer")
        close_button.clicked.connect(self.close_requested)
        buttons.addWidget(close_button)
        root.addLayout(buttons)

    def set_message(self, message: str) -> None:
        self.message_label.setText(message)


class SetupWindow(_Shell):
    """
    Fenêtre d'installation complète.

    `silent=True` (mise à jour intégrée, voir `core/updater.py`) saute
    directement à la page de progression avec les réglages par défaut, et
    lance TRANSLAX automatiquement à la fin sans qu'il y ait quoi que ce
    soit à cliquer -- exactement le comportement déjà établi avec l'ancien
    installeur (`/SILENT` + `postinstall` sans `skipifsilent`).
    """

    PAGE_WELCOME = 0
    PAGE_PROGRESS = 1
    PAGE_FINISH = 2
    PAGE_ERROR = 3

    def __init__(self, silent: bool = False) -> None:
        super().__init__("Installation de TRANSLAX")
        self._silent = silent
        self._version = operations.bundled_version()
        self._worker: InstallWorker | None = None

        self.welcome_page = WelcomePage(self._version)
        self.welcome_page.install_requested.connect(self._start_install)
        self.welcome_page.cancel_requested.connect(self.close)
        self.add_page(self.welcome_page)

        self.progress_page = ProgressPage("Installation en cours…")
        self.add_page(self.progress_page)

        self.finish_page = FinishPage()
        self.finish_page.finish_requested.connect(self._on_finish)
        self.add_page(self.finish_page)

        self.error_page = ErrorPage()
        self.error_page.close_requested.connect(self.close)
        self.add_page(self.error_page)

        self._installed_paths: operations.Paths | None = None

        if silent:
            # Le bouton Réduire reste utile même en mode silencieux (une
            # mise à jour peut prendre quelques secondes) ; pas de bouton
            # Fermer actif tant que ce n'est pas terminé -- une fenêtre
            # fermée en plein milieu laisserait l'exe à moitié remplacé.
            self.title_bar.close_clicked.disconnect(self.close)
            self._start_install(operations.default_install_dir(), True)
        else:
            self.pages.setCurrentIndex(self.PAGE_WELCOME)

    def _start_install(self, install_dir: Path, create_desktop_shortcut: bool) -> None:
        self.pages.setCurrentIndex(self.PAGE_PROGRESS)
        self._worker = InstallWorker(install_dir, self._version, create_desktop_shortcut)
        self._worker.progress.connect(self.progress_page.set_progress)
        self._worker.status.connect(self.progress_page.set_status)
        self._worker.finished_ok.connect(self._on_install_finished)
        self._worker.failed.connect(self._on_install_failed)
        self._worker.start()

    @Slot(object)
    def _on_install_finished(self, paths: operations.Paths) -> None:
        self._installed_paths = paths
        if self._silent:
            operations.launch(paths.exe_path)
            self.close()
            return
        self.pages.setCurrentIndex(self.PAGE_FINISH)

    @Slot(str)
    def _on_install_failed(self, message: str) -> None:
        if self._silent:
            # Une mise à jour silencieuse qui échoue ne doit jamais rester
            # invisible : la fenêtre redevient interactive plutôt que de
            # disparaître sans explication.
            self.title_bar.close_clicked.connect(self.close)
        self.error_page.set_message(message)
        self.pages.setCurrentIndex(self.PAGE_ERROR)

    def _on_finish(self, launch_now: bool) -> None:
        if launch_now and self._installed_paths is not None:
            operations.launch(self._installed_paths.exe_path)
        self.close()


# ---------------------------------------------------------------------------
# DÉSINSTALLATION : Confirmation -> Progression -> Fin
# ---------------------------------------------------------------------------


class UninstallWorker(QThread):
    finished_ok = Signal()
    failed = Signal(str)
    status = Signal(str)

    def __init__(self, install_dir: Path) -> None:
        super().__init__()
        self._install_dir = install_dir

    def run(self) -> None:  # noqa: D102 - API QThread
        try:
            self.status.emit("Fermeture de TRANSLAX si nécessaire…")
            operations.close_running_translax()
            self.status.emit("Suppression des raccourcis…")
            operations.remove_shortcuts()
            self.status.emit("Retrait de l'entrée de désinstallation…")
            operations.unregister_uninstall_entry()
            self.status.emit("Suppression des fichiers…")
            operations.remove_directory_deferred(self._install_dir)
            self.finished_ok.emit()
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"Erreur inattendue : {exc}")


class ConfirmUninstallPage(QWidget):
    confirmed = Signal()
    cancelled = Signal()

    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 50, 28, 24)
        root.setSpacing(14)
        root.addStretch(1)

        root.addLayout(_header("Désinstaller TRANSLAX ?", "Cette action est irréversible."))

        body = QLabel(
            "TRANSLAX et ses fichiers seront supprimés de ce compte Windows. "
            "Les documents que tu as déjà traduits ne sont pas concernés -- "
            "ils sont enregistrés séparément, là où tu les as créés."
        )
        body.setObjectName("wizardBody")
        body.setWordWrap(True)
        root.addWidget(body)
        root.addStretch(1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel_button = QPushButton("Annuler")
        cancel_button.clicked.connect(self.cancelled)
        buttons.addWidget(cancel_button)
        confirm_button = QPushButton("Désinstaller")
        confirm_button.setObjectName("danger")
        confirm_button.clicked.connect(self.confirmed)
        buttons.addWidget(confirm_button)
        root.addLayout(buttons)


class UninstallDonePage(QWidget):
    close_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 60, 28, 24)
        root.setSpacing(14)
        root.addStretch(1)

        title_label = QLabel("TRANSLAX a été désinstallé")
        title_label.setObjectName("wizardTitle")
        root.addWidget(title_label)
        root.addStretch(1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close_button = QPushButton("Fermer")
        close_button.setObjectName("primary")
        close_button.clicked.connect(self.close_requested)
        buttons.addWidget(close_button)
        root.addLayout(buttons)


class UninstallWindow(_Shell):
    PAGE_CONFIRM = 0
    PAGE_PROGRESS = 1
    PAGE_DONE = 2
    PAGE_ERROR = 3

    def __init__(self, install_dir: Path, silent: bool = False) -> None:
        super().__init__("Désinstallation de TRANSLAX")
        self._install_dir = install_dir
        self._silent = silent

        self.confirm_page = ConfirmUninstallPage()
        self.confirm_page.confirmed.connect(self._start_uninstall)
        self.confirm_page.cancelled.connect(self.close)
        self.add_page(self.confirm_page)

        self.progress_page = ProgressPage("Désinstallation en cours…")
        self.add_page(self.progress_page)

        self.done_page = UninstallDonePage()
        self.done_page.close_requested.connect(self.close)
        self.add_page(self.done_page)

        self.error_page = ErrorPage()
        self.error_page.close_requested.connect(self.close)
        self.add_page(self.error_page)

        self._worker: UninstallWorker | None = None

        if silent:
            self._start_uninstall()
        else:
            self.pages.setCurrentIndex(self.PAGE_CONFIRM)

    def _start_uninstall(self) -> None:
        self.pages.setCurrentIndex(self.PAGE_PROGRESS)
        self._worker = UninstallWorker(self._install_dir)
        self._worker.status.connect(self.progress_page.set_status)
        self._worker.finished_ok.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    @Slot()
    def _on_done(self) -> None:
        if self._silent:
            self.close()
            return
        self.pages.setCurrentIndex(self.PAGE_DONE)

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        if self._silent:
            self.close()
            return
        self.error_page.set_message(message)
        self.pages.setCurrentIndex(self.PAGE_ERROR)
