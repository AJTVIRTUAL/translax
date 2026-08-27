"""
Codes de langue NLLB-200 (format FLORES-200 : <code ISO>_<script>).

NLLB gere environ 200 langues ; on n'expose ici qu'une selection courante,
avec l'anglais et le francais en premier puisque c'est le couple par defaut
de TRANSLAX. Ajouter une langue = ajouter une ligne (le moteur ne demande
rien de plus que le code FLORES).
"""

LANGUAGES: dict[str, str] = {
    "eng_Latn": "Anglais",
    "fra_Latn": "Français",
    "spa_Latn": "Espagnol",
    "deu_Latn": "Allemand",
    "ita_Latn": "Italien",
    "por_Latn": "Portugais",
    "nld_Latn": "Néerlandais",
    "rus_Cyrl": "Russe",
    "arb_Arab": "Arabe standard",
    "zho_Hans": "Chinois simplifié",
    "jpn_Jpan": "Japonais",
    "kor_Hang": "Coréen",
    "pol_Latn": "Polonais",
    "tur_Latn": "Turc",
    "ron_Latn": "Roumain",
    "swh_Latn": "Swahili",
    "lin_Latn": "Lingala",
    "wol_Latn": "Wolof",
}

DEFAULT_SOURCE = "eng_Latn"
DEFAULT_TARGET = "fra_Latn"


def label(code: str) -> str:
    """Nom affichable d'un code FLORES (le code lui-même si inconnu)."""
    return LANGUAGES.get(code, code)


# Correspondance FLORES-200 -> ISO 639-1 (2 lettres), pour le moteur OPUS-MT
# (voir core/translate.py::OpusMtEngine) : Helsinki-NLP nomme ses modèles
# "opus-mt-{src}-{tgt}" avec ce code à 2 lettres, pas le code FLORES utilisé
# par NLLB. Une seule table, pas un simple `code.split("_")[0]` -- ça
# tomberait juste pour la plupart des langues ci-dessus, mais PAS toutes
# (zho -> zh, arb -> ar, kor -> ko : la première syllabe ne suffit pas).
FLORES_TO_ISO2: dict[str, str] = {
    "eng_Latn": "en",
    "fra_Latn": "fr",
    "spa_Latn": "es",
    "deu_Latn": "de",
    "ita_Latn": "it",
    "por_Latn": "pt",
    "nld_Latn": "nl",
    "rus_Cyrl": "ru",
    "arb_Arab": "ar",
    "zho_Hans": "zh",
    "jpn_Jpan": "ja",
    "kor_Hang": "ko",
    "pol_Latn": "pl",
    "tur_Latn": "tr",
    "ron_Latn": "ro",
    "swh_Latn": "sw",
    "lin_Latn": "ln",
    "wol_Latn": "wo",
}


def iso2(code: str) -> str | None:
    """Code ISO 639-1 (2 lettres) d'un code FLORES, ou None si absent de la
    table -- ne jamais deviner (voir FLORES_TO_ISO2), pour ne pas construire
    un nom de modèle OPUS-MT silencieusement faux."""
    return FLORES_TO_ISO2.get(code)


# Correspondance FLORES-200 -> code de langue PaddleOCR, pour l'extraction
# OCR locale (voir core/vision_ocr.py::extract_text_paddleocr). PaddleOCR a
# SES PROPRES codes, ni ISO 639-1 ni FLORES -- vérifiés un par un dans le
# code source réellement installé (paddleocr/_utils/langs.py et
# _pipelines/ocr.py), pas devinés : "ch" pas "zh" pour le chinois, "japan"
# pas "ja", "korean" pas "ko" ; l'anglais et les langues latines suivent
# ISO 639-1 (confirmés dans LATIN_LANGS / _PPOCRV6_LANGS). Deux langues de
# la table ci-dessus (lingala, wolof) n'ont pas de modèle PaddleOCR connu --
# absentes ici plutôt qu'une correspondance inventée.
FLORES_TO_PADDLEOCR: dict[str, str] = {
    "eng_Latn": "en",
    "fra_Latn": "fr",
    "spa_Latn": "es",
    "deu_Latn": "de",
    "ita_Latn": "it",
    "por_Latn": "pt",
    "nld_Latn": "nl",
    "rus_Cyrl": "ru",
    "arb_Arab": "ar",
    "zho_Hans": "ch",
    "jpn_Jpan": "japan",
    "kor_Hang": "korean",
    "pol_Latn": "pl",
    "tur_Latn": "tr",
    "ron_Latn": "ro",
    "swh_Latn": "sw",
}


def paddleocr_lang(code: str) -> str | None:
    """Code de langue PaddleOCR d'un code FLORES, ou None si absent de la
    table (voir FLORES_TO_PADDLEOCR) -- jamais deviné."""
    return FLORES_TO_PADDLEOCR.get(code)
