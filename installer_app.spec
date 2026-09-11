# -*- mode: python ; coding: utf-8 -*-
#
# Installeur de TRANSLAX -- entièrement codé pour ce projet (PySide6), pas
# la fenêtre native de Windows, demande explicite de l'utilisateur
# (11/09/2026). Remplace l'ancien installeur Inno Setup
# (installer/translax.iss, resté dans le dépôt mais plus utilisé depuis --
# voir SPEC.md).
#
# Embarque, comme DONNÉES (pas du code) :
#   - dist/TRANSLAX.exe        : le vrai payload (~500 Mo), copié tel quel
#                                 sur le disque de l'utilisateur à l'installation ;
#   - dist/TRANSLAX-Uninstall.exe : le désinstalleur (voir uninstaller_app.spec),
#                                 DOIT être construit AVANT ce fichier
#                                 (scripts/build_installer.py s'en charge
#                                 dans le bon ordre) ;
#   - ui/icon.ico              : icône des raccourcis Bureau/menu Démarrer
#                                 et de l'entrée "Programmes et fonctionnalités".
#
# `installer_app/version.txt` est régénéré juste avant chaque build par
# scripts/build_installer.py (une seule source de vérité, core/version.py,
# jamais recopiée à la main ici).

a = Analysis(
    ['installer_app/main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('dist/TRANSLAX.exe', 'payload'),
        ('dist/TRANSLAX-Uninstall.exe', 'payload'),
        ('ui/icon.ico', '.'),
        ('installer_app/version.txt', '.'),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='TRANSLAX-Setup',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['ui/icon.ico'],
)
