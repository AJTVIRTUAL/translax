"""
Extraction de texte par OCR/vision, pour les PDF dont le calque de texte
déjà embarqué dans le fichier (voir `core/extract.py`) est trop corrompu
pour être exploitable tel quel -- scans anciens, OCR d'origine médiocre.
Réservé au bouton « Traduire X » : « Traduire » reste inchangé, 100 % local
et gratuit dans tous les cas.

Deux moteurs, choisis par `Job.vision_provider` (voir core/pipeline.py) :
- **PaddleOCR** (`extract_text_paddleocr`, licence Apache 2.0) -- PAR
  DÉFAUT depuis le 25/08/2026 : 100 % local, gratuit, aucune clé ni
  connexion internet requise après le téléchargement initial des poids du
  modèle. Objectif explicite : que TRANSLAX n'ait plus besoin d'une
  dépendance payante pour cette fonctionnalité (voir OCR_VLM_COMPARATIF.md
  pour le comparatif complet qui a mené à ce choix).
- **Vision Anthropic (Claude)** (`extract_text_vision`) -- option payante,
  nécessite une connexion internet et une clé API Anthropic personnelle
  (Réglages, jamais embarquée dans TRANSLAX lui-même) : meilleure
  compréhension du CONTEXTE sur les cas difficiles (mise en page complexe,
  contenu ambigu), au prix d'un coût par page.

Les deux partagent la même forme de résultat (`PageResult`/`VisionOcrReport`)
et le même mécanisme de reprise par cache disque -- le reste du pipeline
(récapitulatif, segmentation, etc.) ne sait pas lequel a tourné.

Principe (chemin Anthropic) : chaque page du PDF est rendue en image, puis
envoyée à Claude avec une consigne de transcription FIDÈLE -- corriger les
artefacts de scan/OCR évidents en s'appuyant sur le contexte, mais ne
jamais remplacer un mot par un autre, même plus logique, que celui
réellement imprimé. Un passage sur lequel le modèle n'est pas sûr de sa
lecture est marqué (pas deviné en silence), pour apparaître signalé dans
le récapitulatif montré avant la traduction (voir `ui/main_window.py`,
`_on_vision_review_needed`) -- jamais de correction silencieuse sur un
passage douteux. Même logique de signalement côté PaddleOCR, via un seuil
de confiance par ligne (voir `PADDLEOCR_CONFIDENCE_THRESHOLD`).

Validé sur un cas réel avant d'écrire ce module (voir SPEC.md) : sur
« Hindu Magical Occultism Test.pdf », le calque de texte du PDF contenait
du bruit de caractères (« acc()rding », « tlieir », « wbo ») ET une phrase
entière absente du calque -- la vision a corrigé les deux, vérifié à l'œil
sur l'image page par page, pas juste supposé. Coût mesuré : ~0,02 $/page
avec Sonnet 5.

Reprise : chaque page transcrite est ajoutée immédiatement à un fichier de
cache (JSON Lines) à côté de la sortie -- interrompre en cours de route
(Stop, fermeture, plantage) ne fait jamais repayer une page déjà traitée
au relancement. Fonctionne par un chemin de cache explicite, sans dépendre
de `core/state.py` ni de `core/pipeline.py`, pour rester utilisable seul.

Rencontré en pratique sur un vrai livre (une polémique religieuse du 19e
siècle, hors contexte pour un classifieur automatique) : l'API Anthropic
peut bloquer sa réponse pour UNE page précise (« Output blocked by content
filtering policy »), sans que rien ne clocherait en la regardant vraiment.
Plutôt que de faire échouer tout le livre pour une page, cette page-là
garde son texte original (non corrigé) et le reste continue normalement --
voir `_is_content_filter_block`/`PageResult.vision_failed`.

Ce texte original de secours est entouré d'un marqueur début/fin (voir
`_wrap_restricted_page`, `core/segment.py`) qui survit intact jusque dans
le fichier de sortie final -- demande explicite de l'utilisateur : pouvoir
repérer d'un coup d'œil, dans le document terminé, exactement quelle(s)
page(s) n'ont pas pu être vérifiées, pour aller les corriger ailleurs à la
main plutôt que de découvrir le problème après coup, noyé dans le reste
du livre.
"""
from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import languages as languages_mod
from . import segment as segment_mod
from .translate import Cancelled  # même exception que l'arrêt en cours de traduction

DEFAULT_MODEL = "claude-sonnet-5"

