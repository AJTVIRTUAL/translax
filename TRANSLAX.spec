# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, copy_metadata

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
    icon=['ui/icon.ico'],
)
