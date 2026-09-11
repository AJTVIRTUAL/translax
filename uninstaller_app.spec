# -*- mode: python ; coding: utf-8 -*-
#
# Désinstalleur de TRANSLAX -- petit exécutable SANS le payload de
# l'application (contrairement à installer_app.spec) : il n'a besoin que
# de supprimer des fichiers déjà en place, pas d'en apporter de nouveaux.
# Construit et copié dans le dossier d'installation PAR le Setup (voir
# scripts/build_installer.py) ; référencé comme `UninstallString` dans le
# registre (voir installer_app/operations.py::register_uninstall_entry).
#
# `installer_app/version.txt` est régénéré juste avant chaque build par
# scripts/build_installer.py (une seule source de vérité, core/version.py,
# jamais recopiée à la main ici).

a = Analysis(
    ['installer_app/main.py'],
    pathex=[],
    binaries=[],
    datas=[
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
    name='TRANSLAX-Uninstall',
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