# En dessous de ce seuil de confiance (voir `rec_scores` de PaddleOCR), une
# ligne fait considérer toute la page comme "flagged" -- même sémantique
# que les balises <uncertain> du chemin Anthropic : le texte reste inclus
# (la meilleure lecture du modèle), juste signalé dans le récapitulatif.
# Choisi à partir d'un cas réel (voir extract_text_paddleocr) : le corps
# d'une page lisible ressort à 0,98-1,00, un fragment d'en-tête tourné/
# minuscule et illisible à 0,47-0,61 -- 0,75 sépare proprement les deux
# sans être si strict qu'un mot isolé un peu flou déclenche le signalement
# à tort.
PADDLEOCR_CONFIDENCE_THRESHOLD = 0.75

# Un seul moteur PaddleOCR par langue, réutilisé entre pages ET entre
# documents dans le même processus -- son chargement est lent (plusieurs
# secondes, poids chargés en mémoire), le refaire à chaque page ferait
# exploser le temps total pour rien.
_paddleocr_engines: dict[str, object] = {}
RENDER_DPI = 150
MAX_OUTPUT_TOKENS = 4000

# $ par million de tokens (entrée, sortie) -- pour l'estimation de coût
# affichée dans le récapitulatif, pas une facture réelle (voir la
# documentation Anthropic pour les tarifs à jour).
MODEL_RATES: dict[str, tuple[float, float]] = {
    "claude-sonnet-5": (3.00, 15.00),
    "claude-opus-5": (5.00, 25.00),
}

UNCERTAIN_RE = re.compile(r"<uncertain>(.*?)</uncertain>", re.DOTALL)
PAGE_NUMBER_RE = re.compile(r"^PAGE_NUMBER:\s*(.*)$", re.MULTILINE)
HEADER_RE = re.compile(r"^HEADER:\s*(.*)$", re.MULTILINE)

SYSTEM_PROMPT = """You are transcribing a photographed page from an old scanned book, as \
a preprocessing step for a translation pipeline. Your ONLY job is faithful transcription.

Rules:
1. Transcribe EXACTLY what is printed on the page, including archaic spelling or the \
author's own errors -- do not modernize or paraphrase.
2. Correct clear OCR/scan artifacts (a letter rendered as a similar-looking wrong shape, \
broken characters, stray marks) using surrounding context to determine the ACTUAL printed \
letter -- but never substitute a different WORD than what is actually printed, even if \
another word would make more sense in context.
3. If you genuinely cannot determine a letter or word with confidence, keep your best \
reading but wrap ONLY that word or phrase in <uncertain>...</uncertain> tags.
4. Preserve paragraph breaks exactly as laid out (blank line between paragraphs).
5. Do NOT include the running header or the printed page number in the body transcription \
-- report them separately as instructed below.

Respond in exactly this format, nothing else:
PAGE_NUMBER: <printed page number visible on the page, or "none">
HEADER: <printed running header text, or "none">
---
<transcribed body text>"""


class VisionOcrError(Exception):
    """
    Clé API absente/invalide, réseau indisponible, erreur de l'API
    Anthropic -- toute condition qui empêche de continuer à traiter des
    pages. Message déjà rédigé pour être montré tel quel à l'utilisateur.
    """


@dataclass
class PageResult:
    page_index: int
    printed_page_number: str | None
    header: str | None
    original_text: str      # ce que extract.extract_text() aurait donné pour cette page
    corrected_text: str     # transcription vision, balises <uncertain> retirées
    flagged: bool           # au moins un passage marqué <uncertain> par le modèle
    input_tokens: int
    output_tokens: int
    vision_failed: bool = False  # filtre de contenu Anthropic : repli sur original_text, voir plus bas

    @property
    def changed(self) -> bool:
        """True si la vision a réellement modifié le texte de cette page --
        sert à ne montrer, dans le récapitulatif, que ce qui a vraiment été
        corrigé (voir ui/main_window.py, VisionReviewDialog)."""
        return _normalize(self.original_text) != _normalize(self.corrected_text)


