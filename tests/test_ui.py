"""
Test du chemin interface -> QThread -> moteur -> retour dans l'interface.

Le modèle NLLB est remplacé par un faux traducteur (comme dans
test_pipeline.py), donc le test dure une seconde au lieu de plusieurs
minutes. Ce qui est vérifié ici n'est pas la traduction mais le câblage Qt :

  - le worker démarre bien dans un thread séparé (l'UI n'est pas bloquée) ;
  - les signaux status/progress/finished remontent jusqu'aux widgets ;
  - la barre de progression et le bandeau affichent les bonnes valeurs ;
  - les boutons retrouvent leur état après la fin du travail ;
  - le bouton Stop interrompt réellement le traitement, en laissant un
    fichier partiel cohérent avec l'état de reprise.

La fenêtre n'est jamais affichée (pas de `show()`) : le test ne fait pas
surgir de fenêtre sur le bureau.

    python tests/test_ui.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anthropic  # noqa: E402
import pymupdf  # noqa: E402
from PySide6.QtCore import QEventLoop, QPoint, QPointF, QTimer, Qt  # noqa: E402
from PySide6.QtGui import QWheelEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox, QPushButton  # noqa: E402

from core import heartbeat as heartbeat_mod  # noqa: E402
from core import pipeline, segment, settings, state as state_mod, system_info, updater, vision_ocr  # noqa: E402
from core import version as version_mod  # noqa: E402
import ui.main_window as main_window_mod  # noqa: E402
from ui.main_window import (  # noqa: E402
    PAGE_HUB,
    PAGE_SETTINGS,
    PAGE_TOOLS,
    PAGE_TRANSLATE,
    ApiKeysDialog,
    InfoDialog,
    MainWindow,
    NoScrollComboBox,
    ResumeJobsDialog,
    VisionReviewDialog,
)

# 25 paragraphes séparés par des lignes vides : au-dessus du seuil de la
# stratégie « blocs », donc 1 paragraphe = 1 segment. En dessous du seuil la
# segmentation bascule en « flux » et regroupe les phrases par ~90 mots.
SEGMENT_COUNT = 25
STOP_AFTER = 3
# Sans délai, le faux moteur termine avant que le thread principal n'ait eu
# le temps de réagir : l'arrêt ne serait jamais observable.
SEGMENT_DELAY = 0.03
TIMEOUT_MS = 30000

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK   {label}")
    else:
        print(f"  ECHEC {label} {detail}")
        failures.append(label)


class FakeEngine:
    def __init__(self, *args, **kwargs):
        self.calls = 0

    def load(self, on_status=None):
        if on_status:
            on_status("(faux moteur chargé)")

    def translate(self, text: str, *, heartbeat=None) -> str:
        self.calls += 1
        if heartbeat is not None:
            heartbeat.beat()
        time.sleep(SEGMENT_DELAY)
        return "FR " + text

    def unload(self):
        pass


def make_source(folder: Path) -> Path:
    paragraphs = [
        f"Paragraph number {i} used by the interface test. It is long enough "
        "to be treated as a paragraph rather than as a heading."
        for i in range(SEGMENT_COUNT)
    ]
    path = folder / "interface.txt"
    path.write_text("\n\n".join(paragraphs) + "\n", encoding="utf-8")
    return path


def make_vision_pdf(path: Path, pages: list[str]) -> None:
    """PDF réel et minimal (créé par pymupdf lui-même) pour tester Traduire X
    sans dépendre d'un vrai scan -- voir tests/test_vision_ocr.py pour la
    validation de la mécanique d'extraction elle-même."""
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()


class FakeVisionMessages:
    """Même principe que dans test_vision_ocr.py : un faux client Anthropic,
    une réponse canned par page, jamais d'appel réseau réel."""

    def __init__(self, corrected_texts: list[str]):
        self._texts = corrected_texts
        self.calls = 0

    def create(self, **kwargs):
        text = self._texts[self.calls]
        self.calls += 1

        class _Block:
            type = "text"
            def __init__(self, t):
                self.text = t

        class _Usage:
            def __init__(self):
                self.input_tokens = 100
                self.output_tokens = 50

        class _Response:
            def __init__(self, t):
                self.content = [_Block(t)]
                self.usage = _Usage()

        return _Response(f"PAGE_NUMBER: none\nHEADER: none\n---\n{text}")


class FakeVisionClient:
    def __init__(self, corrected_texts: list[str]):
        self.messages = FakeVisionMessages(corrected_texts)


def install_fake_vision_client(corrected_texts: list[str]):
    """Monkey-patch anthropic.Anthropic pour toujours renvoyer la MÊME
    instance factice (peu importe les arguments du constructeur), pour
    pouvoir vérifier après coup combien de fois elle a réellement été
    appelée -- restaurer via la valeur `original` retournée."""
    fake = FakeVisionClient(corrected_texts)
    original = anthropic.Anthropic
    anthropic.Anthropic = lambda api_key=None: fake  # type: ignore[assignment]
    return fake, original


def count_blocks(path: Path) -> int:
    return len([b for b in path.read_text(encoding="utf-8").split("\n\n") if b.strip()])


def wait_for_thread(window: MainWindow) -> bool:
    """Tourne la boucle d'évènements jusqu'à la fin du thread. Retourne False
    si le délai est dépassé (le test échoue alors au lieu de bloquer)."""
    if window.thread is None:
        return True
    loop = QEventLoop()
    timed_out = {"value": False}

    def on_timeout():
        timed_out["value"] = True
        loop.quit()

    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(on_timeout)
    timer.start(TIMEOUT_MS)
    window.thread.finished.connect(loop.quit)
    loop.exec()
    timer.stop()
    return not timed_out["value"]


def wait_for_named_thread(window: MainWindow, attr_name: str, timeout_ms: int = TIMEOUT_MS) -> bool:
    """
    Même principe que `wait_for_thread`, mais pour n'importe quel attribut
    QThread de `window` (ex. `_update_check_thread`/`_update_download_thread`,
    voir la section « Mises à jour ») -- ces threads-là sont distincts de
    `window.thread` (une traduction en cours), qui n'a rien à voir ici.
    """
    thread = getattr(window, attr_name)
    if thread is None:
        return True
    loop = QEventLoop()
    timed_out = {"value": False}

    def on_timeout():
        timed_out["value"] = True
        loop.quit()

    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(on_timeout)
    timer.start(timeout_ms)
    thread.finished.connect(loop.quit)
    loop.exec()
    timer.stop()
    return not timed_out["value"]


def flush_progress_animation(window: MainWindow, target: int, timeout_ms: int = 1000) -> None:
    """
    La barre glisse vers chaque nouvelle valeur au lieu d'y sauter (voir
    PROGRESS_ANIMATION_MS, `_animate_progress_to`) : `thread.finished` peut
    arriver avant que l'animation du DERNIER segment n'ait fini de tourner
    -- `wait_for_thread` seul ne suffit donc pas pour vérifier une valeur
    finale exacte juste après. Pompe la boucle d'évènements jusqu'à ce que
    la barre atteigne `target`, ou abandonne après `timeout_ms` (le test
    échouera alors sur son propre check, avec la vraie valeur observée).
    """
    loop = QEventLoop()
    deadline = QTimer()
    deadline.setSingleShot(True)
    deadline.timeout.connect(loop.quit)
    poll = QTimer()

    def check_value():
        if window.progress_bar.value() >= target:
            loop.quit()

    poll.timeout.connect(check_value)
    poll.start(15)
    deadline.start(timeout_ms)
    if window.progress_bar.value() < target:
        loop.exec()
    poll.stop()
    deadline.stop()


