"""
Tests de l'installeur custom (`installer_app/`) -- entièrement codé pour ce
projet (PySide6), demande explicite de l'utilisateur (11/09/2026) : « je
veux pas que ce soit celui natif de Windows, je veux que ce soit codé tout
par [nous] ». Remplace l'ancien installeur Inno Setup.

Rien n'est simulé au niveau des opérations qui comptent réellement :
- une VRAIE copie de fichier par blocs (`operations.copy_with_progress`),
  avec un vrai fichier de plusieurs Mo (pas 500 Mo comme le vrai payload --
  la MÉCANIQUE de copie est identique quelle que soit la taille) ;
- un VRAI raccourci Windows (`.lnk`) créé par PowerShell, relu ensuite pour
  vérifier sa cible réelle -- pas seulement son existence ;
- une VRAIE clé de registre (sous une clé de TEST dédiée, jamais la vraie
  clé TRANSLAX -- voir `_patch_registry_key`) ;
- un VRAI processus « TRANSLAX.exe » (une copie renommée de `notepad.exe`)
  lancé puis fermé par `close_running_translax`, pour vérifier que ce
  garde-fou fonctionne pour de vrai, pas seulement en apparence.

Toutes les fenêtres sont construites SANS `.show()` -- comme
`tests/test_ui.py` : ce test ne fait jamais surgir de fenêtre sur le
bureau.

    python tests/test_installer.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import winreg
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from installer_app import operations  # noqa: E402

failures: list[str] = []
TIMEOUT_MS = 15_000
TEST_UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\TRANSLAX-TEST-INSTALLER"


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK   {label}")
    else:
        print(f"  ECHEC {label} {detail}")
        failures.append(label)


def wait_for_thread(thread, timeout_ms: int = TIMEOUT_MS) -> bool:
    if thread is None or not thread.isRunning():
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


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    workdir = Path(tempfile.mkdtemp(prefix="translax_installer_test_"))

    try:
        print("\n1. Copie réelle par blocs (operations.copy_with_progress)")
        src = workdir / "fake_payload.bin"
        # 12 Mo de contenu réel généré (pas des zéros -- un contenu identifiable
        # rend une corruption de copie détectable) : assez gros pour traverser
        # plusieurs blocs de 4 Mo (voir COPY_CHUNK_SIZE), assez petit pour rester
        # rapide (le vrai payload fait ~500 Mo, la mécanique est identique).
        payload = os.urandom(12 * 1024 * 1024)
        src.write_bytes(payload)
        dest = workdir / "copied.exe"
        progress_calls: list[tuple[int, int]] = []
        operations.copy_with_progress(src, dest, on_progress=lambda d, t: progress_calls.append((d, t)))
        check("le fichier copié existe", dest.exists())
        check("le contenu copié est IDENTIQUE à l'original (pas de corruption)",
              dest.read_bytes() == payload)
        check("la progression a bien été rapportée plusieurs fois (blocs de 4 Mo)",
              len(progress_calls) >= 2, f"({len(progress_calls)} appel(s))")
        check("le dernier appel de progression atteint bien la taille totale",
              progress_calls[-1][0] == len(payload))
        check("aucun fichier .part résiduel après une copie réussie",
              not dest.with_suffix(dest.suffix + ".part").exists())

        print("\n2. Annulation d'une copie en cours (should_stop)")
        dest2 = workdir / "cancelled.exe"
        raised = False
        try:
            operations.copy_with_progress(src, dest2, should_stop=lambda: True)
        except operations.InstallError:
            raised = True
        check("annuler dès le premier bloc lève bien InstallError", raised)
        check("aucun fichier final n'est laissé après une annulation", not dest2.exists())
        check("aucun fichier .part résiduel après une annulation",
              not dest2.with_suffix(dest2.suffix + ".part").exists())

        print("\n3. Raccourci Windows RÉEL (.lnk), relu pour vérifier sa vraie cible")
        shortcut_path = workdir / "TRANSLAX.lnk"
        target = dest
        operations.create_shortcut(shortcut_path, target, target, "Description de test")
        check("le fichier .lnk existe réellement", shortcut_path.exists())
        check("le fichier .lnk n'est pas vide", shortcut_path.exists() and shortcut_path.stat().st_size > 0)
        # Relecture réelle via PowerShell -- pas juste "le fichier existe",
        # la vraie CIBLE à l'intérieur du .lnk doit être la bonne.
        import subprocess
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(New-Object -ComObject WScript.Shell).CreateShortcut('{shortcut_path}').TargetPath"],
            capture_output=True, text=True, timeout=15,
        )
        check("la cible relue dans le .lnk correspond bien au fichier visé",
              result.stdout.strip().lower() == str(target).lower(), f"(lu : {result.stdout.strip()!r})")

        print("\n4. Registre RÉEL, sous une clé de TEST dédiée (jamais la vraie clé TRANSLAX)")
        real_key = operations.UNINSTALL_KEY
        operations.UNINSTALL_KEY = TEST_UNINSTALL_KEY
        try:
            uninstall_exe = workdir / "TRANSLAX-Uninstall.exe"
            operations.register_uninstall_entry("9.9.9-test", workdir, workdir / "icon.ico", uninstall_exe)
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, TEST_UNINSTALL_KEY) as key:
                display_name, _ = winreg.QueryValueEx(key, "DisplayName")
                display_version, _ = winreg.QueryValueEx(key, "DisplayVersion")
                no_modify, _ = winreg.QueryValueEx(key, "NoModify")
                uninstall_string, _ = winreg.QueryValueEx(key, "UninstallString")
                quiet_uninstall_string, _ = winreg.QueryValueEx(key, "QuietUninstallString")
            check("DisplayName écrit correctement", display_name == "TRANSLAX")
            check("DisplayVersion écrit correctement", display_version == "9.9.9-test", f"({display_version!r})")
            check("NoModify=1 (empêche un bouton “Modifier” qui n'existe pas)", no_modify == 1)
            # RÉGRESSION RÉELLE : la première version de ces chaînes omettait
            # `--uninstall`, si bien que "Désinstaller" depuis Windows
            # tentait une INSTALLATION -- constaté en lançant pour de vrai le
            # désinstalleur gelé, resté bloqué sans rien faire.
            check("UninstallString contient bien --uninstall (sinon : tente une installation)",
                  "--uninstall" in uninstall_string, f"({uninstall_string!r})")
            check("QuietUninstallString contient --uninstall ET --silent",
                  "--uninstall" in quiet_uninstall_string and "--silent" in quiet_uninstall_string,
                  f"({quiet_uninstall_string!r})")

            operations.unregister_uninstall_entry()
            removed = False
            try:
                winreg.OpenKey(winreg.HKEY_CURRENT_USER, TEST_UNINSTALL_KEY)
            except FileNotFoundError:
                removed = True
            check("la clé de test a bien été retirée après unregister_uninstall_entry", removed)
        finally:
            operations.UNINSTALL_KEY = real_key
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, TEST_UNINSTALL_KEY)
            except OSError:
                pass  # déjà retirée normalement -- filet de sécurité si un check a échoué avant

        print("\n5. Fermeture RÉELLE d'un processus « TRANSLAX.exe » (pas un simulacre)")
        # Une vraie copie de ping.exe renommée, lancée en boucle continue
        # (-t) -- un vrai processus Windows qui reste résident sous le bon
        # nom d'image, pas une simple supposition sur ce que ferait
        # tasklist/taskkill. notepad.exe a été essayé en premier et écarté :
        # sur les Windows récents, System32\notepad.exe n'est qu'un
        # redirecteur vers l'appli Notepad empaquetée (Store) -- une copie
        # renommée se lance et se termine aussitôt sans laisser de
        # processus réel à observer, constaté en le testant réellement.
        ping_exe = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "ping.exe"
        fake_translax = workdir / operations.EXE_NAME
        if not ping_exe.exists():
            print("  SAUTÉ -- ping.exe introuvable sur cette machine (attendu sur un vrai Windows).")
        else:
            shutil.copy(ping_exe, fake_translax)
            import subprocess as sp
            proc = sp.Popen([str(fake_translax), "-t", "127.0.0.1"], stdout=sp.DEVNULL, stderr=sp.DEVNULL)
            time.sleep(1.0)
            check("le faux TRANSLAX.exe est bien vu comme en cours d'exécution",
                  operations.is_translax_running())
            closed = operations.close_running_translax(timeout_s=5.0)
            check("close_running_translax rapporte un succès réel", closed)
            check("plus aucun TRANSLAX.exe ne tourne après fermeture",
                  not operations.is_translax_running())
            proc.wait(timeout=5)

        print("\n6. Installation complète, de bout en bout, chemins redirigés vers un dossier de test")
        install_dir = workdir / "install"
        desktop_link = workdir / "desktop" / "TRANSLAX.lnk"
        start_menu_link = workdir / "startmenu" / "TRANSLAX.lnk"
        real_payload = operations.bundled_app_exe_path
        real_desktop = operations.desktop_shortcut_path
        real_start_menu = operations.start_menu_shortcut_path
        real_key2 = operations.UNINSTALL_KEY
        operations.bundled_app_exe_path = lambda: src           # réutilise le faux payload de la section 1
        operations.desktop_shortcut_path = lambda: desktop_link
        operations.start_menu_shortcut_path = lambda: start_menu_link
        operations.UNINSTALL_KEY = TEST_UNINSTALL_KEY
        try:
            from installer_app.ui import SetupWindow, UninstallWindow

            window = SetupWindow(silent=False)
            check("la fenêtre s'ouvre bien sur la page Bienvenue (pas Progression)",
                  window.pages.currentIndex() == SetupWindow.PAGE_WELCOME)

            window.welcome_page.install_requested.emit(install_dir, True)
            check("cliquer Installer passe bien à la page Progression",
                  window.pages.currentIndex() == SetupWindow.PAGE_PROGRESS)
            check("un vrai thread d'installation a bien démarré",
                  window._worker is not None and window._worker.isRunning())

            finished = wait_for_thread(window._worker)
            check("le thread d'installation se termine dans le délai imparti", finished)
            check("la page Fin est bien atteinte (pas la page Erreur)",
                  window.pages.currentIndex() == SetupWindow.PAGE_FINISH,
                  f"(page={window.pages.currentIndex()})")

            check("le fichier exe est réellement présent dans le dossier d'installation",
                  (install_dir / operations.EXE_NAME).exists())
            check("le contenu copié est bien celui du payload, sans corruption",
                  (install_dir / operations.EXE_NAME).read_bytes() == payload)
            check("le raccourci Bureau a réellement été créé", desktop_link.exists())
            check("le raccourci menu Démarrer a réellement été créé", start_menu_link.exists())
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, TEST_UNINSTALL_KEY) as key:
                install_location, _ = winreg.QueryValueEx(key, "InstallLocation")
            check("l'entrée de registre pointe vers le bon dossier d'installation",
                  install_location == str(install_dir))

            print("\n7. Désinstallation complète, sur cette même installation de test")
            uninstall_window = UninstallWindow(install_dir, silent=False)
            check("la désinstallation s'ouvre sur la page de confirmation",
                  uninstall_window.pages.currentIndex() == UninstallWindow.PAGE_CONFIRM)
            uninstall_window.confirm_page.confirmed.emit()
            uninstall_finished = wait_for_thread(uninstall_window._worker)
            check("le thread de désinstallation se termine dans le délai imparti", uninstall_finished)
            check("la page Fin (désinstallation) est bien atteinte",
                  uninstall_window.pages.currentIndex() == UninstallWindow.PAGE_DONE)
            check("les raccourcis ont bien été retirés",
                  not desktop_link.exists() and not start_menu_link.exists())
            entry_removed = False
            try:
                winreg.OpenKey(winreg.HKEY_CURRENT_USER, TEST_UNINSTALL_KEY)
            except FileNotFoundError:
                entry_removed = True
            check("l'entrée de registre a bien été retirée", entry_removed)

            # La suppression du DOSSIER est différée (processus détaché, voir
            # operations.remove_directory_deferred -- un exe ne peut pas se
            # supprimer lui-même pendant qu'il tourne) : on attend un peu et on
            # revérifie, plutôt que de suppose que ça a marché.
            deadline = time.time() + 8
            while install_dir.exists() and time.time() < deadline:
                time.sleep(0.5)
            check("le dossier d'installation a bien fini par être supprimé (suppression différée)",
                  not install_dir.exists())
        finally:
            operations.bundled_app_exe_path = real_payload
            operations.desktop_shortcut_path = real_desktop
            operations.start_menu_shortcut_path = real_start_menu
            operations.UNINSTALL_KEY = real_key2
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, TEST_UNINSTALL_KEY)
            except OSError:
                pass

        print("\n8. Mode silencieux (mise à jour intégrée) : aucune interaction nécessaire")
        install_dir_silent = workdir / "install_silent"
        operations.bundled_app_exe_path = lambda: src
        operations.desktop_shortcut_path = lambda: workdir / "desktop2" / "TRANSLAX.lnk"
        operations.start_menu_shortcut_path = lambda: workdir / "startmenu2" / "TRANSLAX.lnk"
        operations.UNINSTALL_KEY = TEST_UNINSTALL_KEY
        real_default_dir = operations.default_install_dir
        operations.default_install_dir = lambda: install_dir_silent
        real_launch = operations.launch
        launched: list[Path] = []
        operations.launch = lambda exe_path: launched.append(exe_path)
        try:
            from installer_app.ui import SetupWindow as SetupWindowSilent

            silent_window = SetupWindowSilent(silent=True)
            check("le mode silencieux saute directement à la page Progression (jamais Bienvenue)",
                  silent_window.pages.currentIndex() == SetupWindowSilent.PAGE_PROGRESS)
            finished_silent = wait_for_thread(silent_window._worker)
            check("l'installation silencieuse se termine dans le délai imparti", finished_silent)
            check("le fichier a bien été installé sans la moindre interaction",
                  (install_dir_silent / operations.EXE_NAME).exists())
            check("TRANSLAX est relancé automatiquement à la fin (« on clique et tout se lance »)",
                  len(launched) == 1 and launched[0] == install_dir_silent / operations.EXE_NAME)
        finally:
            operations.bundled_app_exe_path = real_payload
            operations.desktop_shortcut_path = real_desktop
            operations.start_menu_shortcut_path = real_start_menu
            operations.UNINSTALL_KEY = real_key2
            operations.default_install_dir = real_default_dir
            operations.launch = real_launch
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, TEST_UNINSTALL_KEY)
            except OSError:
                pass

        print("\n9. Un payload manquant échoue proprement, sans planter l'interface")
        operations.bundled_app_exe_path = lambda: workdir / "n_existe_pas.exe"
        try:
            from installer_app.ui import SetupWindow as SetupWindowFail

            fail_window = SetupWindowFail(silent=False)
            fail_window.welcome_page.install_requested.emit(workdir / "install_fail", False)
            wait_for_thread(fail_window._worker)
            check("une installation dont le payload est introuvable atterrit sur la page Erreur",
                  fail_window.pages.currentIndex() == SetupWindowFail.PAGE_ERROR)
            check("un message d'erreur lisible est bien affiché",
                  bool(fail_window.error_page.message_label.text()))
        finally:
            operations.bundled_app_exe_path = real_payload

        print("\n10. Sélection du mode (installer vs désinstaller) -- la RÉGRESSION exacte du point 4")
        # main.build_window ne construit jamais réellement de fenêtre pour ce
        # test (les deux branches sont vérifiées par la classe qu'AURAIT
        # choisie should_uninstall, sans instancier -- ce point teste la
        # DÉCISION, la section 6/7 ci-dessus teste déjà les fenêtres elles-
        # mêmes de bout en bout).
        from installer_app.main import parse_args, should_uninstall

        args_plain = parse_args([])
        args_uninstall_flag = parse_args(["--uninstall"])
        args_legacy = parse_args(["/SILENT"])

        operations.bundled_app_exe_path = lambda: src  # un payload EXISTE (build Setup normal)
        try:
            check("--uninstall explicite -> désinstallation, même avec un payload présent",
                  should_uninstall(args_uninstall_flag))
            check("aucun argument, payload présent (build Setup normal) -> installation",
                  not should_uninstall(args_plain))
            check("indicateur Inno hérité (/SILENT) normalisé sans déclencher la désinstallation",
                  args_legacy.silent and not should_uninstall(args_legacy))
        finally:
            operations.bundled_app_exe_path = real_payload

        operations.bundled_app_exe_path = lambda: workdir / "payload_absent.exe"  # build Uninstall dédié
        try:
            check("RÉGRESSION : aucun argument mais PAS de payload (build Uninstall) -> désinstallation quand même",
                  should_uninstall(args_plain))
        finally:
            operations.bundled_app_exe_path = real_payload

    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    if failures:
        print(f"{len(failures)} test(s) en échec : " + ", ".join(failures))
        return 1
    print("Tous les tests de l'installeur passent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