@dataclass
class VisionOcrReport:
    total_pages: int = 0
    pages: list[PageResult] = field(default_factory=list)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    model: str = DEFAULT_MODEL

    @property
    def flagged_count(self) -> int:
        return sum(1 for p in self.pages if p.flagged)

    @property
    def failed_count(self) -> int:
        """Pages où la vision n'a pas pu tourner du tout (filtre de contenu
        Anthropic) -- le texte original (non corrigé) a été gardé tel quel
        pour ne perdre aucun contenu du livre."""
        return sum(1 for p in self.pages if p.vision_failed)

    @property
    def changed_count(self) -> int:
        """Pages dont le texte a réellement changé -- sert à ne montrer, par
        défaut, que ce qui a été corrigé plutôt que tout le livre."""
        return sum(1 for p in self.pages if p.changed)

    def estimated_cost_usd(self) -> float:
        rate_in, rate_out = MODEL_RATES.get(self.model, MODEL_RATES[DEFAULT_MODEL])
        return self.total_input_tokens / 1_000_000 * rate_in + self.total_output_tokens / 1_000_000 * rate_out


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _parse_response(raw: str) -> tuple[str | None, str | None, str, bool]:
    """Découpe la réponse structurée du modèle en (numéro de page, en-tête,
    texte du corps, signalé comme incertain)."""
    def clean(value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return None if value.lower() == "none" else value

    page_number = clean(m.group(1)) if (m := PAGE_NUMBER_RE.search(raw)) else None
    header = clean(m.group(1)) if (m := HEADER_RE.search(raw)) else None

    body = raw.split("---", 1)[1] if "---" in raw else raw
    flagged = bool(UNCERTAIN_RE.search(body))
    # Le texte incertain est gardé (la meilleure lecture du modèle reste
    # utile à la traduction) -- seule la balise est retirée ; le signalement
    # lui-même vit dans `PageResult.flagged`, pas dans le texte.
    body = UNCERTAIN_RE.sub(lambda mo: mo.group(1), body)
    return page_number, header, body.strip(), flagged


def _is_content_filter_block(exc: Exception) -> bool:
    """
    Distingue le cas précis « l'API a bloqué la réponse pour cette page à
    cause de sa politique de filtrage de contenu » (rencontré en pratique :
    une page d'un livre du 19e siècle, critique virulente du clergé et de
    la papauté -- polémique religieuse d'époque, pas un contenu généré
    problématique, mais suffisant pour déclencher un classifieur automatique
    sans le contexte du reste du livre) d'une AUTRE erreur 400 qui, elle,
    indiquerait un vrai problème (requête malformée, mauvais nom de modèle)
    à ne surtout pas masquer silencieusement.
    """
    body = getattr(exc, "body", None)
    message = ""
    if isinstance(body, dict):
        message = str(body.get("error", {}).get("message", ""))
    return "content filtering" in message.lower() or "content filtering" in str(exc).lower()


def _wrap_restricted_page(original_text: str, page_index: int, printed_page_number: str | None) -> str:
    """
    Entoure le texte original d'une page bloquée par le filtre de contenu
    d'un marqueur de début/fin détecté par `core/segment.py`
    (`RESTRICTED_MARKER_PREFIX`) -- ce qui empêche `translate_segments` de
    jamais l'envoyer au modèle de traduction (voir sa docstring) et permet
    de le repérer d'un coup d'œil dans le document final, avec le numéro de
    page exact pour aller vérifier la source.

    Les paragraphes internes de la page sont volontairement aplatis en un
    seul bloc continu (pas de ligne vide interne) : c'est ce qui garantit
    que toute la page reste UN SEUL segment après la segmentation, jamais
    coupée en plusieurs morceaux dont certains échapperaient au marqueur.
    """
    label = printed_page_number or str(page_index + 1)
    collapsed = re.sub(r"\s+", " ", original_text).strip()
    prefix = segment_mod.RESTRICTED_MARKER_PREFIX
    return (
        f"{prefix} — DÉBUT PAGE NON VÉRIFIÉE (page {label}) : bloquée par le filtre de "
        f"contenu de l'API, texte original (non corrigé) conservé ci-dessous.\n"
        f"{collapsed}\n"
        f"{prefix} — FIN PAGE NON VÉRIFIÉE (page {label})"
    )


def render_page_png(page) -> bytes:
    pix = page.get_pixmap(dpi=RENDER_DPI)
    return pix.tobytes("png")


def _transcribe_page(client, image_bytes: bytes, model: str) -> tuple[str, int, int]:
    image_data = base64.standard_b64encode(image_bytes).decode("utf-8")
    response = client.messages.create(
        model=model,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image_data}},
                {"type": "text", "text": "Transcribe this page."},
            ],
        }],
    )
    raw = "".join(block.text for block in response.content if block.type == "text")
    return raw, response.usage.input_tokens, response.usage.output_tokens


