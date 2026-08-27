# -*- mode: python ; coding: utf-8 -*-

import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, copy_metadata

# Rendu multiplateforme le 27/08/2026 (voir MACOS_BUILD.md) : ce fichier a
# longtemps été écrit en pensant uniquement Windows -- `collect_data_files`/
# `copy_metadata`/`collect_dynamic_libs` (les trois correctifs PaddleOCR
# ci-dessous) s'adaptent déjà tout seuls à l'OS réel (ils inspectent ce qui
# est VRAIMENT installé sur la machine qui construit, .dylib sur Mac au
# lieu de .dll sur Windows par exemple) -- rien à changer pour eux. Seuls
# l'icône (format différent selon l'OS) et l'emballage final en vrai
# bundle `.app` (voir tout en bas) sont propres à macOS.

# PaddleOCR local (voir SPEC.md §5 vicies) résout ses pipelines par chemin
# de fichier relatif à l'intérieur du paquet `paddlex`
# (paddlex/configs/pipelines/{nom}.yaml, paddlex/configs/modules/...).
# `Analysis` ne suit que le graphe d'imports Python : ces fichiers .yaml
# ne sont jamais du code importé, donc jamais copiés automatiquement --
# sans ceci, l'exe gelé plante à l'exécution avec "The pipeline (OCR)
# does not exist!" alors même que `python tests/test_vision_ocr.py`
# (interpréteur non gelé, fichiers réels sur disque) passe sans problème.
paddlex_datas = collect_data_files('paddlex')
paddleocr_datas = collect_data_files('paddleocr')

# Deuxième correctif du même genre (26/08/2026) : `paddlex` vérifie SES
# PROPRES dépendances via `importlib.metadata` (paddlex/utils/deps.py),
# pas en les important -- PyInstaller ne peut pas deviner ça par simple
# analyse du graphe d'imports, donc les dossiers .dist-info de CES
# paquets-là ne sont jamais bundlés (leur code, oui ; leurs métadonnées de
# version, non). Sans ceci : "A dependency error occurred during pipeline
# creation" au moment précis de créer le pipeline OCR, même quand tout est
# réellement installé et fonctionnel. Liste identifiée en interrogeant
# réellement `paddlex.utils.deps` sur cette machine (pas devinée) : ce
# sont exactement les paquets de l'extra "ocr-core", celui qui satisfait
# vraiment `@pipeline_requires_extra("ocr", alt="ocr-core")` -- l'extra
# "ocr" complet référence aussi des paquets non installés ici (scipy,
# scikit-learn...), utiles uniquement à des pipelines que TRANSLAX
# n'utilise jamais (PP-StructureV3, PP-ChatOCR...).
_paddlex_metadata_names = [
    "paddlex", "imagesize", "opencv-contrib-python", "pyclipper",
    "pypdfium2", "python-bidi", "shapely",
]
paddlex_metadata_datas = []
for _name in _paddlex_metadata_names:
    paddlex_metadata_datas += copy_metadata(_name)

# Troisième correctif du même genre (26/08/2026) : "RuntimeError :
# (PreconditionNotMet) The third-party dynamic library (mklml.dll) that
# Paddle depends on is not configured correctly. (error code is 126)".
# Erreur Windows 126 = "module introuvable" -- pas que mklml.dll soit
# absent du tout, mais que le moteur natif de Paddle (dynamic_loader.cc)
# ne le trouve pas là où IL s'attend à le trouver. Vérifié dans
# `paddle/__init__.py` : Paddle calcule ce chemin lui-même par
# `os.path.dirname(__file__) + "/libs"`, PAS un chemin fixe -- il faut
# donc que `paddle/libs/*.dll` existe à cet exact sous-chemin RELATIF
# dans l'exe gelé. `Analysis` détecte bien certains de ces .dll par
# analyse binaire automatique, mais peut les placer à plat (racine du
# bundle) plutôt qu'à ce sous-chemin précis -- `collect_dynamic_libs`
# (contrairement à la détection automatique) préserve la structure
# `paddle/libs/...`, exactement ce que Paddle recherche. ~188 Mo au total
# (mklml.dll et mkldnn.dll sont les plus gros, ~45-88 Mo chacun) -- une
# taille réelle et incompressible du moteur d'inférence CPU de Paddle,
# pas un signe d'un problème d'inclusion superflue.
paddle_binaries = collect_dynamic_libs('paddle')

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=paddle_binaries,
    datas=[
        ('ui/styles.qss', 'ui'),
        ('ui/icon.ico', 'ui'),
        ('ui/icons/pause.svg', 'ui/icons'),
        ('ui/icons/stop.svg', 'ui/icons'),
    ]
    + paddlex_datas + paddleocr_datas + paddlex_metadata_datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# .ico sur Windows, .icns sur macOS -- exigence de chaque OS pour l'icône
# d'un exécutable/bundle, PyInstaller ne les accepte pas de façon
# interchangeable (contrairement à ce qu'on pourrait supposer).
_icon_file = 'ui/icon.icns' if sys.platform == 'darwin' else 'ui/icon.ico'

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='TRANSLAX',
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
    icon=[_icon_file],
)

if sys.platform == 'darwin':
    # Sans ce bloc, `pyinstaller TRANSLAX.spec` sur Mac ne produirait
    # qu'un exécutable Unix nu (dist/TRANSLAX), pas un vrai bundle `.app`
    # -- contrairement au raccourci `pyinstaller --onefile --windowed`
    # (utilisé par l'ancienne version de ce guide), qui ajoute cet
    # emballage tout seul ; un .spec écrit à la main doit le faire
    # explicitement. `BUNDLE` enveloppe directement l'EXE onefile
    # ci-dessus -- pas de changement de mode (toujours un seul exécutable
    # à l'intérieur), juste l'habillage `.app` (Info.plist, icône Dock).
    #
    # NON TESTÉ sur un vrai Mac au moment d'écrire ce bloc (voir
    # MACOS_BUILD.md) -- honnêteté à jour avec le reste de ce fichier :
    # si cette étape échoue, le message d'erreur exact est ce qu'il faut
    # pour corriger.
    app = BUNDLE(
        exe,
        name='TRANSLAX.app',
        icon=_icon_file,
        bundle_identifier='com.ajtws.translax',
        info_plist={'NSHighResolutionCapable': True},
    )