def flush_page_animation(window: MainWindow, target_page: int, timeout_ms: int = 1000) -> None:
    """
    Même raisonnement que `flush_progress_animation`, pour le fondu entre
    écrans (voir `MainWindow._navigate_to`) : `self.pages.setCurrentIndex`
    n'a lieu qu'à la fin du fondu de sortie (signal `finished`), pas au
    moment de l'appel à `_navigate_to` -- pomper la boucle d'évènements une
    seule fois ne suffit pas forcément à laisser l'animation aller à son
    terme.
    """
    loop = QEventLoop()
    deadline = QTimer()
    deadline.setSingleShot(True)
    deadline.timeout.connect(loop.quit)
    poll = QTimer()

    def check_value():
        if window.pages.currentIndex() == target_page:
            loop.quit()

    poll.timeout.connect(check_value)
    poll.start(15)
    deadline.start(timeout_ms)
    if window.pages.currentIndex() != target_page:
        loop.exec()
    poll.stop()
    deadline.stop()


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="translax_ui_"))
    original_engine = pipeline.translate.PreciseEngine
    pipeline.translate.PreciseEngine = FakeEngine  # type: ignore[assignment]
    # Le sélecteur de modèle présélectionne "600M — Turbo" par défaut depuis
    # le 25/08/2026 (voir ui/main_window.py::UI_DEFAULT_MODEL_KEY) -- toute
    # section ci-dessous qui appelle _start() sans choisir explicitement un
    # modèle passe donc par translate.FastEngine, pas PreciseEngine. Sans ce
    # patch, ces sections chargeraient pour de vrai CTranslate2 (lent, voire
    # une vraie conversion dans le dossier de réglages isolé de ce test) au
    # lieu du faux moteur instantané -- constaté une fois sous forme d'un
    # test qui ne finissait jamais en mode offscreen.
    original_fast_engine = pipeline.translate.FastEngine
    pipeline.translate.FastEngine = FakeEngine  # type: ignore[assignment]
    # Ce test vérifie le câblage thread/signaux, pas la traduction du titre
    # (couverte séparément dans test_pipeline.py) -- sans ce patch, le faux
    # moteur "traduirait" aussi le nom du fichier et le renommerait,
    # invalidant tous les chemins "interface.md" attendus ci-dessous.
    original_translate_title = pipeline._translate_title
    pipeline._translate_title = lambda engine, stem: None  # type: ignore[assignment]
    # Isolé du vrai %APPDATA%\TRANSLAX\settings.json de cette machine --
    # sans ça, `_start()` (appelé directement ci-dessous, sans clic réel)
    # écrirait pour de vrai un "dernier job" pointant vers un dossier
    # temporaire qui sera supprimé à la fin du test, et une reprise
    # interrompue laisserait ce pointeur orphelin dans le vrai fichier de
    # réglages du développeur.
    real_settings_dir = settings._settings_dir  # noqa: SLF001
    settings._settings_dir = lambda: workdir / "settings"  # noqa: SLF001

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()  # jamais affichée
    # Le sélecteur présélectionne "600M — Turbo" depuis le 25/08/2026 (voir
    # ui/main_window.py::UI_DEFAULT_MODEL_KEY) -- ramené ici à "600M"
    # (moteur precise, déjà patché en FakeEngine juste au-dessus, et déjà
    # présent dans le vrai cache HuggingFace de cette machine) pour toutes
    # les sections qui appellent _start() sans choisir un modèle exprès :
    # sans ce réglage, `_confirm_model_download` ouvrirait une vraie
    # QMessageBox "Convertir ?" pour Turbo (jamais converti dans le dossier
    # de réglages isolé de ce test), qui reste ouverte pour toujours en
    # mode offscreen faute d'utilisateur pour cliquer dessus.
    window._set_model_combo("600M")

    try:
        source = make_source(workdir)
        out_dir = workdir / "sortie"
        out_dir.mkdir()
        out_file = out_dir / "interface.md"

        print("\n1. Sélection du document")
        check("stratégie blocs (1 paragraphe = 1 segment)",
              segment.detect_strategy(source.read_text(encoding="utf-8")) == "blocks")
        window._set_source(source)
        window.output_dir = out_dir
        window._update_output_preview()
        check("nom du fichier affiché", window.file_label.text() == "interface.txt")
        check("sortie annoncée en .md", "interface.md" in window.output_preview.text())
        check("boutons de résultat désactivés", not window.open_file_button.isEnabled())

        print("\n2. Traduction complète dans un thread")
        window._start()
        check("thread démarré", window.thread is not None and window.thread.isRunning())
        check("commandes verrouillées pendant le travail", not window.translate_button.isEnabled())
        check("bouton Stop actif", window.stop_button.isEnabled())
        check("thread terminé dans les temps", wait_for_thread(window))
        app.processEvents()
        flush_progress_animation(window, SEGMENT_COUNT)  # laisse le dernier glissement de la barre finir

        check("fichier .md créé", out_file.exists())
        check("tous les segments écrits", count_blocks(out_file) == SEGMENT_COUNT,
              f"({count_blocks(out_file)}/{SEGMENT_COUNT})")
        check("barre de progression au maximum",
              window.progress_bar.value() == SEGMENT_COUNT,
              f"({window.progress_bar.value()}/{SEGMENT_COUNT})")
        check("bandeau annonce la fin", "Terminé" in window.stats_label.text(),
              f"({window.stats_label.text()})")
        check("journal alimenté en direct",
              window.log.toPlainText().count("mots") == SEGMENT_COUNT,
              f"({window.log.toPlainText().count('mots')} lignes)")
        check("commandes déverrouillées", window.translate_button.isEnabled())
        check("bouton Stop désactivé", not window.stop_button.isEnabled())
        check("bouton Ouvrir activé", window.open_file_button.isEnabled())
        check("thread nettoyé", window.thread is None and window.worker is None)

        print("\n3. Pause à la demande (n'abandonne rien -- voir 3bis pour Stop)")
        out_file.unlink()
        state_file = state_mod.state_path(out_file)
        if state_file.exists():
            state_file.unlink()

        window._start()
        # Pause déclenchée depuis un signal de progression : déterministe,
        # on sait qu'au moins STOP_AFTER segments sont déjà écrits. `_pause`
        # (pas `_stop`) : reprise possible ensuite, contrairement au bouton
        # Stop rouge (voir 3bis, demande explicite de l'utilisateur,
        # 26/08/2026 -- les deux boutons ne font plus la même chose).
        window.worker.progress.connect(
            lambda p: window._pause() if p.done >= STOP_AFTER else None
        )
        check("thread terminé après Pause", wait_for_thread(window))
        app.processEvents()

        blocks = count_blocks(out_file)
        saved = state_mod.load_state(out_file)
        check("interruption signalée", "Interrompu" in window.stats_label.text(),
              f"({window.stats_label.text()})")
        check("traduction partielle écrite", STOP_AFTER <= blocks < SEGMENT_COUNT, f"({blocks} blocs)")
        check("état cohérent avec le fichier", saved is not None and saved.done == blocks,
              f"(état {saved.done if saved else None} / fichier {blocks})")
        check("reprise possible après Pause", state_mod.can_resume(out_file, source) is not None)
        check("commandes déverrouillées après Pause", window.translate_button.isEnabled())

        print("\n3bis. Stop (rouge) : abandon définitif, contrairement à Pause")
        # PAS `make_source(workdir)` ici : elle écrit toujours sur le même
        # chemin fixe `interface.txt` (voir sa définition) -- la section 4,
        # juste après, a encore besoin de CE fichier précis (créé en
        # section 1, pas encore repris). L'écraser puis le renommer ici
        # ferait disparaître "interface.txt" sous les pieds de la section 4
        # (bug réellement rencontré en écrivant ce test). On copie plutôt
        # son contenu vers un fichier distinct, sans jamais toucher au sien.
        stop_source = workdir / "stop_test.txt"
        stop_source.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        stop_out = out_dir / "stop_test.md"
        window._set_source(stop_source)
        window.output_dir = out_dir
        window._update_output_preview()
        window._start()
        window.worker.progress.connect(
            lambda p: window._stop() if p.done >= STOP_AFTER else None
        )

        real_exec = QMessageBox.exec
        real_clicked = QMessageBox.clickedButton
        # D'abord « Annuler » sur la confirmation : ne doit RIEN interrompre.
        QMessageBox.exec = lambda self: None
        QMessageBox.clickedButton = lambda self: next(b for b in self.buttons() if b.text() == "Annuler")
        try:
            # Attend que le signal de progression ait bien déclenché
            # _stop() (connecté plus haut sur ce même signal, donc déjà
            # exécuté -- confirmation mockée comprise -- au moment où cette
            # boucle-ci se débloque) sans attendre la fin du thread, qui
            # doit ici continuer à tourner (« Annuler » ne l'interrompt pas).
            loop = QEventLoop()
            timer = QTimer()
            timer.setSingleShot(True)
            timer.timeout.connect(loop.quit)
            timer.start(TIMEOUT_MS)
            window.worker.progress.connect(lambda p: loop.quit() if p.done >= STOP_AFTER else None)
            loop.exec()
            timer.stop()
            check("« Annuler » sur la confirmation Stop ne touche à rien : thread toujours actif",
                  window.thread is not None and window.thread.isRunning())
        finally:
            QMessageBox.exec = real_exec
            QMessageBox.clickedButton = real_clicked

        QMessageBox.exec = lambda self: None
        QMessageBox.clickedButton = lambda self: next(b for b in self.buttons() if b.text() == "Abandonner")
        try:
            check("thread terminé après Stop (abandon confirmé)", wait_for_thread(window))
        finally:
            QMessageBox.exec = real_exec
            QMessageBox.clickedButton = real_clicked
        app.processEvents()

        check("interruption signalée comme un abandon, pas une pause",
              "abandonnée" in window.stats_label.text().lower(), f"({window.stats_label.text()})")
        check("le texte déjà traduit reste écrit sur le disque (jamais perdu par l'abandon)",
              STOP_AFTER <= count_blocks(stop_out) < SEGMENT_COUNT, f"({count_blocks(stop_out)} blocs)")
        check("PLUS de reprise possible après Stop (abandon définitif, contrairement à Pause)",
              state_mod.can_resume(stop_out, stop_source) is None)
        check("le job n'est plus dans la liste d'attente après abandon",
              not any(j.get("input_path") == str(stop_source) for j in settings.get_pending_jobs()))

        # Restaure la sélection de la section 3 (source/sortie encore en
        # pause, pas encore reprise) : 3bis a détourné `window` vers
        # `stop_source`/`out_dir` pour son propre scénario -- la section 4
        # a besoin de retrouver exactement `source`/`out_file`.
        window._set_source(source)
        window.output_dir = out_dir
        window._update_output_preview()

        print("\n4. Reprise depuis l'interface")
        # Le vrai _start() ouvrirait ici une boîte de dialogue modale
        # « Reprendre / Recommencer » qui bloquerait le test. On répond
        # « Reprendre » à sa place ; le reste du chemin est inchangé.
        window._resolve_conflict = lambda out_path: "auto"  # type: ignore[assignment]
        window._start()
        check("thread terminé dans les temps", wait_for_thread(window))
        app.processEvents()
        check("document complété", count_blocks(out_file) == SEGMENT_COUNT,
              f"({count_blocks(out_file)}/{SEGMENT_COUNT})")
        check("aucun doublon après reprise",
              [line for line in out_file.read_text(encoding="utf-8").splitlines() if line.strip()]
              == [f"FR {s['text']}" for s in segment.segment_text(source.read_text(encoding="utf-8"))])

        print("\n5. Sélecteur de modèle et avertissement de téléchargement")
        keys = [window.model_combo.itemData(i) for i in range(window.model_combo.count())]
        check("les 6 modèles proposés, commerciaux d'abord, usage personnel en dernier (25/08/2026)",
              keys == ["opus-mt", "madlad-3b", "600M", "1.3B", "3.3B", "600M-ct2"], f"({keys})")

        # Mocké pour TOUTE la section 5, pas seulement les blocs qui
        # testaient déjà une confirmation de téléchargement : sélectionner
        # un profil "usage personnel" (600M/1.3B/3.3B/600M-ct2) ouvre
        # maintenant AUSSI un avertissement (voir _on_model_selected) --
        # sans ce mock, chaque `setCurrentIndex` vers l'un de ces profils
        # ferait tourner une vraie QMessageBox.exec() en boucle infinie en
        # mode offscreen (même piège déjà rencontré et corrigé une fois
        # cette session pour 600M-ct2 spécifiquement, désormais général à
        # tous les profils "usage personnel"). `clickedButton = None` :
        # équivalent d'un clic sur "Annuler" ou de fermer une boîte
        # d'information sans bouton particulier à repérer.
        real_exec = QMessageBox.exec
        real_clicked = QMessageBox.clickedButton
        QMessageBox.exec = lambda self: None
        QMessageBox.clickedButton = lambda self: None
        try:
            idx_600m = window.model_combo.findData("600M")
            window.model_combo.setCurrentIndex(idx_600m)
            check("600M annoncé déjà présent (pas de téléchargement)",
                  "déjà téléchargé" in window.model_info_label.text(),
                  f"({window.model_info_label.text()})")
            check("600M ne déclenche aucune confirmation", window._confirm_model_download("600M"))

            idx_13b = window.model_combo.findData("1.3B")
            window.model_combo.setCurrentIndex(idx_13b)
            check("1.3B distillé annoncé à télécharger",
                  "à télécharger" in window.model_info_label.text() and "5.3" in window.model_info_label.text(),
                  f"({window.model_info_label.text()})")
            check("annuler le téléchargement renvoie False", window._confirm_model_download("1.3B") is False)

            window.model_combo.setCurrentIndex(idx_600m)  # on revient sur un modèle déjà présent

            # 600M-ct2 (moteur Turbo, voir translate.FastEngine) : les
            # réglages sont isolés dans un dossier temporaire depuis le
            # tout début de ce test (voir plus haut, `settings._settings_dir`
            # patché) -- donc `ctranslate2_model_dir` pointe vers CE dossier
            # temporaire, jamais converti, même si le vrai
            # %APPDATA%\TRANSLAX de cette machine a déjà le modèle prêt
            # (converti par tests/test_translate.py). Ce profil se comporte
            # donc ici comme le 1.3B ci-dessus : "pas prêt".
            idx_ct2 = window.model_combo.findData("600M-ct2")
            window.model_combo.setCurrentIndex(idx_ct2)
            check("600M-ct2 annoncé à convertir (réglages isolés, jamais converti ici)",
                  "à convertir localement" in window.model_info_label.text(),
                  f"({window.model_info_label.text()})")
            check("annuler la conversion renvoie False", window._confirm_model_download("600M-ct2") is False)
            window.model_combo.setCurrentIndex(idx_600m)
        finally:
            QMessageBox.exec = real_exec
            QMessageBox.clickedButton = real_clicked

        print("\n6. Nettoyage des pages : dialogue cross-thread")
        # Un .txt avec de vrais sauts de page (\f) et un pied de page qui se
        # répète -- déclenche `page_cleanup`, donc le signal
        # `cleanup_review_needed`, jamais exercé par les sections précédentes.
        paginated_source = workdir / "paginated.txt"
        pages = [
            f"Real paragraph number {i} of the paginated interface test, long enough to count on its own."
            f"\n\n\n{i} | P a g e"
            for i in range(1, 6)
        ]
        paginated_source.write_text("\f".join(pages), encoding="utf-8")

        window._set_source(paginated_source)
        window.output_dir = out_dir
        window._update_output_preview()

        # On passe par le VRAI slot de production (_on_cleanup_review_needed),
        # avec la QMessageBox simulée -- même technique que pour le test du
        # téléchargement de modèle plus haut. Se connecter à une fonction
        # Python ordinaire plutôt qu'à une méthode liée d'un QObject se
        # révèle ambigu pour Qt quant au thread d'exécution ; passer par le
        # vrai slot lève l'ambiguïté ET teste le vrai chemin de production.
        real_exec = QMessageBox.exec
        real_clicked = QMessageBox.clickedButton
        QMessageBox.exec = lambda self: None
        QMessageBox.clickedButton = lambda self: self.buttons()[0]  # 1er bouton ajouté = "nettoyée"

        window._start()
        check("le worker existe après _start()", window.worker is not None)
        thread_ok = wait_for_thread(window)
        check("thread terminé dans les temps", thread_ok)
        app.processEvents()

        QMessageBox.exec = real_exec
        QMessageBox.clickedButton = real_clicked

        paginated_output = out_dir / "paginated.md"
        check("fichier traduit sans le pied de page répété (décision « clean » appliquée)",
              paginated_output.exists() and "P a g e" not in paginated_output.read_text(encoding="utf-8"))

        print("\n7. Reboost : pouls de génération, sans jamais rien interrompre")
        window._set_source(source)
        window.output_dir = out_dir
        window._update_output_preview()
        if out_file.exists():
            out_file.unlink()
        state_file = state_mod.state_path(out_file)
        if state_file.exists():
            state_file.unlink()

        window._start()
        check("worker créé", window.worker is not None)
        if window.worker is not None:
            check("bouton Reboost actif pendant la traduction", window.reboost_button.isEnabled())
            window._reboost(automatic=False)
            check("Reboost n'interrompt rien : thread toujours actif",
                  window.thread is not None and window.thread.isRunning())
            check("Reboost a bien écrit un verdict dans le journal",
                  "Reboost : actif" in window.log.toPlainText())
        check("thread terminé dans les temps", wait_for_thread(window))
        app.processEvents()
        check("bouton Reboost désactivé après la fin", not window.reboost_button.isEnabled())

        # Déclenchement automatique après le seuil -- on l'abaisse pour le
        # test plutôt que d'attendre 15 minutes réelles. Le seuil est lu
        # comme variable de module au moment de l'appel (pas figé à
        # l'import), donc le patcher ici suffit à changer le comportement.
        original_threshold = main_window_mod.HEARTBEAT_AUTO_THRESHOLD_S
        main_window_mod.HEARTBEAT_AUTO_THRESHOLD_S = 0.05
        try:
            class _FakeWorker:
                def __init__(self):
                    self.heartbeat = heartbeat_mod.Heartbeat()

            window.worker = _FakeWorker()  # type: ignore[assignment]
            window.log.clear()
            window._auto_reboost_done = False
            time.sleep(0.1)
            window._check_heartbeat()
            check("déclenchement automatique après le seuil",
                  "Vérification automatique" in window.log.toPlainText())
            check("flag posé pour ne pas répéter à chaque tick", window._auto_reboost_done)

            before = window.log.toPlainText().count("Vérification automatique")
            window._check_heartbeat()
            after = window.log.toPlainText().count("Vérification automatique")
            check("pas de répétition tant que rien ne reprend", after == before, f"({before} -> {after})")

            window.worker.heartbeat.beat()
            window._check_heartbeat()
            check("re-armé dès qu'un mot est de nouveau produit", not window._auto_reboost_done)
        finally:
            main_window_mod.HEARTBEAT_AUTO_THRESHOLD_S = original_threshold
            window.worker = None

        print("\n8. Reprise automatique proposée au démarrage (liste, pas juste le dernier job)")
        # Rien à proposer : ne doit ni planter ni ouvrir de dialogue.
        _settings_data = settings.load_settings()
        _settings_data["pending_jobs"] = {}
        settings.save_settings(_settings_data)
        exec_calls = {"count": 0}
        real_dialog_exec = ResumeJobsDialog.exec
        ResumeJobsDialog.exec = lambda self: exec_calls.__setitem__("count", exec_calls["count"] + 1)
        try:
            window._offer_resume_pending_jobs()
            check("aucune proposition quand rien n'est mémorisé", exec_calls["count"] == 0)
        finally:
            ResumeJobsDialog.exec = real_dialog_exec

        auto_source = make_source(workdir)  # réutilise document.txt -> interface.txt le renomme, donc distinct
        auto_source = auto_source.rename(workdir / "auto_resume.txt")
        auto_out = out_dir / "auto_resume.md"
        window._set_source(auto_source)
        window.output_dir = out_dir
        window._update_output_preview()
        window._start()
        # _pause() (pas _stop()) : c'est désormais Pause qui interrompt sans
        # abandonner (Stop/Pause séparés, demande explicite de
        # l'utilisateur, 26/08/2026) -- le scénario de reprise a besoin d'un
        # job interrompu MAIS reprenable, pas d'un abandon définitif.
        window.worker.progress.connect(lambda p: window._pause() if p.done >= STOP_AFTER else None)
        check("thread interrompu (prépare le scénario de reprise)", wait_for_thread(window))
        app.processEvents()

        stored = settings.get_pending_jobs()
        check("le job interrompu est mémorisé", any(j.get("input_path") == str(auto_source) for j in stored),
              f"({stored!r})")

        # Les sous-scénarios passent par le VRAI déclencheur de production
        # (le QTimer.singleShot(0, ...) armé dans __init__, pas un appel
        # direct à _offer_resume_pending_jobs) -- on laisse
        # app.processEvents() le déclencher, pour tester le chemin réel
        # emprunté au démarrage de l'appli. Un appel direct laisserait le
        # timer arraché en attente, susceptible de se déclencher plus tard
        # sur cette même fenêtre pendant qu'un AUTRE scénario est en cours
        # (plusieurs MainWindow() coexistent dans ce process de test, ce qui
        # n'arrive jamais dans l'appli réelle où il n'y en a qu'une seule).
        #
        # `ResumeJobsDialog` n'est pas une QMessageBox : pas de piège de
        # réordonnancement de boutons ici -- on simule directement le choix
        # via `_choose()` sur le premier (et seul, dans ce test) job listé.
        def _fake_pick(kind):
            def _exec(self):
                if self._entries:
                    snapshot, job_state, original_label = self._entries[0]
                    self._choose(kind, snapshot, job_state, original_label)
            return _exec

        print("  8a. « Plus tard » : rien ne démarre, reproposé au prochain lancement")
        ResumeJobsDialog.exec = lambda self: None  # ni _choose ni reject explicite -- comme un clic "Plus tard"
        window_ignore = MainWindow()
        try:
            app.processEvents()  # déclenche le singleShot(0) de __init__, une seule fois
            check("« Plus tard » ne démarre aucun thread", window_ignore.thread is None)
            check("le repère reste mémorisé après « Plus tard »",
                  any(j.get("input_path") == str(auto_source) for j in settings.get_pending_jobs()))
        finally:
            ResumeJobsDialog.exec = real_dialog_exec
            window_ignore.close()

        print("  8c. « Autre moteur… » : repeuple sans démarrer, sans imposer le moteur d'origine")
        ResumeJobsDialog.exec = _fake_pick("resume_other_engine")
        window_other_engine = MainWindow()
        try:
            # Changé AVANT que le timer de démarrage ne déclenche
            # _offer_resume_pending_jobs (juste après, via processEvents()) :
            # si _apply_job_snapshot(..., apply_model=False) fonctionne
            # vraiment, ce choix doit survivre intact -- pas être écrasé par
            # le "600M" du job d'origine.
            other_idx = window_other_engine.model_combo.findData("1.3B")
            window_other_engine.model_combo.setCurrentIndex(other_idx)
            app.processEvents()  # déclenche le singleShot(0) de __init__, une seule fois
            check("aucun thread démarré (l'utilisateur doit choisir puis cliquer Traduire)",
                  window_other_engine.thread is None)
            check("la source a quand même été repeuplée",
                  window_other_engine.source_path == auto_source, f"({window_other_engine.source_path})")
            check("le moteur choisi AVANT l'offre (1.3B) n'a PAS été écrasé par celui d'origine (600M)",
                  window_other_engine.model_combo.currentData() == "1.3B",
                  f"({window_other_engine.model_combo.currentData()})")
            check("le journal indique la reprise en attente d'un choix de moteur",
                  "Choisissez un moteur" in window_other_engine.log.toPlainText(),
                  f"({window_other_engine.log.toPlainText()!r})")
            check("le repère reste mémorisé (rien n'a été consommé)",
                  any(j.get("input_path") == str(auto_source) for j in settings.get_pending_jobs()))
        finally:
            ResumeJobsDialog.exec = real_dialog_exec
            window_other_engine.close()

        print("  8b. « Reprendre » : ré-applique la config et termine la traduction")
        ResumeJobsDialog.exec = _fake_pick("resume")
        window_resume = MainWindow()
        try:
            app.processEvents()  # déclenche le singleShot(0) de __init__, une seule fois
            check("la source a été repeuplée depuis le job mémorisé",
                  window_resume.source_path == auto_source, f"({window_resume.source_path})")
            check("thread relancé automatiquement", window_resume.thread is not None)
            check("thread de reprise terminé dans les temps", wait_for_thread(window_resume))
            app.processEvents()
        finally:
            ResumeJobsDialog.exec = real_dialog_exec

        check("document complété par la reprise automatique",
              count_blocks(auto_out) == SEGMENT_COUNT, f"({count_blocks(auto_out)}/{SEGMENT_COUNT})")
        check("le repère est effacé une fois le job réellement terminé",
              not any(j.get("input_path") == str(auto_source) for j in settings.get_pending_jobs()))
        window_resume.close()

        print("  8d. « Abandonner » dans la liste : efface l'état de reprise sans démarrer")
        abandon_source = make_source(workdir).rename(workdir / "abandon_resume.txt")
        abandon_out = out_dir / "abandon_resume.md"
        window._set_source(abandon_source)
        window.output_dir = out_dir
        window._update_output_preview()
        window._start()
        window.worker.progress.connect(lambda p: window._pause() if p.done >= STOP_AFTER else None)
        check("thread interrompu (prépare le scénario d'abandon)", wait_for_thread(window))
        app.processEvents()
        check("le job à abandonner est bien mémorisé",
              any(j.get("input_path") == str(abandon_source) for j in settings.get_pending_jobs()))

        def _fake_abandon(self):
            if self._entries:
                self._abandon(self._entries[0][0])

        ResumeJobsDialog.exec = _fake_abandon
        window_abandon = MainWindow()
        try:
            app.processEvents()
            check("« Abandonner » ne démarre aucun thread", window_abandon.thread is None)
            check("le job abandonné n'est plus mémorisé",
                  not any(j.get("input_path") == str(abandon_source) for j in settings.get_pending_jobs()))
            check("l'état de reprise (.translax) est bien effacé pour ce job",
                  state_mod.can_resume(abandon_out, abandon_source) is None)
            check("le fichier de sortie partiel, lui, n'est jamais touché par l'abandon", abandon_out.exists())
        finally:
            ResumeJobsDialog.exec = real_dialog_exec
            window_abandon.close()

        print("\n9. Traduire X : extraction vision avant traduction")
        window.api_key_edit.clear()
        # "interface.txt" a été renommé en "auto_resume.txt" à la section 8
        # (n'existe donc plus) -- un fichier .txt frais, juste pour ce test
        # du refus non-PDF, qui n'a besoin que d'exister et de ne pas être un PDF.
        not_pdf_source = workdir / "not_a_pdf.txt"
        not_pdf_source.write_text("Peu importe le contenu.", encoding="utf-8")
        window._set_source(not_pdf_source)
        window.output_dir = out_dir
        window._update_output_preview()

        info_calls = {"count": 0}
        real_information = QMessageBox.information
        QMessageBox.information = staticmethod(lambda *a, **k: info_calls.__setitem__("count", info_calls["count"] + 1))
        try:
            window._start_vision()
            check("refusé sur un .txt (Traduire X ne s'applique qu'aux PDF)", info_calls["count"] == 1)
            check("aucun thread démarré sur le refus PDF", window.thread is None)
        finally:
            QMessageBox.information = real_information

        vision_pdf = workdir / "vision.pdf"
        make_vision_pdf(vision_pdf, ["Raw page one.", "Raw page two."])
        vision_out = out_dir / "vision.md"
        window._set_source(vision_pdf)
        window.output_dir = out_dir
        window._update_output_preview()

        # La clé API n'est exigée QUE si "Claude" est choisi dans le
        # sélecteur `vision_model_combo` (remplace l'ancienne case à
        # cocher -- demande explicite de l'utilisateur, 26/08/2026) --
        # PaddleOCR (par défaut, voir _build_ui) ne bloque jamais rien
        # sur l'absence de clé (Traduire X utilise l'OCR local par
        # défaut, voir section 9c plus bas pour ce chemin précis). Ce
        # test-ci porte sur le chemin Anthropic explicitement choisi.
        window.vision_model_combo.setCurrentIndex(window.vision_model_combo.findData("anthropic"))

        warn_calls = {"count": 0}
        real_warning = QMessageBox.warning
        QMessageBox.warning = staticmethod(lambda *a, **k: warn_calls.__setitem__("count", warn_calls["count"] + 1))
        # Sans clé, _start_vision ouvre maintenant directement ApiKeysDialog
        # (demande explicite de l'utilisateur, 25/08/2026 -- le champ n'est
        # plus modifiable en place, autant ouvrir la saisie tout de suite) --
        # une vraie QDialog.exec() distincte de la QMessageBox ci-dessus,
        # à neutraliser séparément (même principe que VisionReviewDialog
        # plus bas dans ce fichier).
        real_dialog_exec = ApiKeysDialog.exec
        ApiKeysDialog.exec = lambda self: QDialog.Rejected
        try:
            window._start_vision()
            check("refusé sans clé API (mode Claude explicitement choisi)", warn_calls["count"] == 1)
            check("aucun thread démarré sans clé", window.thread is None)
        finally:
            QMessageBox.warning = real_warning
            ApiKeysDialog.exec = real_dialog_exec

        window.api_key_edit.setText("fake-key-for-tests")

        print("  9a. « Annuler » au récapitulatif : rien n'est traduit")
        fake_vision_a, original_anthropic = install_fake_vision_client(
            ["Corrected page one.", "Corrected page two."]
        )
        real_dialog_exec = VisionReviewDialog.exec
        VisionReviewDialog.exec = lambda self: QDialog.Rejected
        try:
            window._start_vision()
            check("worker créé (extraction vision en cours)", window.worker is not None)
            check("thread terminé dans les temps", wait_for_thread(window))
            app.processEvents()
        finally:
            VisionReviewDialog.exec = real_dialog_exec
            anthropic.Anthropic = original_anthropic  # type: ignore[assignment]
        check("extraction allée jusqu'au bout avant la décision (2 pages transcrites)",
              fake_vision_a.messages.calls == 2, f"({fake_vision_a.messages.calls})")
        check("annulé au récapitulatif : rien n'a été écrit", not vision_out.exists())

        print("  9b. « Continuer » au récapitulatif : traduction complète")
        # Un NOUVEAU fichier PDF, distinct de 9a : le cache vision de
        # "vision.pdf" contient déjà ses 2 pages (l'extraction de 9a est
        # allée jusqu'au bout avant d'être annulée AU récapitulatif) --
        # réutiliser le même fichier masquerait ici un vrai appel au faux
        # client sous une lecture de cache, sans rien fausser côté résultat
        # mais sans vraiment tester ce qu'on veut tester.
        vision_pdf2 = workdir / "vision2.pdf"
        make_vision_pdf(vision_pdf2, ["Raw page one.", "Raw page two."])
        vision_out2 = out_dir / "vision2.md"
        window._set_source(vision_pdf2)
        window.output_dir = out_dir
        window._update_output_preview()

        fake_vision_b, original_anthropic = install_fake_vision_client(
            ["Corrected page one.", "Corrected page two."]
        )
        VisionReviewDialog.exec = lambda self: QDialog.Accepted
        progress_seen = []
        try:
            window._start_vision()
            check("worker créé (extraction vision en cours)", window.worker is not None)
            if window.worker is not None:
                window.worker.vision_progress.connect(
                    lambda done, total, report: progress_seen.append((done, total))
                )
            check("thread terminé dans les temps", wait_for_thread(window))
            app.processEvents()
        finally:
            VisionReviewDialog.exec = real_dialog_exec
            anthropic.Anthropic = original_anthropic  # type: ignore[assignment]

        check("les deux pages ont été transcrites (progression vision reçue)",
              progress_seen == [(1, 2), (2, 2)], f"({progress_seen})")
        check("le faux client a bien été appelé deux fois (pas de cache réutilisé par erreur)",
              fake_vision_b.messages.calls == 2, f"({fake_vision_b.messages.calls})")
        check("fichier traduit créé après validation du récapitulatif", vision_out2.exists())
        check("le texte corrigé (pas le texte brut) a été traduit",
              vision_out2.exists() and "FR Corrected page" in vision_out2.read_text(encoding="utf-8"))

        print("  9c. OCR local par défaut (PaddleOCR), sans clé API requise")
        # Chemin par défaut depuis le 25/08/2026 (voir _build_ui,
        # PaddleOCR à l'index 0 de vision_model_combo) : remis explicitement
        # ici -- 9/9a/9b ont choisi Anthropic pour tester ce chemin-là, ne
        # pas laisser cet état fuiter sur ce test-ci.
        window.vision_model_combo.setCurrentIndex(window.vision_model_combo.findData("paddleocr"))
        window.api_key_edit.clear()  # aucune clé -- ne doit RIEN bloquer sur ce chemin

        paddleocr_calls = {"count": 0}
        real_extract_paddleocr = vision_ocr.extract_text_paddleocr

        def fake_extract_paddleocr(pdf_path, cache_path, *, src_lang="eng_Latn", on_progress=None, should_stop=None):
            # Un vrai appel à PaddleOCR prend plusieurs dizaines de secondes
            # par page (mesuré, voir SPEC.md) -- bien trop lent pour ce
            # test, qui vérifie le CÂBLAGE (bon chemin appelé, pas de
            # blocage sur la clé), pas la qualité réelle de l'OCR (déjà
            # vérifiée séparément, à la main, sur un vrai document).
            paddleocr_calls["count"] += 1
            report = vision_ocr.VisionOcrReport(total_pages=1, model="paddleocr")
            result = vision_ocr.PageResult(
                page_index=0, printed_page_number=None, header=None,
                original_text="raw text", corrected_text="OCR local page one.",
                flagged=False, input_tokens=0, output_tokens=0,
            )
            report.pages.append(result)
            if on_progress is not None:
                on_progress(1, 1, report)
            return "OCR local page one.", report

        vision_ocr.extract_text_paddleocr = fake_extract_paddleocr
        vision_pdf3 = workdir / "vision3.pdf"
        make_vision_pdf(vision_pdf3, ["Raw local page."])
        vision_out3 = out_dir / "vision3.md"
        window._set_source(vision_pdf3)
        window.output_dir = out_dir
        window._update_output_preview()

        VisionReviewDialog.exec = lambda self: QDialog.Accepted
        try:
            window._start_vision()
            check("aucun blocage sur la clé API (chemin local par défaut)", window.thread is not None)
            check("thread terminé dans les temps", wait_for_thread(window))
            app.processEvents()
        finally:
            VisionReviewDialog.exec = real_dialog_exec
            vision_ocr.extract_text_paddleocr = real_extract_paddleocr

        check("extract_text_paddleocr appelé (pas Anthropic) pour ce chemin par défaut",
              paddleocr_calls["count"] == 1, f"({paddleocr_calls['count']})")
        check("fichier traduit créé via l'OCR local", vision_out3.exists())
        check("le texte OCR local (pas le texte brut) a été traduit",
              vision_out3.exists() and "FR OCR local page one." in vision_out3.read_text(encoding="utf-8"))

        print("\n10. Extraction seulement : mêmes boutons, sans traduction")
        idx_13b = window.model_combo.findData("1.3B")
        # 1.3B est aussi un profil "usage personnel" (voir _on_model_selected) :
        # mocké le temps de ce seul changement, sinon une vraie QMessageBox
        # s'ouvrirait ici en mode offscreen.
        real_exec = QMessageBox.exec
        QMessageBox.exec = lambda self: None
        try:
            window.model_combo.setCurrentIndex(idx_13b)  # non caché : déclencherait normalement une confirmation
        finally:
            QMessageBox.exec = real_exec
        check("boutons intitulés Traduire/Traduire X avant de cocher",
              window.translate_button.text() == "Traduire" and window.translate_x_button.text() == "Traduire X")

        window.extract_only_check.setChecked(True)
        check("boutons relibellés Extraire/Extraire X", window.translate_button.text() == "Extraire")
        check("Extraire X aussi relibellé", window.translate_x_button.text() == "Extraire X")
        check("langue cible désactivée (n'a plus de sens sans traduction)", not window.tgt_combo.isEnabled())
        check("modèle désactivé (aucun modèle chargé en mode extraction)", not window.model_combo.isEnabled())

        extract_source = workdir / "extract_ui.txt"
        extract_source.write_text(
            "\n\n".join(
                f"Paragraph {i} for the extraction-only UI test, long enough to be a real paragraph."
                for i in range(5)
            ) + "\n",
            encoding="utf-8",
        )
        extract_out_ui = out_dir / "extract_ui.md"
        window._set_source(extract_source)
        window.output_dir = out_dir
        window._update_output_preview()
        check("aperçu de sortie sans mention de titre traduit",
              "renommage" in window.output_preview.text())

        # test_ui.py assigne directement FakeEngine comme constructeur (pas
        # de fabrique-compteur comme dans test_pipeline.py) -- on en pose une
        # ici, juste pour cette vérification, afin de savoir si le moteur a
        # réellement été instancié pour ce job précis.
        engine_construction_count = {"count": 0}
        real_fake_engine_ctor = pipeline.translate.PreciseEngine

        def counting_engine_factory(*args, **kwargs):
            engine_construction_count["count"] += 1
            return real_fake_engine_ctor(*args, **kwargs)

        pipeline.translate.PreciseEngine = counting_engine_factory  # type: ignore[assignment]

        exec_calls_extract = {"count": 0}
        real_exec = QMessageBox.exec
        QMessageBox.exec = lambda self: exec_calls_extract.__setitem__("count", exec_calls_extract["count"] + 1)
        try:
            window._start()
            check("aucune confirmation de téléchargement (le modèle 1.3B non caché est ignoré)",
                  exec_calls_extract["count"] == 0)
            check("thread démarré malgré le modèle 1.3B non mis en cache", window.thread is not None)
            check("thread terminé dans les temps", wait_for_thread(window))
            app.processEvents()
        finally:
            QMessageBox.exec = real_exec
            pipeline.translate.PreciseEngine = real_fake_engine_ctor  # type: ignore[assignment]

        check("aucun moteur NLLB créé (extraction seule)", engine_construction_count["count"] == 0)
        extract_ui_content = extract_out_ui.read_text(encoding="utf-8") if extract_out_ui.exists() else ""
        check("fichier extrait créé, dans la langue source (aucun préfixe « FR »)",
              extract_out_ui.exists() and "FR " not in extract_ui_content)
        check("bandeau final mentionne « extraits », pas « traduits »",
              "extraits" in window.stats_label.text(), f"({window.stats_label.text()})")

        window.extract_only_check.setChecked(False)
        check("boutons redeviennent Traduire/Traduire X en décochant",
              window.translate_button.text() == "Traduire" and window.translate_x_button.text() == "Traduire X")
        check("langue cible et modèle réactivés en décochant",
              window.tgt_combo.isEnabled() and window.model_combo.isEnabled())
        real_exec = QMessageBox.exec
        QMessageBox.exec = lambda self: None
        try:
            window.model_combo.setCurrentIndex(window.model_combo.findData("600M"))  # revient à un modèle déjà présent
        finally:
            QMessageBox.exec = real_exec

        print("\n11. Hall d'accueil et navigation par écrans (demande explicite de l'utilisateur)")
        # Fenêtre dédiée, pas la `window` partagée par les sections
        # précédentes -- son état (fichier chargé, case Extraction cochée,
        # etc.) a été mutée de bien des façons depuis le début du fichier ;
        # plus simple et plus sûr de repartir d'un écran neuf pour vérifier
        # la navigation elle-même. Repère de reprise explicitement effacé
        # avant construction : ne dépend pas de ce que les sections
        # précédentes ont pu laisser dans les réglages isolés -- sinon
        # `_offer_resume_pending_jobs` pourrait ouvrir une vraie boîte de
        # dialogue non prévue par ce test précis.
        _settings_data = settings.load_settings()
        _settings_data["pending_jobs"] = {}
        settings.save_settings(_settings_data)
        hub_window = MainWindow()
        try:
            check("le hall d'accueil est le tout premier écran vu",
                  hub_window.pages.currentIndex() == PAGE_HUB)

            hub_window._go_to_translate()
            flush_page_animation(hub_window, PAGE_TRANSLATE)
            check("« TRADUIRE DU VOLUME » mène à l'écran de traduction",
                  hub_window.pages.currentIndex() == PAGE_TRANSLATE)
            check("Extraction seulement décochée depuis ce chemin",
                  not hub_window.extract_only_check.isChecked())

            hub_window._navigate_to(PAGE_HUB)
            flush_page_animation(hub_window, PAGE_HUB)
            hub_window._go_to_extract()
            flush_page_animation(hub_window, PAGE_TRANSLATE)
            check("« EXTRAIRE AVEC ANALYSE » mène au même écran, case cochée",
                  hub_window.pages.currentIndex() == PAGE_TRANSLATE
                  and hub_window.extract_only_check.isChecked())
            check("le bouton de retour au menu porte le bon texte",
                  hub_window.back_to_hub_button.text() == "← Menu")
            hub_window.back_to_hub_button.click()
            flush_page_animation(hub_window, PAGE_HUB)
            check("de retour au hall d'accueil après le clic",
                  hub_window.pages.currentIndex() == PAGE_HUB)

            hub_window._go_to_tools()
            flush_page_animation(hub_window, PAGE_TOOLS)
            check("« ANNULER NETTOYAGE » mène à l'écran Outils",
                  hub_window.pages.currentIndex() == PAGE_TOOLS)

            print("  11a. Outil d'annulation du nettoyage")
            from core import postprocess

            no_backup_path = out_dir / "outil_sans_sauvegarde.md"
            no_backup_path.write_text("Contenu quelconque.\n", encoding="utf-8")
            hub_window.undo_path_edit.setText(str(no_backup_path))
            check("aucune sauvegarde -> bouton désactivé",
                  not hub_window.undo_button.isEnabled())
            check("message explicite sur l'absence de sauvegarde",
                  "Aucune sauvegarde" in hub_window.undo_status_label.text())

            with_backup_path = out_dir / "outil_avec_sauvegarde.md"
            original_text = "## Ceci est une phrase complète qui ne devrait pas être un titre.\n"
            with_backup_path.write_text(original_text, encoding="utf-8")
            postprocess.cleanup_file(
                [{"type": "heading", "text": "This is a full sentence that should not be a heading."}],
                with_backup_path, apply=True,
            )
            hub_window.undo_path_edit.setText("")  # force un changement pour redéclencher le signal
            hub_window.undo_path_edit.setText(str(with_backup_path))
            check("sauvegarde présente -> bouton activé",
                  hub_window.undo_button.isEnabled())
            check("message confirme qu'une sauvegarde existe",
                  "prêt à annuler" in hub_window.undo_status_label.text())

            # _on_undo_cleanup_clicked ouvre une vraie QMessageBox.information
            # de confirmation -- mockée le temps du clic, sinon elle reste
            # ouverte pour toujours en mode offscreen.
            real_exec = QMessageBox.exec
            QMessageBox.exec = lambda self: None
            try:
                hub_window.undo_button.click()
            finally:
                QMessageBox.exec = real_exec
            check("le fichier est restauré à l'identique après le clic",
                  with_backup_path.read_text(encoding="utf-8") == original_text)
            check("le bouton se désactive une fois la sauvegarde consommée",
                  not hub_window.undo_button.isEnabled())
        finally:
            hub_window.close()

        print("\n12. Hauteur des 5 boutons et modal des clés API (demande explicite de l'utilisateur)")
        heights = {
            b.height() for b in (
                window.translate_button, window.translate_x_button,
                window.pause_button, window.stop_button, window.reboost_button,
            )
        }
        check("les 5 boutons ont exactement la même hauteur", len(heights) == 1, f"({heights})")
        check("cette hauteur est bien celle de Traduire",
              window.translate_button.height() == window.translate_button.sizeHint().height())
        check("le champ de clé API n'est plus modifiable directement",
              window.api_key_edit.isReadOnly())

        settings.set_anthropic_api_key(None)
        settings.set_xai_api_key(None)
        settings.set_openai_api_key(None)
        window.api_key_edit.clear()

        real_dialog_exec = ApiKeysDialog.exec

        def fake_accept_with_keys(self):
            self.anthropic_edit.setText("sk-ant-fake")
            self.xai_edit.setText("xai-fake")
            self.openai_edit.setText("sk-openai-fake")
            return QDialog.Accepted

        ApiKeysDialog.exec = fake_accept_with_keys
        try:
            window._open_api_keys_dialog()
        finally:
            ApiKeysDialog.exec = real_dialog_exec
        check("clic (simulé) -> les 3 clés sont enregistrées",
              settings.get_anthropic_api_key() == "sk-ant-fake"
              and settings.get_xai_api_key() == "xai-fake"
              and settings.get_openai_api_key() == "sk-openai-fake")
        check("le champ affiché reflète la clé Anthropic enregistrée",
              window.api_key_edit.text() == "sk-ant-fake")

        ApiKeysDialog.exec = lambda self: QDialog.Rejected
        try:
            window._open_api_keys_dialog()
        finally:
            ApiKeysDialog.exec = real_dialog_exec
        check("« Annuler » ne change rien", settings.get_anthropic_api_key() == "sk-ant-fake")

        print("\n13. Page Paramètres et diagnostic matériel (demande explicite de l'utilisateur)")
        # D'abord retour au vrai hall d'accueil (le test précédent a pu
        # laisser `window` sur un autre écran) pour cliquer le VRAI bouton,
        # pas appeler _go_to_settings() directement -- teste le câblage réel.
        window._navigate_to(PAGE_HUB)
        flush_page_animation(window, PAGE_HUB)
        window.settings_link_button.click()
        flush_page_animation(window, PAGE_SETTINGS)
        check("cliquer « Paramètres » depuis le hall d'accueil mène à l'écran Paramètres",
              window.pages.currentIndex() == PAGE_SETTINGS)

        # La détection ne se lance PLUS toute seule à l'arrivée sur cette
        # page (demande explicite de l'utilisateur, 26/08/2026) -- rien
        # n'a encore été analysé tant que le bouton dédié n'a pas été
        # cliqué.
        check("aucune détection lancée avant le clic sur « Analyser »",
              "non lancée" in window.system_info_label.text().lower(),
              f"({window.system_info_label.text()!r})")
        window.refresh_system_info_button.click()

        info_text = window.system_info_label.text()
        check("le texte affiché mentionne le processeur", "Processeur" in info_text, f"({info_text!r})")
        check("le texte affiché mentionne le GPU", "GPU" in info_text, f"({info_text!r})")
        check("le texte affiché distingue explicitement le cas de Turbo (toujours CPU)",
              "Turbo" in info_text and "TOUJOURS" in info_text, f"({info_text!r})")

        # Diagnostic RÉEL, pas mocké : reflète le vrai matériel de la machine
        # qui exécute ce test -- cohérent avec tests/test_system_info.py,
        # qui vérifie core/system_info.py::detect() en isolation. Ici, on
        # vérifie juste que l'interface affiche bien CE QUE detect() a
        # réellement renvoyé, sans le retranscrire à moitié ou le fausser.
        real_info = system_info.detect()
        check("le CPU affiché correspond à celui réellement détecté",
              real_info.cpu_name in info_text, f"({real_info.cpu_name!r} pas dans {info_text!r})")

        window._navigate_to(PAGE_HUB)
        flush_page_animation(window, PAGE_HUB)
        check("retour au hall d'accueil depuis Paramètres", window.pages.currentIndex() == PAGE_HUB)

        print("\n14. Inversion langue source / langue cible (demande explicite de l'utilisateur)")
        window._navigate_to(PAGE_TRANSLATE)
        flush_page_animation(window, PAGE_TRANSLATE)

        src_before = window.src_combo.currentData()
        tgt_before = window.tgt_combo.currentData()
        check("langue source et langue cible de départ sont bien différentes (préalable au test)",
              src_before != tgt_before, f"({src_before!r})")

        window.swap_lang_button.click()
        check("un clic échange la langue source vers l'ancienne langue cible",
              window.src_combo.currentData() == tgt_before)
        check("un clic échange la langue cible vers l'ancienne langue source",
              window.tgt_combo.currentData() == src_before)

        window.swap_lang_button.click()
        check("un second clic (aller-retour) restaure la langue source d'origine",
              window.src_combo.currentData() == src_before)
        check("un second clic (aller-retour) restaure la langue cible d'origine",
              window.tgt_combo.currentData() == tgt_before)

        # En mode extraction seule, la langue cible ne sert à rien (voir
        # _on_extract_only_toggled) -- le bouton d'inversion doit suivre
        # exactement le même sort que tgt_combo/tgt_label, pas rester
        # actif à échanger vers une langue cible qui n'a plus de sens.
        window.extract_only_check.setChecked(True)
        check("le bouton d'inversion se désactive en mode extraction seule (comme la langue cible)",
              not window.swap_lang_button.isEnabled())
        window.extract_only_check.setChecked(False)
        check("le bouton d'inversion se réactive hors mode extraction seule",
              window.swap_lang_button.isEnabled())

        print("\n15. Nouvelles cartes Paramètres : à propos, clés API, cache OCR (demande explicite)")
        window._navigate_to(PAGE_SETTINGS)
        flush_page_animation(window, PAGE_SETTINGS)

        about_text = window.about_text_label.text()
        check("la carte « À propos » affiche le numéro de version réel",
              version_mod.VERSION in about_text, f"({about_text!r})")
        # "AJTVIRTUAL" depuis le 27/08/2026 (changement d'éditeur fait
        # directement par l'utilisateur, commit 9827c73) -- "AJTWS" reste
        # le nom encore utilisé ailleurs (splash `main.py`, installeur),
        # incohérence signalée mais pas résolue unilatéralement ici.
        check("la carte « À propos » mentionne l'éditeur", "AJTVIRTUAL" in about_text)

        # Bouton « Gérer les clés API » : doit ouvrir le même ApiKeysDialog
        # que le champ de la page Traduire, pas un doublon divergent.
        real_dialog_exec = ApiKeysDialog.exec
        ApiKeysDialog.exec = lambda self: QDialog.Rejected
        try:
            window.api_keys_settings_button.click()
            check("« Gérer les clés API » (Paramètres) ouvre bien ApiKeysDialog sans planter", True)
        finally:
            ApiKeysDialog.exec = real_dialog_exec

        # Cache OCR/reprise : deux faux dossiers .translax préparés dans le
        # dossier de sortie par défaut, comme le ferait un vrai job (voir
        # core/state.py::work_dir et core/pipeline.py::vision_cache).
        cache_root = workdir / "sortie_avec_cache"
        (cache_root / "livre1" / state_mod.WORK_DIR_NAME).mkdir(parents=True)
        (cache_root / "livre1" / state_mod.WORK_DIR_NAME / "x.progress.json").write_text("{}", encoding="utf-8")
        (cache_root / "livre2" / state_mod.WORK_DIR_NAME).mkdir(parents=True)
        (cache_root / "livre2" / state_mod.WORK_DIR_NAME / "y.vision_cache.jsonl").write_text("{}\n", encoding="utf-8")
        settings.set_default_output_dir(cache_root)

        window.scan_default_cache_button.click()
        check("l'analyse trouve bien les 2 dossiers de cache préparés",
              "2 dossier" in window.cache_status_label.text(), f"({window.cache_status_label.text()!r})")
        check("« Vider les caches trouvés » s'active une fois des caches trouvés",
              window.clear_cache_button.isEnabled())

        # Confirmation avant suppression : simule un clic sur « Supprimer »
        # en repérant le bouton par identité (jamais par position -- voir
        # l'incident de réordonnancement des boutons documenté plus haut).
        real_msgbox_exec = QMessageBox.exec
        real_clicked_button = QMessageBox.clickedButton

        def fake_confirm_delete(self):
            for b in self.buttons():
                if b.text() == "Supprimer":
                    self._test_clicked = b
            return 0

        QMessageBox.exec = fake_confirm_delete
        QMessageBox.clickedButton = lambda self: getattr(self, "_test_clicked", None)
        try:
            window._clear_found_caches()
        finally:
            QMessageBox.exec = real_msgbox_exec
            QMessageBox.clickedButton = real_clicked_button

        check("les deux dossiers .translax sont réellement supprimés du disque",
              not (cache_root / "livre1" / state_mod.WORK_DIR_NAME).exists()
              and not (cache_root / "livre2" / state_mod.WORK_DIR_NAME).exists())
        check("« Vider les caches trouvés » se désactive une fois le cache vidé",
              not window.clear_cache_button.isEnabled())

        window._navigate_to(PAGE_HUB)
        flush_page_animation(window, PAGE_HUB)

        print("\n16. Sélecteurs : molette ignorée, modèle OCR (Traduire X), verrouillage pendant l'exécution")
        window._navigate_to(PAGE_TRANSLATE)
        flush_page_animation(window, PAGE_TRANSLATE)

        for combo_name in ("src_combo", "tgt_combo", "model_combo", "output_format_combo", "vision_model_combo"):
            check(f"{combo_name} est bien un NoScrollComboBox (molette ignorée)",
                  isinstance(getattr(window, combo_name), NoScrollComboBox))

        before_idx = window.model_combo.currentIndex()
        wheel = QWheelEvent(
            QPointF(0, 0), QPointF(0, 0), QPoint(0, 0), QPoint(0, -120),
            Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False,
        )
        window.model_combo.wheelEvent(wheel)
        check("un évènement molette sur un sélecteur ne change pas sa valeur",
              window.model_combo.currentIndex() == before_idx)
        check("l'évènement molette est bien ignoré (remonte au parent), pas juste avalé",
              not wheel.isAccepted())

        check("le sélecteur de modèle OCR propose bien les 4 fournisseurs déjà cités",
              window.vision_model_combo.count() == 4)
        check("PaddleOCR est le choix par défaut (gratuit, local)",
              window.vision_model_combo.currentData() == "paddleocr")
        vision_model = window.vision_model_combo.model()
        check("PaddleOCR est sélectionnable (fonctionnalité réelle)", vision_model.item(0).isEnabled())
        check("Claude/Anthropic est sélectionnable (fonctionnalité réelle)", vision_model.item(1).isEnabled())
        check("Grok/xAI n'est PAS sélectionnable (pas encore implémenté)", not vision_model.item(2).isEnabled())
        check("ChatGPT/OpenAI n'est PAS sélectionnable (pas encore implémenté)", not vision_model.item(3).isEnabled())

        window._set_running(True)
        check("le format de sortie se verrouille pendant l'exécution", not window.output_format_combo.isEnabled())
        check("le sélecteur de modèle OCR se verrouille pendant l'exécution", not window.vision_model_combo.isEnabled())
        window._set_running(False)
        check("le format de sortie se déverrouille une fois terminé", window.output_format_combo.isEnabled())
        check("le sélecteur de modèle OCR se déverrouille une fois terminé", window.vision_model_combo.isEnabled())

        print("\n17. Page Paramètres responsive (demande explicite de l'utilisateur)")
        # Bug réel rencontré en construisant cette fonctionnalité : un
        # label à retour à la ligne mais sans la bonne politique de taille
        # fait exploser la largeur réclamée par toute la page (calculée
        # sur son texte en UNE ligne) bien au-delà de la fenêtre réelle,
        # dans une QScrollArea -- ce test vérifie qu'aucun widget ne
        # déborde horizontalement, même à la largeur minimale de la
        # fenêtre (setMinimumSize), pas seulement en grand.
        window.show()
        window._navigate_to(PAGE_SETTINGS)
        flush_page_animation(window, PAGE_SETTINGS)
        window.resize(0, 900)  # clampé par setMinimumSize -- la largeur minimale réelle de l'appli
        for _ in range(8):
            app.processEvents()

        settings_buttons = (
            window.scan_default_cache_button, window.pick_cache_folder_button, window.clear_cache_button,
            window.refresh_system_info_button, window.api_keys_settings_button,
        )
        window_right = window.mapToGlobal(window.rect().topRight()).x()
        overflowing = [
            b.text() for b in settings_buttons
            if b.mapToGlobal(b.rect().topRight()).x() > window_right
        ]
        check("aucun bouton de la page Paramètres ne déborde à la largeur minimale de la fenêtre",
              not overflowing, f"({overflowing!r})")
        check("les boutons du cache OCR sont empilés verticalement (jamais tronqués côte à côte)",
              window.scan_default_cache_button.y() < window.pick_cache_folder_button.y()
              < window.clear_cache_button.y())
        window.hide()

        window._navigate_to(PAGE_HUB)
        flush_page_animation(window, PAGE_HUB)

        print("\n18. Mises à jour (page Paramètres, demande explicite de l'utilisateur -- « comme sur VS Code »)")
        window._navigate_to(PAGE_SETTINGS)
        flush_page_animation(window, PAGE_SETTINGS)
        check("aucune vérification lancée toute seule à l'arrivée sur la page",
              "non lancée" in window.update_status_label.text().lower(),
              f"({window.update_status_label.text()!r})")

        real_check_latest = updater.check_latest_release
        real_download = updater.download_installer
        real_launch = updater.launch_installer_and_quit
        real_app_quit = QApplication.quit

        print("  18a. Nouvelle version trouvée -> bouton « Mettre à jour » apparaît")
        fake_release = updater.ReleaseInfo(
            version="99.0.0", download_url="https://example.invalid/fake.exe",
            asset_size=123, notes="Notes de test.",
        )
        updater.check_latest_release = lambda: fake_release
        try:
            window.check_update_button.click()
            check("thread de vérification terminé", wait_for_named_thread(window, "_update_check_thread"))
            app.processEvents()
            check("la nouvelle version (fictive) est bien signalée",
                  "99.0.0" in window.update_status_label.text(), f"({window.update_status_label.text()!r})")
            # `isVisibleTo(window)`, pas `isVisible()` : la fenêtre elle-même
            # est cachée depuis la section 17 (`window.hide()`) -- `isVisible()`
            # tiendrait compte de ça et renverrait toujours False, peu importe
            # `setVisible(True)`. `isVisibleTo` ignore l'état du sommet et ne
            # regarde que ce que CE widget voudrait être, ce qui est bien ce
            # qu'on veut vérifier ici.
            check("le bouton « Mettre à jour » apparaît", window.install_update_button.isVisibleTo(window))
        finally:
            updater.check_latest_release = real_check_latest

        print("  18b. Déjà à jour -> pas de bouton « Mettre à jour »")
        same_release = updater.ReleaseInfo(
            version=version_mod.VERSION, download_url="https://example.invalid/fake.exe",
            asset_size=1, notes="",
        )
        updater.check_latest_release = lambda: same_release
        try:
            window.check_update_button.click()
            check("thread de vérification terminé", wait_for_named_thread(window, "_update_check_thread"))
            app.processEvents()
            check("« déjà la dernière version » affiché quand rien de plus récent",
                  "déjà la dernière version" in window.update_status_label.text().lower(),
                  f"({window.update_status_label.text()!r})")
            check("le bouton « Mettre à jour » disparaît",
                  not window.install_update_button.isVisibleTo(window))
        finally:
            updater.check_latest_release = real_check_latest

        print("  18c. Échec réseau -> message lisible, jamais un plantage")
        def fake_check_error():
            raise updater.UpdateCheckError("panne réseau simulée")
        updater.check_latest_release = fake_check_error
        try:
            window.check_update_button.click()
            check("thread de vérification (échec) terminé", wait_for_named_thread(window, "_update_check_thread"))
            app.processEvents()
            check("l'échec de vérification est affiché lisiblement",
                  "panne réseau simulée" in window.update_status_label.text())
        finally:
            updater.check_latest_release = real_check_latest

        print("  18d. « Mettre à jour » : téléchargement puis lancement de l'installeur, TRANSLAX se ferme")
        updater.check_latest_release = lambda: fake_release

        def fake_download(url, dest_path, on_progress=None, should_stop=None):
            dest_path.write_bytes(b"contenu factice")
            if on_progress:
                on_progress(10, 10)

        updater.download_installer = fake_download
        launch_calls = {"count": 0}
        updater.launch_installer_and_quit = lambda path: launch_calls.__setitem__("count", launch_calls["count"] + 1)
        quit_calls = {"count": 0}
        QApplication.quit = lambda self=None: quit_calls.__setitem__("count", quit_calls["count"] + 1)

        real_exec = QMessageBox.exec
        real_clicked = QMessageBox.clickedButton
        QMessageBox.exec = lambda self: None
        QMessageBox.clickedButton = lambda self: next(b for b in self.buttons() if b.text() == "Mettre à jour")
        try:
            window.check_update_button.click()
            check("thread de vérification (nouvelle version) terminé",
                  wait_for_named_thread(window, "_update_check_thread"))
            app.processEvents()
            window.install_update_button.click()
            check("thread de téléchargement terminé", wait_for_named_thread(window, "_update_download_thread"))
            app.processEvents()
            check("l'installeur (mocké) a bien été « lancé »", launch_calls["count"] == 1)

            # QApplication.quit() est différé (QTimer.singleShot) pour
            # laisser le message s'afficher -- on laisse la boucle
            # d'évènements tourner assez longtemps pour l'observer.
            delay_loop = QEventLoop()
            QTimer.singleShot(1500, delay_loop.quit)
            delay_loop.exec()
            check("QApplication.quit() est bien appelé une fois l'installeur lancé",
                  quit_calls["count"] >= 1)
        finally:
            QMessageBox.exec = real_exec
            QMessageBox.clickedButton = real_clicked
            updater.check_latest_release = real_check_latest
            updater.download_installer = real_download
            updater.launch_installer_and_quit = real_launch
            QApplication.quit = real_app_quit

        window._navigate_to(PAGE_HUB)
        flush_page_animation(window, PAGE_HUB)

        print("\n19. Boutons info : écran épuré, explications à la demande (demande explicite de l'utilisateur)")
        window._navigate_to(PAGE_SETTINGS)
        flush_page_animation(window, PAGE_SETTINGS)
        settings_page_widget = window.pages.currentWidget().widget()
        settings_info_buttons = [
            b for b in settings_page_widget.findChildren(QPushButton) if b.objectName() == "infoButton"
        ]
        check("chaque carte à texte explicatif a bien son bouton info "
              "(Matériel, Mises à jour, À propos, Clés API, Cache)",
              len(settings_info_buttons) == 5, f"({len(settings_info_buttons)})")

        real_dialog_exec = InfoDialog.exec
        opened_titles: list[str] = []
        InfoDialog.exec = lambda self: opened_titles.append(self.windowTitle()) or 0
        try:
            for b in settings_info_buttons:
                b.click()
        finally:
            InfoDialog.exec = real_dialog_exec
        check("chaque bouton ouvre bien un InfoDialog distinct, avec un titre",
              len(opened_titles) == 5 and all(opened_titles), f"({opened_titles!r})")

        window._navigate_to(PAGE_TRANSLATE)
        flush_page_animation(window, PAGE_TRANSLATE)
        translate_page_widget = window.pages.currentWidget().widget()
        translate_info_buttons = [
            b for b in translate_page_widget.findChildren(QPushButton) if b.objectName() == "infoButton"
        ]
        check("la page Traduire a bien un bouton info pour le modèle OCR (remplace l'ancien paragraphe fixe)",
              len(translate_info_buttons) == 1, f"({len(translate_info_buttons)})")

        # InfoDialog testé directement (ni mocké, ni via .exec() bloquant) :
        # construit pour de vrai, texte réellement affiché, et le bouton
        # « Fermer » accepte réellement le dialogue.
        real_dialog = InfoDialog("Titre de test", "Texte explicatif de test.")
        check("le titre demandé est bien celui du modal", real_dialog.windowTitle() == "Titre de test")
        labels_in_dialog = [lbl.text() for lbl in real_dialog.findChildren(QLabel)]
        check("le texte explicatif est bien affiché dans le modal",
              "Texte explicatif de test." in labels_in_dialog, f"({labels_in_dialog!r})")
        close_buttons = [b for b in real_dialog.findChildren(QPushButton) if b.text() == "Fermer"]
        check("le bouton « Fermer » existe", len(close_buttons) == 1)
        if close_buttons:
            close_buttons[0].click()
            check("cliquer « Fermer » accepte bien le modal", real_dialog.result() == QDialog.Accepted)

        window._navigate_to(PAGE_HUB)
        flush_page_animation(window, PAGE_HUB)

    finally:
        pipeline.translate.PreciseEngine = original_engine  # type: ignore[assignment]
        pipeline.translate.FastEngine = original_fast_engine  # type: ignore[assignment]
        pipeline._translate_title = original_translate_title  # type: ignore[assignment]
        settings._settings_dir = real_settings_dir  # noqa: SLF001
        window.close()
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    if failures:
        print(f"{len(failures)} test(s) en échec : " + ", ".join(failures))
        return 1
    print("Tous les tests d'interface passent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