# --------------------------------------------------------------- cache disque
def _load_cache(cache_path: Path) -> dict[int, PageResult]:
    if not cache_path.exists():
        return {}
    cached: dict[int, PageResult] = {}
    try:
        for line in cache_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            data = json.loads(line)
            cached[data["page_index"]] = PageResult(**data)
    except (json.JSONDecodeError, KeyError, TypeError):
        # Cache corrompu (arrêt brutal en pleine écriture) : on repart de
        # zéro plutôt que de planter -- ça recoûtera les pages déjà faites,
        # mais ne bloque jamais la traduction pour ça.
        return {}
    return cached


def _append_cache(cache_path: Path, result: PageResult) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(result.__dict__, ensure_ascii=False) + "\n")


def extract_text_vision(
    pdf_path: Path,
    api_key: str,
    cache_path: Path,
    *,
    model: str = DEFAULT_MODEL,
    on_progress=None,       # (page_faite, total_pages, report_partiel)
    should_stop=None,
) -> tuple[str, VisionOcrReport]:
    """
    Retourne (texte au format `extract.extract_text()` -- pages jointes par
    \\f --, rapport détaillé page par page). Lève `VisionOcrError` si la clé
    est absente/invalide ou l'API injoignable, `Cancelled` si `should_stop()`
    passe à True en cours de route (les pages déjà faites restent dans le
    cache pour la reprise).
    """
    if not api_key:
        raise VisionOcrError(
            "Aucune clé API Anthropic renseignée -- Traduire X en a besoin (Réglages)."
        )

    try:
        import anthropic
    except ImportError as exc:
        raise VisionOcrError("Le module 'anthropic' n'est pas installé.") from exc
    try:
        import pymupdf
    except ImportError:
        import fitz as pymupdf  # PyMuPDF antérieur à 1.24, voir core/extract.py

    try:
        doc = pymupdf.open(str(pdf_path))
    except Exception as exc:  # noqa: BLE001 - fichier illisible, format inattendu...
        raise VisionOcrError(f"Impossible d'ouvrir le document : {exc}") from exc

    client = anthropic.Anthropic(api_key=api_key)
    total = doc.page_count
    cached = _load_cache(cache_path)

    report = VisionOcrReport(total_pages=total, model=model)
    pages_text: list[str] = []

    for i in range(total):
        if should_stop is not None and should_stop():
            raise Cancelled(f"Arrêt demandé après {i}/{total} pages (extraction vision).")

        existing = cached.get(i)
        if existing is not None:
            result = existing
        else:
            page = doc[i]
            original_text = page.get_text("text", sort=True)  # même appel que extract.py
            image_bytes = render_page_png(page)
            try:
                raw, in_tok, out_tok = _transcribe_page(client, image_bytes, model)
            except anthropic.AuthenticationError as exc:
                raise VisionOcrError("Clé API Anthropic invalide ou refusée.") from exc
            except anthropic.RateLimitError as exc:
                raise VisionOcrError(
                    "Limite de débit de l'API Anthropic atteinte -- réessayer plus tard "
                    "(les pages déjà transcrites sont conservées, la reprise repartira d'ici)."
                ) from exc
            except anthropic.APIConnectionError as exc:
                raise VisionOcrError("Connexion internet indisponible ou API injoignable.") from exc
            except anthropic.BadRequestError as exc:
                if not _is_content_filter_block(exc):
                    raise VisionOcrError(f"Erreur de l'API Anthropic : {exc}") from exc
                # Cas précis, pas une vraie panne : cette PAGE est bloquée par
                # le filtre de contenu d'Anthropic (rencontré en pratique sur
                # une polémique religieuse du 19e siècle, hors contexte pour
                # un classifieur page par page) -- l'API ne rend AUCUN texte
                # pour elle. Plutôt que de faire échouer tout le livre pour
                # une seule page, on garde le texte original (non corrigé)
                # de cette page-là et on continue -- rien n'est perdu, la
                # page est juste signalée dans le récapitulatif.
                result = PageResult(
                    page_index=i, printed_page_number=None, header=None,
                    original_text=original_text,
                    corrected_text=_wrap_restricted_page(original_text, i, None),
                    flagged=True, vision_failed=True, input_tokens=0, output_tokens=0,
                )
                _append_cache(cache_path, result)
                report.pages.append(result)
                report.total_input_tokens += result.input_tokens
                report.total_output_tokens += result.output_tokens
                pages_text.append(result.corrected_text)
                if on_progress is not None:
                    on_progress(i + 1, total, report)
                continue
            except anthropic.APIStatusError as exc:
                raise VisionOcrError(f"Erreur de l'API Anthropic : {exc}") from exc

            page_number, header, corrected, flagged = _parse_response(raw)
            result = PageResult(
                page_index=i, printed_page_number=page_number, header=header,
                original_text=original_text, corrected_text=corrected, flagged=flagged,
                input_tokens=in_tok, output_tokens=out_tok,
            )
            _append_cache(cache_path, result)

        report.pages.append(result)
        report.total_input_tokens += result.input_tokens
        report.total_output_tokens += result.output_tokens
        pages_text.append(result.corrected_text)

        if on_progress is not None:
            on_progress(i + 1, total, report)

    return "\f".join(pages_text), report


def _get_paddleocr_engine(lang: str):
    """
    Un seul moteur PaddleOCR par langue (voir `_paddleocr_engines` en tête
    de fichier). Import différé : `paddleocr`/`paddlepaddle` ne doivent
    être requis que si ce chemin est vraiment choisi.
    """
    if lang not in _paddleocr_engines:
        from paddleocr import PaddleOCR
        # enable_mkldnn=False : sans ce réglage, paddlepaddle 3.3.1 (moteur
        # "PIR") plante à l'inférence CPU -- NotImplementedError sur
        # ConvertPirAttribute2RuntimeAttribute. Vérifié réellement sur
        # cette machine avant d'écrire cette fonction (voir
        # OCR_VLM_COMPARATIF.md) : sans ce paramètre, l'OCR ne fonctionne
        # pas du tout sur cette configuration, ce n'est pas une prudence
        # excessive.
        _paddleocr_engines[lang] = PaddleOCR(use_textline_orientation=True, lang=lang, enable_mkldnn=False)
    return _paddleocr_engines[lang]


def _join_ocr_lines(texts: list[str]) -> str:
    """
    Rejoint les lignes détectées par PaddleOCR en un seul texte de page.

    Ne tente PAS de reconstruire les paragraphes ligne par ligne (repérage
    d'indentation, d'espacement vertical...) : `core/segment.py::detect_strategy`
    bascule déjà en stratégie « flux » (reconstruction par phrases, ~90
    mots) pour un texte sans repère fiable de paragraphe -- exactement le
    mécanisme déjà validé pour les .txt sans ligne vide et les EPUB (voir
    core/extract.py). Rien à réinventer ici : les lignes OCR, sans
    structure de paragraphe fiable non plus, sont un cas de plus pour ce
    même mécanisme existant.

    Seul traitement propre à l'OCR : recoller un mot coupé par un trait
    d'union en fin de ligne (artefact de mise en page/impression, pas du
    contenu réel) -- « Appro- » + « priate » -> « Appropriate », pas
    « Appro- priate ». Une ligne qui se termine par un trait d'union
    précédé d'une lettre est jointe SANS espace à la suivante ; sinon,
    jointe avec un espace comme d'habitude.
    """
    joined = ""
    for text in texts:
        text = text.strip()
        if not text:
            continue
        if joined and joined.endswith("-") and len(joined) >= 2 and joined[-2].isalpha():
            joined = joined[:-1] + text
        elif joined:
            joined = joined + " " + text
        else:
            joined = text
    return joined


def extract_text_paddleocr(
    pdf_path: Path,
    cache_path: Path,
    *,
    src_lang: str = "eng_Latn",
    on_progress=None,       # (page_faite, total_pages, report_partiel)
    should_stop=None,
) -> tuple[str, VisionOcrReport]:
    """
    Équivalent 100 % local et gratuit de `extract_text_vision` -- même
    principe (une page à la fois, rendue en image, reprise via le même
    cache disque, même forme de rapport `VisionOcrReport`/`PageResult`,
    donc réutilisable telle quelle par `VisionReviewDialog`), mais via
    PaddleOCR (licence Apache 2.0) au lieu de l'API Anthropic : aucune
    connexion internet requise après le premier téléchargement des poids
    du modèle, aucune clé API, aucun coût par page. Voir
    `OCR_VLM_COMPARATIF.md` pour le comparatif complet qui a mené à ce
    choix, et SPEC.md pour le détail de cette intégration.

    Validé sur un cas réel avant d'écrire cette fonction (comme
    `extract_text_vision` avant elle) : sur une page de « Hindu Magical
    Occultism Test.pdf » dont le calque de texte du PDF contenait du bruit
    (« MAGIOAL », « titis », « tbat », « wiU »), PaddleOCR a lu le corps
    du texte à 0,98-1,00 de confiance -- bien plus fiable que le calque
    d'origine. Un fragment d'en-tête tourné/minuscule a été correctement
    détecté avec une confiance basse (0,47-0,61), signalé (`flagged`)
    plutôt que faussement présenté comme fiable.

    Contrairement à `extract_text_vision`, aucun filtre de contenu à gérer
    (PaddleOCR ne refuse jamais de transcrire une page) -- `vision_failed`
    reste toujours False sur ce chemin.
    """
    try:
        import pymupdf
    except ImportError:
        import fitz as pymupdf  # PyMuPDF antérieur à 1.24, voir core/extract.py

    paddleocr_lang = languages_mod.paddleocr_lang(src_lang)
    if paddleocr_lang is None:
        raise VisionOcrError(
            f"PaddleOCR ne connaît pas de modèle pour la langue {src_lang} -- essayez la "
            "vision Anthropic pour cette langue (clé API requise), ou Traduire sans vision."
        )

    try:
        doc = pymupdf.open(str(pdf_path))
    except Exception as exc:  # noqa: BLE001 - fichier illisible, format inattendu...
        raise VisionOcrError(f"Impossible d'ouvrir le document : {exc}") from exc

    total = doc.page_count
    cached = _load_cache(cache_path)

    report = VisionOcrReport(total_pages=total, model="paddleocr")
    pages_text: list[str] = []
    engine = None  # chargé au premier vrai besoin seulement, voir plus bas

    for i in range(total):
        if should_stop is not None and should_stop():
            raise Cancelled(f"Arrêt demandé après {i}/{total} pages (extraction OCR locale).")

        existing = cached.get(i)
        if existing is not None:
            result = existing
        else:
            # Chargé ici, pas avant la boucle : un job entièrement repris
            # depuis le cache (toutes les pages déjà faites) ne doit pas
            # payer le chargement du modèle (plusieurs secondes, poids
            # chargés en mémoire) pour rien -- constaté par un test réel
            # avant cette correction, pas une supposition.
            if engine is None:
                try:
                    engine = _get_paddleocr_engine(paddleocr_lang)
                except ImportError as exc:
                    raise VisionOcrError(
                        "Les modules 'paddleocr'/'paddlepaddle' ne sont pas installés -- "
                        "voir requirements.txt."
                    ) from exc
                import cv2  # amené par paddleocr/paddlex, pas une dépendance ajoutée à part
                import numpy as np

            page = doc[i]
            original_text = page.get_text("text", sort=True)  # même appel que extract.py
            image_bytes = render_page_png(page)

            # PaddleOCR.predict() accepte un tableau numpy directement
            # (vérifié réellement sur cette machine) -- pas besoin d'écrire
            # un fichier temporaire pour chaque page.
            array = np.frombuffer(image_bytes, dtype="uint8")
            decoded = cv2.imdecode(array, cv2.IMREAD_COLOR)
            ocr_results = engine.predict(decoded)

            texts: list[str] = []
            scores: list[float] = []
            for ocr_page in ocr_results:
                texts.extend(ocr_page.get("rec_texts", []))
                scores.extend(ocr_page.get("rec_scores", []))

            corrected = _join_ocr_lines(texts)
            # Une page sans la moindre ligne détectée (page blanche, ou
            # échec de détection) est signalée plutôt que silencieusement
            # présentée comme "confiante" -- pas de score à comparer au
            # seuil dans ce cas, donc pas de raison de faire confiance.
            flagged = (not scores) or any(s < PADDLEOCR_CONFIDENCE_THRESHOLD for s in scores)

            result = PageResult(
                page_index=i, printed_page_number=None, header=None,
                original_text=original_text, corrected_text=corrected, flagged=flagged,
                input_tokens=0, output_tokens=0,  # gratuit -- pas d'appel API
            )
            _append_cache(cache_path, result)

        report.pages.append(result)
        pages_text.append(result.corrected_text)

        if on_progress is not None:
            on_progress(i + 1, total, report)

    return "\f".join(pages_text), report
