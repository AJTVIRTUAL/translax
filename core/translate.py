"""
Moteur de traduction : segments -> Markdown, écrit au fur et à mesure.

Reprise du moteur validé du pipeline d'origine (`translate_document.py`),
transformé en fonctions réutilisables avec un callback de progression à la
place des `print()`, pour que l'interface puisse s'y brancher.

Réglages : NE PAS CHANGER SANS VÉRIFIER LA SORTIE
  - `batch_size` reste implicitement à 1 (traitement séquentiel). Grouper
    plusieurs segments avec du padding fait dérailler NLLB-200-600M sur les
    segments courts (boucles « nous nous sommes nous nous... »).
  - `num_beams=4`. Le décodage glouton (num_beams=1) est plus rapide mais
    produit aussi des boucles de répétition.
  - `no_repeat_ngram_size=3` : garde-fou peu coûteux contre ces boucles.

Ces trois points ont été constatés en comparant les sorties, pas supposés.

Le `heartbeat` optionnel de `translate()`/`translate_segments()` (voir
`core/heartbeat.py` et `_HeartbeatCriteria` ci-dessous) n'influence AUCUN
de ces trois réglages ni la sortie produite -- vérifié par comparaison
directe, même segment, avec et sans lui branché.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

from . import heartbeat as heartbeat_mod
from . import languages as languages_mod
from . import state as state_mod

# Seuil au-delà duquel une entrée du cache HuggingFace est considérée comme un
# vrai modèle téléchargé plutôt qu'un stub (juste des fichiers refs/config,
# sans les poids) — rencontré en pratique avec nllb-200-3.3B, jamais réellement
# téléchargé sur cette machine mais dont le dossier de cache existait quand
# même. 50 Mo est très en dessous du plus petit poids réel de ces modèles
# (~2,5 Go pour le 600M) donc aucun risque de faux positif.
MIN_CACHED_BYTES = 50_000_000


@dataclass(frozen=True)
class ModelInfo:
    """
    Fiche d'un modèle NLLB-200 proposé dans le sélecteur.

    `size_gb` et `speed_note` sont soit mesurés sur cette machine, soit
    estimés par extrapolation du seul point mesuré (600M) — marqué
    `size_is_estimate=True` dans ce cas. Ne pas transformer ces estimations
    en promesse ferme dans l'UI : elles sont là pour donner un ordre de
    grandeur avant un téléchargement de plusieurs Go, pas un chiffre garanti.
    """
    key: str
    repo_id: str
    label: str
    size_gb: float
    size_is_estimate: bool
    speed_note: str
    description: str
    # "precise" (transformers, moteur d'origine validé) ou "fast" (moteur
    # CTranslate2, voir FastEngine plus bas). Défaut à "precise" pour ne pas
    # avoir à toucher les trois entrées existantes.
    engine: str = "precise"


# Les trois profils "Précis" exposés dans TRANSLAX depuis le début. Le 1.3B
# pointe vers la variante DISTILLÉE (pas la version dense
# "facebook/nllb-200-1.3B") : c'est le compromis rapidité/qualité voulu pour
# ce créneau, cohérent avec le 600M distillé déjà validé.
MODEL_INFO: dict[str, ModelInfo] = {
    "600M": ModelInfo(
        key="600M",
        repo_id="facebook/nllb-200-distilled-600M",
        label="600M — Rapide",
        size_gb=2.5,
        size_is_estimate=False,  # mesuré : 2,46 Go de poids réels dans le cache
        speed_note="mesuré : 10-70 s/segment selon la longueur du texte",
        description="Le plus rapide des trois moteurs « Précis ». Bon choix pour de gros volumes (livres entiers).",
    ),
    "1.3B": ModelInfo(
        key="1.3B",
        repo_id="facebook/nllb-200-distilled-1.3B",
        label="1.3B distillé — Équilibré",
        size_gb=5.3,
        size_is_estimate=True,  # extrapolé depuis le 600M, jamais mesuré ici
        speed_note="estimé ~2 à 2,5× plus lent que 600M (non mesuré sur cette machine)",
        description="Meilleure qualité que 600M, pour un temps encore raisonnable.",
    ),
    "3.3B": ModelInfo(
        key="3.3B",
        repo_id="facebook/nllb-200-3.3B",
        label="3.3B — Qualité maximale",
        size_gb=13.5,
        size_is_estimate=True,  # extrapolé depuis le 600M, jamais mesuré ici
        speed_note="estimé ~5 à 6× plus lent que 600M (non mesuré sur cette machine)",
        description="La meilleure qualité des trois « Précis ». Réservé aux documents courts : bien plus lent.",
    ),
    # Quatrième profil, ajouté le 25/08/2026 : mêmes poids NLLB-200-600M,
    # mais servis par CTranslate2 (moteur d'inférence C++ dédié, quantifié
    # int8) au lieu de transformers. Mesuré sur cette machine (Ryzen 7
    # 4700U, CPU seul) : 13,2× plus rapide que « 600M — Rapide » sur une
    # phrase de référence, ET 6,5 à 7× plus rapide sur un jeu de 4 phrases
    # variées (titres, chiffres, phrases longues) -- voir test_translate.py.
    # Sortie quasi identique au moteur « Précis » : 3 phrases sur 4
    # rigoureusement identiques dans ce même test ; la 4e ne diffère que
    # par un mot (singulier/pluriel d'un synonyme), sens inchangé -- écart
    # attendu et documenté de la quantification int8, pas un bug.
    "600M-ct2": ModelInfo(
        key="600M-ct2",
        repo_id="facebook/nllb-200-distilled-600M",
        label="600M — Turbo (CTranslate2)",
        size_gb=0.6,
        size_is_estimate=False,  # mesuré : 622 596 105 octets pour model.bin
        speed_note="mesuré sur cette machine : environ 13× plus rapide que « 600M — Rapide »",
        description=(
            "Le même 600M, servi par un moteur d'inférence optimisé (quantification int8) "
            "au lieu de transformers. Sortie quasi identique, très nettement plus rapide sur "
            "CPU. Nécessite une conversion locale unique (quelques minutes) au premier lancement."
        ),
        engine="fast",
    ),
    # Cinquième profil, ajouté le 25/08/2026 : licence commerciale propre
    # (CC-BY 4.0, attribution requise -- pas CC-BY-NC comme les 4 profils
    # NLLB ci-dessus), en vue de la commercialisation de TRANSLAX. Un modèle
    # PAR PAIRE de langues (Helsinki-NLP/opus-mt-{src}-{tgt}), pas un seul
    # modèle multilingue comme NLLB -- `repo_id` est donc un GABARIT, pas un
    # identifiant utilisable tel quel (voir OpusMtEngine, qui le complète
    # avec les langues réellement choisies dans le sélecteur). Mesuré sur
    # cette machine pour eng_Latn -> fra_Latn : sortie quasi identique à
    # « 600M — Rapide » (voir test_translate.py), 604,6 Mo sur le disque.
    "opus-mt": ModelInfo(
        key="opus-mt",
        repo_id="Helsinki-NLP/opus-mt-{src}-{tgt}",
        label="OPUS-MT — Licence commerciale (Helsinki-NLP)",
        size_gb=0.6,
        size_is_estimate=True,  # mesuré pour eng_Latn->fra_Latn seulement ; varie par paire
        speed_note="mesuré (eng_Latn->fra_Latn) : comparable à « 600M — Rapide »",
        description=(
            "Un modèle dédié à la paire de langues choisie (pas un seul modèle "
            "multilingue) : licence CC-BY 4.0, usage commercial permis avec attribution. "
            "Couverture non garantie pour toutes les paires : message clair si la paire "
            "choisie n'a pas de modèle publié par Helsinki-NLP."
        ),
        engine="opus-mt",
    ),
    # Sixième profil, ajouté le 25/08/2026 : licence Apache 2.0 (usage
    # commercial sans restriction), pour élargir la couverture de langues
    # au-delà de ce qu'OPUS-MT publie par paire -- UN seul modèle pour
    # ~419 langues (comme NLLB), mais commercialement propre. `repo_id` EST
    # fixe ici (contrairement à opus-mt) : la langue cible se choisit par un
    # jeton préfixé au texte (voir MadladEngine), pas par un modèle séparé.
    # size_gb mesuré via l'API Hub (métadonnées des fichiers), PAS un
    # téléchargement complet effectué en développement -- poids nettement
    # plus lourds que tout autre profil de TRANSLAX (11,8 Go, à comparer aux
    # 13,5 Go du 3.3B ci-dessus), et jamais mesurés en vitesse réelle sur
    # cette machine (voir SPEC.md pour le raisonnement de cette limite
    # assumée).
    "madlad-3b": ModelInfo(
        key="madlad-3b",
        repo_id="google/madlad400-3b-mt",
        label="MADLAD-400 3B — Licence commerciale (Google)",
        size_gb=11.8,
        size_is_estimate=False,  # mesuré via l'API Hub (files_metadata), pas une extrapolation
        speed_note="non mesuré sur cette machine -- 3 milliards de paramètres, attendre un net ralentissement sur CPU sans GPU",
        description=(
            "Un seul modèle pour environ 419 langues (comme NLLB), licence Apache 2.0 : usage "
            "commercial sans restriction. Poids nettement plus lourds que les autres profils -- "
            "à valider avant un usage réel soutenu sur une machine sans GPU."
        ),
        engine="madlad",
    ),
}
MODEL_MAP = {key: info.repo_id for key, info in MODEL_INFO.items()}
DEFAULT_MODEL_KEY = "600M"


def is_model_cached(repo_id: str) -> bool:
    """
    True si `repo_id` est déjà téléchargé localement (pas juste un stub de
    cache vide). Sert à décider si l'UI doit avertir avant de lancer un
    téléchargement de plusieurs Go.
    """
    size = cached_size_bytes(repo_id)
    return size is not None and size >= MIN_CACHED_BYTES


def cached_size_bytes(repo_id: str) -> int | None:
    """Taille réelle sur disque de `repo_id` dans le cache HuggingFace, ou
    None s'il n'y figure pas du tout."""
    try:
        from huggingface_hub import scan_cache_dir
        cache_info = scan_cache_dir()
    except Exception:
        # Cache introuvable/illisible : on ne bloque pas l'appli pour ça,
        # on suppose juste qu'on ne sait pas et on laisse l'estimation faire
        # office d'indication.
        return None
    for repo in cache_info.repos:
        if repo.repo_id == repo_id:
            return repo.size_on_disk
    return None


def ctranslate2_model_dir(repo_id: str) -> Path:
    """
    Dossier où vit le modèle `repo_id` une fois converti au format
    CTranslate2 (voir FastEngine) -- séparé du cache HuggingFace (qui
    contient les poids d'origine, utilisés une seule fois pour produire
    cette conversion), dans le même dossier de données que les autres
    réglages de l'appli (voir `core/settings.py`).
    """
    from . import settings as settings_mod
    safe_name = repo_id.replace("/", "__")
    return settings_mod.app_data_dir() / "ctranslate2-models" / safe_name


def is_ctranslate2_ready(repo_id: str) -> bool:
    """
    True si `repo_id` a déjà été converti au format CTranslate2 (conversion
    locale, CPU, faite une seule fois -- voir FastEngine.load()).

    `model.bin` est le fichier de poids que produit toujours une conversion
    CTranslate2 aboutie, quel que soit le modèle d'origine (vérifié sur une
    conversion réelle du 600M) : un dossier présent mais sans ce fichier
    (conversion interrompue en cours de route) n'est pas considéré prêt.
    """
    return (ctranslate2_model_dir(repo_id) / "model.bin").exists()


def opus_mt_repo_id(src_lang: str, tgt_lang: str) -> str | None:
    """
    Nom du modèle Helsinki-NLP pour cette paire de langues (codes FLORES,
    comme partout ailleurs dans TRANSLAX -- convertis ici en ISO 639-1, voir
    `core/languages.py::iso2`), ou None si l'une des deux langues n'a pas
    de correspondance connue. Ne devine jamais si le modèle EXISTE
    vraiment sur le Hub -- juste le nom qu'il aurait s'il existe (voir
    OpusMtEngine.load(), qui gère l'absence réelle).
    """
    src = languages_mod.iso2(src_lang)
    tgt = languages_mod.iso2(tgt_lang)
    if not src or not tgt:
        return None
    return MODEL_INFO["opus-mt"].repo_id.format(src=src, tgt=tgt)


def is_model_ready(model_key: str, src_lang: str | None = None, tgt_lang: str | None = None) -> bool:
    """
    True si `model_key` est utilisable sans téléchargement/conversion
    supplémentaire -- quel que soit son moteur. Fait le bon choix entre
    `is_model_cached` (cache HuggingFace, moteur « precise »),
    `is_ctranslate2_ready` (dossier converti, moteur « fast ») et le nom de
    modèle résolu pour la paire choisie (moteur « opus-mt », qui a besoin de
    `src_lang`/`tgt_lang` puisqu'il n'a pas de `repo_id` fixe) : c'est cette
    fonction que l'interface doit appeler, jamais les fonctions plus bas
    directement.
    """
    info = MODEL_INFO[model_key]
    if info.engine == "fast":
        return is_ctranslate2_ready(info.repo_id)
    if info.engine == "opus-mt":
        if not src_lang or not tgt_lang:
            return False  # rien à vérifier sans savoir quelle paire est choisie
        repo_id = opus_mt_repo_id(src_lang, tgt_lang)
        return repo_id is not None and is_model_cached(repo_id)
    return is_model_cached(info.repo_id)


MAX_INPUT_TOKENS = 512
DEFAULT_NUM_BEAMS = 4
NO_REPEAT_NGRAM_SIZE = 3

# Lissage exponentiel de la vitesse. Le premier segment inclut la chauffe du
# modèle (mesuré : 71 s puis 54 s puis 35 s sur les mêmes segments) ; une
# moyenne cumulée resterait pessimiste pendant des centaines de segments.
RATE_SMOOTHING = 0.3


@dataclass
class Progress:
    """Une mise à jour de progression, émise après chaque segment traduit."""
    done: int              # segments écrits au total (reprise comprise)
    total: int             # segments du document
    rate: float            # secondes par segment, lissées (sert à l'ETA)
    eta: float             # secondes restantes estimées
    seg_type: str          # title | heading | bullet | paragraph
    source_text: str
    translated_text: str
    average_rate: float = 0.0   # moyenne brute depuis le début du run

    @property
    def percent(self) -> float:
        return 100.0 * self.done / self.total if self.total else 0.0


class Cancelled(Exception):
    """Levée quand l'utilisateur demande l'arrêt en cours de traduction."""


class OpusMtUnavailable(Exception):
    """
    Levée par OpusMtEngine.load() quand Helsinki-NLP n'a publié aucun
    modèle pour la paire de langues choisie (langue absente de la table
    ISO 639-1, ou modèle absent du Hub) -- OPUS-MT ne couvre pas toutes les
    combinaisons, contrairement à NLLB qui gère ses 200 langues avec un
    seul modèle. Message pensé pour s'afficher tel quel dans la boîte
    d'erreur de l'interface (voir ui/main_window.py), pas une trace brute.
    """


class MadladUnavailable(Exception):
    """
    Levée par MadladEngine.load() quand la langue CIBLE choisie n'a pas de
    correspondance dans `core/languages.py::FLORES_TO_ISO2`. À ne pas lire
    comme "Google ne prend pas en charge cette langue" -- MADLAD-400 couvre
    en réalité ~419 langues, bien plus que les 18 exposées par TRANSLAX :
    cette erreur signale une limite de la table de correspondance de
    TRANSLAX, pas du modèle lui-même.
    """


def render_markdown(seg_type: str, text: str) -> str:
    if seg_type == "title":
        return f"# {text}\n"
    if seg_type == "heading":
        return f"## {text}\n"
    if seg_type == "bullet":
        return f"- {text}\n"
    if seg_type == "restricted":
        # Bloc jamais traduit (voir translate_segments et core/segment.py) --
        # rendu en citation Markdown pour rester visuellement reconnaissable
        # dans le document final, où qu'il tombe.
        return "\n".join(f"> {line}" for line in text.split("\n")) + "\n"
    return f"{text}\n"


class _HeartbeatCriteria:
    """
    Ne fait JAMAIS arrêter la génération (renvoie toujours False) : sert
    uniquement à horodater qu'un pas de décodage vient d'avoir lieu, via
    `heartbeat.beat()`.

    Branché comme `StoppingCriteria` plutôt que comme `streamer` : un
    `streamer` serait l'outil habituel pour ce genre de besoin, mais cette
    version de transformers lève explicitement `NotImplementedError` dès que
    `num_beams > 1` ("`streamer` cannot be used with beam search (yet!)") --
    or ce moteur traduit toujours avec `num_beams=4`. Un `StoppingCriteria`,
    lui, est appelé à CHAQUE pas de décodage quelle que soit la stratégie,
    beam search comprise (vérifié en lisant `_beam_search` dans
    `transformers/generation/utils.py`, et confirmé empiriquement : même
    traduction mot pour mot, avec et sans cette classe branchée, sur le même
    segment -- 27 pas observés pour une phrase d'une quinzaine de mots).
    """

    def __init__(self, heartbeat: heartbeat_mod.Heartbeat) -> None:
        self._heartbeat = heartbeat

    def __call__(self, input_ids, scores, **kwargs):
        import torch

        self._heartbeat.beat()
        return torch.zeros(input_ids.shape[0], dtype=torch.bool, device=input_ids.device)


class PreciseEngine:
    """
    Moteur « Précis » : transformers + NLLB-200, un segment à la fois.

    C'est le moteur qui a produit les traductions déjà validées du projet.
    Lent sur CPU (~10-17 s/segment sur une machine sans GPU) mais fiable.
    """

    name = "precise"

    def __init__(
        self,
        model_key: str = DEFAULT_MODEL_KEY,
        src_lang: str = "eng_Latn",
        tgt_lang: str = "fra_Latn",
        num_beams: int = DEFAULT_NUM_BEAMS,
        threads: int | None = None,
    ):
        self.model_id = MODEL_MAP.get(model_key, model_key)
        self.src_lang = src_lang
        self.tgt_lang = tgt_lang
        self.num_beams = num_beams
        self.threads = threads or os.cpu_count()
        self.device = "cpu"
        self._tokenizer = None
        self._model = None
        self._tgt_id = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def load(self, on_status=None) -> None:
        """Charge modèle et tokenizer (plusieurs secondes, voire minutes au
        tout premier lancement si le modèle doit être téléchargé)."""
        import torch
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
        from transformers.utils import logging as hf_logging

        # NLLB embarque max_length=200 dans sa generation_config ; comme on
        # passe max_new_tokens (qui a la priorité et donne la bonne sortie),
        # transformers émet un avertissement à CHAQUE segment. On coupe les
        # warnings : les vraies erreurs restent affichées.
        hf_logging.set_verbosity_error()

        if self.threads:
            torch.set_num_threads(self.threads)
        self.device = "cuda" if torch.cuda.is_available() else "cpu"

        if on_status:
            on_status(f"Chargement du modèle {self.model_id} sur {self.device}…")

        self._tokenizer = AutoTokenizer.from_pretrained(self.model_id, src_lang=self.src_lang)
        self._model = AutoModelForSeq2SeqLM.from_pretrained(self.model_id)
        self._model.to(self.device)
        self._model.eval()
        self._tgt_id = self._tokenizer.convert_tokens_to_ids(self.tgt_lang)

        if on_status:
            on_status("Modèle chargé.")

    def translate(self, text: str, *, heartbeat: heartbeat_mod.Heartbeat | None = None) -> str:
        import torch

        if not self.loaded:
            raise RuntimeError("Moteur non chargé : appeler load() d'abord.")

        inputs = self._tokenizer(
            text,
            return_tensors="pt",
            truncation=True,
            max_length=MAX_INPUT_TOKENS,
        ).to(self.device)
        in_len = inputs["input_ids"].shape[1]

        stopping_criteria = None
        if heartbeat is not None:
            from transformers import StoppingCriteriaList
            stopping_criteria = StoppingCriteriaList([_HeartbeatCriteria(heartbeat)])

        with torch.no_grad():
            tokens = self._model.generate(
                **inputs,
                forced_bos_token_id=self._tgt_id,
                max_new_tokens=min(MAX_INPUT_TOKENS, int(in_len * 2) + 20),
                num_beams=self.num_beams,
                no_repeat_ngram_size=NO_REPEAT_NGRAM_SIZE,
                stopping_criteria=stopping_criteria,
            )
        return self._tokenizer.batch_decode(tokens, skip_special_tokens=True)[0]

    def unload(self) -> None:
        self._tokenizer = None
        self._model = None
        self._tgt_id = None


class FastEngine:
    """
    Moteur « Turbo » : mêmes poids NLLB-200 que PreciseEngine, mais servis
    par CTranslate2 (moteur d'inférence C++ dédié, quantification int8) au
    lieu de transformers/PyTorch en mode eager. Ajouté le 25/08/2026, à la
    demande explicite de l'utilisateur, après avoir vérifié sur CETTE
    machine (Ryzen 7 4700U, CPU seul, sans GPU) :
      - 13,2× plus rapide que PreciseEngine sur une phrase de référence,
        6,5 à 7× sur un jeu de 4 phrases variées (titres, chiffres, phrases
        longues) -- voir test_translate.py.
      - Sortie quasi identique : 3 phrases sur 4 rigoureusement identiques
        dans ce même test ; la 4e ne change qu'un mot (singulier/pluriel
        d'un synonyme), sens inchangé -- écart attendu de la quantification
        int8 (précision numérique réduite), pas un bug de cette classe.

    Le tokenizer reste le même `AutoTokenizer` HuggingFace que
    PreciseEngine : seul le moteur qui exécute le modèle change.
    `translate_batch` de CTranslate2 attend des CHAÎNES de tokens (pas des
    identifiants numériques comme `generate()`), d'où les allers-retours
    `encode`/`convert_ids_to_tokens` et `convert_tokens_to_ids`/`decode`.

    Limite connue et acceptée, PAS contournable : le `callback` par jeton
    de CTranslate2 n'est appelé, d'après la docstring réelle du paquet
    installé (4.8.1), "for each generated token when beam_size is 1". Ce
    moteur utilise `beam_size=4` comme PreciseEngine (même raison : le
    décodage glouton boucle sur les segments courts). Le battement Reboost
    de ce moteur est donc par SEGMENT (avant et après chaque appel), pas
    par jeton comme `_HeartbeatCriteria` pour PreciseEngine -- un blocage
    en cours de génération d'un très long segment serait donc détecté un
    peu plus tard qu'avec le moteur « Précis », mais reste détecté.
    """

    name = "fast"

    def __init__(
        self,
        model_key: str = DEFAULT_MODEL_KEY,
        src_lang: str = "eng_Latn",
        tgt_lang: str = "fra_Latn",
        num_beams: int = DEFAULT_NUM_BEAMS,
        threads: int | None = None,
    ):
        self.model_id = MODEL_MAP.get(model_key, model_key)
        self.src_lang = src_lang
        self.tgt_lang = tgt_lang
        self.num_beams = num_beams
        self.threads = threads or os.cpu_count()
        self._tokenizer = None
        self._translator = None

    @property
    def loaded(self) -> bool:
        return self._translator is not None

    def load(self, on_status=None) -> None:
        """
        Convertit le modèle au format CTranslate2 si ce n'est pas déjà fait
        (opération locale, CPU, une seule fois -- mesuré : 77 s pour le
        600M sur cette machine, poids d'origine déjà en cache), puis charge
        tokenizer et moteur. Un import différé (comme pour PreciseEngine) :
        `ctranslate2` ne doit être requis que si ce moteur est vraiment
        choisi.
        """
        import ctranslate2
        from transformers import AutoTokenizer

        model_dir = ctranslate2_model_dir(self.model_id)
        if not is_ctranslate2_ready(self.model_id):
            if on_status:
                on_status(
                    f"Conversion de {self.model_id} au format CTranslate2 "
                    "(une seule fois, quelques minutes)…"
                )
            model_dir.parent.mkdir(parents=True, exist_ok=True)
            from ctranslate2.converters import TransformersConverter
            converter = TransformersConverter(self.model_id)
            converter.convert(str(model_dir), quantization="int8", force=False)

        if on_status:
            on_status(f"Chargement du modèle {self.model_id} (CTranslate2)…")

        self._tokenizer = AutoTokenizer.from_pretrained(self.model_id, src_lang=self.src_lang)
        self._translator = ctranslate2.Translator(
            str(model_dir),
            device="cpu",
            compute_type="int8",
            intra_threads=self.threads,
        )

        if on_status:
            on_status("Modèle chargé.")

    def translate(self, text: str, *, heartbeat: heartbeat_mod.Heartbeat | None = None) -> str:
        if not self.loaded:
            raise RuntimeError("Moteur non chargé : appeler load() d'abord.")

        if heartbeat is not None:
            heartbeat.beat()  # battement par segment, pas par jeton -- voir la docstring de la classe

        source_ids = self._tokenizer.encode(text, truncation=True, max_length=MAX_INPUT_TOKENS)
        source_tokens = self._tokenizer.convert_ids_to_tokens(source_ids)
        in_len = len(source_tokens)

        results = self._translator.translate_batch(
            [source_tokens],
            target_prefix=[[self.tgt_lang]],
            beam_size=self.num_beams,
            no_repeat_ngram_size=NO_REPEAT_NGRAM_SIZE,
            max_decoding_length=min(MAX_INPUT_TOKENS, int(in_len * 2) + 20),
        )

        if heartbeat is not None:
            heartbeat.beat()

        # Le premier jeton de l'hypothèse est le préfixe de langue cible
        # forcé par `target_prefix` (l'équivalent CTranslate2 du
        # `forced_bos_token_id` de PreciseEngine) : on le saute, comme
        # `skip_special_tokens=True` le fait implicitement côté PreciseEngine.
        target_tokens = results[0].hypotheses[0][1:]
        target_ids = self._tokenizer.convert_tokens_to_ids(target_tokens)
        return self._tokenizer.decode(target_ids, skip_special_tokens=True)

    def unload(self) -> None:
        self._tokenizer = None
        self._translator = None


class OpusMtEngine:
    """
    Moteur « OPUS-MT » : un modèle Helsinki-NLP (architecture MarianMT)
    dédié à UNE SEULE paire de langues, ajouté le 25/08/2026 en vue de la
    commercialisation de TRANSLAX -- licence CC-BY 4.0 (attribution requise,
    usage commercial permis), contrairement aux quatre profils NLLB
    ci-dessus (CC-BY-NC 4.0, usage commercial interdit, gardés ici
    uniquement pour l'usage personnel de l'auteur -- voir SPEC.md). C'est
    la base technique de LibreTranslate, l'alternative open-source à DeepL
    qui vend déjà un service dessus : choix éprouvé pour ce cas d'usage,
    pas un pari.

    Différence structurelle avec PreciseEngine/FastEngine, pas un détail :
    NLLB est UN modèle qui sait traduire entre 200 langues via un jeton de
    langue cible forcé (`forced_bos_token_id`) ; OPUS-MT n'a pas cette
    notion -- chaque modèle ne sait traduire QUE dans le sens pour lequel
    il a été entraîné (`opus-mt-en-fr` ne fait que anglais -> français).
    `self.model_id` est donc résolu au moment de la construction, à partir
    des langues réellement choisies (voir `translate.opus_mt_repo_id`), pas
    un `repo_id` fixe comme les autres moteurs. Toutes les paires ne sont
    pas couvertes : `load()` lève `OpusMtUnavailable` avec un message clair
    si Helsinki-NLP n'a rien publié pour cette paire précise, plutôt que de
    laisser remonter une erreur HuggingFace brute.

    Vérifié réellement (pas juste "ça devrait marcher") sur eng_Latn ->
    fra_Latn : chargement direct via `AutoTokenizer`/`AutoModelForSeq2SeqLM`
    (résolus en `MarianTokenizer`/`MarianMTModel`), AUCUN jeton de langue à
    forcer (juste tokenizer -> generate -> decode), sortie quasi identique
    à « 600M — Rapide » sur les mêmes phrases -- voir test_translate.py.
    """

    name = "opus-mt"

    def __init__(
        self,
        model_key: str = "opus-mt",  # accepté pour la même signature que les autres moteurs (voir pipeline.py), pas utilisé : le modèle dépend de src_lang/tgt_lang, jamais d'un model_key
        src_lang: str = "eng_Latn",
        tgt_lang: str = "fra_Latn",
        num_beams: int = DEFAULT_NUM_BEAMS,
        threads: int | None = None,
    ):
        self.src_lang = src_lang
        self.tgt_lang = tgt_lang
        self.num_beams = num_beams
        self.threads = threads or os.cpu_count()
        self.model_id = opus_mt_repo_id(src_lang, tgt_lang)
        self._tokenizer = None
        self._model = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def load(self, on_status=None) -> None:
        import torch
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
        from transformers.utils import logging as hf_logging

        hf_logging.set_verbosity_error()

        if self.model_id is None:
            raise OpusMtUnavailable(
                f"OPUS-MT ne connaît pas la paire {self.src_lang} -> {self.tgt_lang} "
                "(langue absente de la table de correspondance ISO 639-1 -- voir "
                "core/languages.py). Essayez un autre moteur pour cette paire."
            )

        if self.threads:
            torch.set_num_threads(self.threads)
        self.device = "cuda" if torch.cuda.is_available() else "cpu"

        if on_status:
            on_status(f"Chargement du modèle {self.model_id} sur {self.device}…")

        try:
            self._tokenizer = AutoTokenizer.from_pretrained(self.model_id)
            self._model = AutoModelForSeq2SeqLM.from_pretrained(self.model_id)
        except OSError as exc:
            # Le Hub renvoie une 404 (via huggingface_hub, enveloppée par
            # transformers en OSError) quand Helsinki-NLP n'a tout
            # simplement pas publié cette paire -- traduit en message
            # actionnable plutôt que de laisser remonter la trace brute.
            raise OpusMtUnavailable(
                f"Aucun modèle OPUS-MT publié pour {self.src_lang} -> {self.tgt_lang} "
                f"({self.model_id}). Essayez un autre moteur pour cette paire."
            ) from exc

        self._model.to(self.device)
        self._model.eval()

        if on_status:
            on_status("Modèle chargé.")

    def translate(self, text: str, *, heartbeat: heartbeat_mod.Heartbeat | None = None) -> str:
        import torch

        if not self.loaded:
            raise RuntimeError("Moteur non chargé : appeler load() d'abord.")

        inputs = self._tokenizer(
            text,
            return_tensors="pt",
            truncation=True,
            max_length=MAX_INPUT_TOKENS,
        ).to(self.device)
        in_len = inputs["input_ids"].shape[1]

        # Même mécanisme de pouls que PreciseEngine (voir _HeartbeatCriteria)
        # -- OPUS-MT reste un modèle transformers/PyTorch classique, donc le
        # même StoppingCriteria fonctionne à l'identique, pas de limite par
        # segment comme pour FastEngine/CTranslate2.
        stopping_criteria = None
        if heartbeat is not None:
            from transformers import StoppingCriteriaList
            stopping_criteria = StoppingCriteriaList([_HeartbeatCriteria(heartbeat)])

        with torch.no_grad():
            tokens = self._model.generate(
                **inputs,
                max_new_tokens=min(MAX_INPUT_TOKENS, int(in_len * 2) + 20),
                num_beams=self.num_beams,
                no_repeat_ngram_size=NO_REPEAT_NGRAM_SIZE,
                stopping_criteria=stopping_criteria,
            )
        return self._tokenizer.batch_decode(tokens, skip_special_tokens=True)[0]

    def unload(self) -> None:
        self._tokenizer = None
        self._model = None


class MadladEngine:
    """
    Moteur « MADLAD-400 » (Google, architecture T5) -- licence Apache 2.0,
    usage commercial sans restriction, ajouté le 25/08/2026 pour élargir la
    couverture de langues au-delà de ce qu'OPUS-MT publie par paire : UN
    seul modèle pour ~419 langues (comme NLLB), commercialement propre.

    Troisième mécanisme différent, pas une redite d'OpusMtEngine ni de
    PreciseEngine : pas de `forced_bos_token_id` (NLLB), pas de modèle dédié
    par paire (OPUS-MT) -- la langue CIBLE est un jeton `<2xx>` PRÉFIXÉ AU
    TEXTE SOURCE lui-même (ex. "<2fr> Hello world." pour une sortie en
    français). Vérifié réellement (pas supposé) : `<2fr>` est une seule
    entrée de vocabulaire atomique (id 46, jamais éclatée en "<", "2",
    "fr", ">"), pas une astuce de prompt fragile qui dépendrait de la
    tokenisation. Aucun jeton de langue SOURCE n'est nécessaire
    (contrairement à NLLB, dont le tokenizer a besoin de `src_lang` dès sa
    construction) : le modèle détermine la langue source lui-même.

    Réutilise `languages_mod.iso2` -- la même table que OpusMtEngine -- pour
    le jeton cible : `MadladUnavailable` si la langue choisie n'y figure
    pas (limite de la table de TRANSLAX, pas du modèle : voir la docstring
    de cette exception).

    **Poids non téléchargés en développement, volontairement** : le plus
    petit modèle (3B) pèse ~11,8 Go de poids réels (mesuré via l'API Hub,
    sans téléchargement complet) -- un téléchargement de cette taille n'a
    pas été déclenché juste pour valider ce moteur, laissé à la
    confirmation normale de l'interface avant tout téléchargement de
    plusieurs Go. Le mécanisme de tokenisation/prompt ci-dessus, lui, EST
    vérifié réellement (tokenizer seul, téléchargement léger). Sur un CPU
    sans GPU, attendre un ralentissement très important par rapport aux
    profils 600M/1.3B -- non mesuré, mais 3 milliards de paramètres contre
    600 millions ne laisse guère de doute sur le sens de cet écart.
    """

    name = "madlad"

    def __init__(
        self,
        model_key: str = "madlad-3b",
        src_lang: str = "eng_Latn",
        tgt_lang: str = "fra_Latn",
        num_beams: int = DEFAULT_NUM_BEAMS,
        threads: int | None = None,
    ):
        self.model_id = MODEL_MAP.get(model_key, model_key)
        self.src_lang = src_lang  # non utilisé par le modèle (voir docstring), gardé pour les messages d'erreur/journal
        self.tgt_lang = tgt_lang
        self._tgt_iso = languages_mod.iso2(tgt_lang)
        self.num_beams = num_beams
        self.threads = threads or os.cpu_count()
        self._tokenizer = None
        self._model = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def load(self, on_status=None) -> None:
        import torch
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
        from transformers.utils import logging as hf_logging

        hf_logging.set_verbosity_error()

        if self._tgt_iso is None:
            raise MadladUnavailable(
                f"MADLAD-400 : la langue cible {self.tgt_lang} n'a pas de correspondance dans "
                "la table de TRANSLAX (core/languages.py) -- le modèle couvre pourtant bien plus "
                "de langues que celles exposées ici. Essayez un autre moteur pour cette langue."
            )

        if self.threads:
            torch.set_num_threads(self.threads)
        self.device = "cuda" if torch.cuda.is_available() else "cpu"

        if on_status:
            on_status(
                f"Chargement du modèle {self.model_id} sur {self.device} "
                "(poids volumineux, ~12 Go -- patience au premier lancement)…"
            )

        self._tokenizer = AutoTokenizer.from_pretrained(self.model_id)
        self._model = AutoModelForSeq2SeqLM.from_pretrained(self.model_id)
        self._model.to(self.device)
        self._model.eval()

        if on_status:
            on_status("Modèle chargé.")

    def translate(self, text: str, *, heartbeat: heartbeat_mod.Heartbeat | None = None) -> str:
        import torch

        if not self.loaded:
            raise RuntimeError("Moteur non chargé : appeler load() d'abord.")

        prompted = f"<2{self._tgt_iso}> {text}"
        inputs = self._tokenizer(
            prompted,
            return_tensors="pt",
            truncation=True,
            max_length=MAX_INPUT_TOKENS,
        ).to(self.device)
        in_len = inputs["input_ids"].shape[1]

        # Même mécanisme de pouls que PreciseEngine/OpusMtEngine -- MADLAD
        # reste un modèle transformers/PyTorch classique (T5), le même
        # StoppingCriteria fonctionne à l'identique.
        stopping_criteria = None
        if heartbeat is not None:
            from transformers import StoppingCriteriaList
            stopping_criteria = StoppingCriteriaList([_HeartbeatCriteria(heartbeat)])

        with torch.no_grad():
            tokens = self._model.generate(
                **inputs,
                max_new_tokens=min(MAX_INPUT_TOKENS, int(in_len * 2) + 20),
                num_beams=self.num_beams,
                no_repeat_ngram_size=NO_REPEAT_NGRAM_SIZE,
                stopping_criteria=stopping_criteria,
            )
        return self._tokenizer.batch_decode(tokens, skip_special_tokens=True)[0]

    def unload(self) -> None:
        self._tokenizer = None
        self._model = None


def translate_segments(
    segments: list[dict],
    out_path: Path,
    engine: "PreciseEngine | FastEngine | OpusMtEngine | MadladEngine",
    *,
    start_index: int = 0,
    job_state: state_mod.JobState | None = None,
    on_progress=None,
    should_stop=None,
    heartbeat: heartbeat_mod.Heartbeat | None = None,
) -> int:
    """
    Traduit `segments[start_index:]` et écrit le Markdown au fil de l'eau.

    Chaque segment est écrit puis `flush()` immédiatement : sans ça, le
    fichier de sortie reste vide pendant des heures alors que le travail
    avance (bug rencontré et corrigé sur le pipeline d'origine).

    Retourne le nombre total de segments écrits (reprise comprise). Lève
    `Cancelled` si `should_stop()` passe à True — les segments déjà écrits
    restent sur le disque et l'état est sauvegardé pour la reprise.

    Un segment de type "restricted" (voir `core/segment.py`,
    `core/vision_ocr.py`) n'est JAMAIS envoyé au moteur -- son texte est
    écrit tel quel, dans sa langue d'origine. Sert à une page bloquée par
    le filtre de contenu de l'API vision : plutôt que de la perdre ou de
    la traduire à moitié, son texte original reste visible, entouré d'un
    marqueur de début/fin qui indique la page concernée, pour que
    l'utilisateur sache exactement où revenir vérifier après coup.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    total = len(segments)
    done = start_index
    started = time.time()
    processed = 0
    smoothed_rate = None

    mode = "a" if start_index > 0 else "w"
    with out_path.open(mode, encoding="utf-8") as out_f:
        for index in range(start_index, total):
            if should_stop is not None and should_stop():
                _persist(out_path, job_state, done, finished=False)
                raise Cancelled(f"Arrêt demandé après {done}/{total} segments.")

            segment = segments[index]
            segment_started = time.time()
            if segment["type"] == "restricted":
                translated = segment["text"]  # jamais traduit -- voir la docstring ci-dessus
            else:
                translated = engine.translate(segment["text"], heartbeat=heartbeat)

            out_f.write(render_markdown(segment["type"], translated))
            out_f.write("\n")
            out_f.flush()
            os.fsync(out_f.fileno())

            done += 1
            processed += 1
            duration = time.time() - segment_started
            smoothed_rate = (
                duration if smoothed_rate is None
                else RATE_SMOOTHING * duration + (1 - RATE_SMOOTHING) * smoothed_rate
            )
            average_rate = (time.time() - started) / processed
            _persist(out_path, job_state, done, finished=done >= total)

            if on_progress is not None:
                on_progress(
                    Progress(
                        done=done,
                        total=total,
                        rate=smoothed_rate,
                        eta=(total - done) * smoothed_rate,
                        seg_type=segment["type"],
                        source_text=segment["text"],
                        translated_text=translated,
                        average_rate=average_rate,
                    )
                )

    return done


def write_segments_plain(
    segments: list[dict],
    out_path: Path,
    *,
    job_state: state_mod.JobState | None = None,
) -> int:
    """
    Écrit les segments TELS QUELS -- langue source, aucune traduction --
    dans le même format Markdown que `translate_segments` (`render_markdown`),
    pour que le résultat ait la même structure qu'une traduction, juste dans
    la langue d'origine. Sert au mode « extraction seulement » (voir
    `pipeline.Job.extract_only`) : bénéficier du même nettoyage (en-têtes/
    pieds de page, vision IA, structuration en titres/puces/paragraphes)
    sans vouloir de traduction.

    Aucun modèle, aucun appel réseau, aucune boucle lente ici -- c'est de
    la pure écriture disque, quasi instantanée même pour un document de
    plusieurs centaines de segments. Pas de reprise incrémentale par
    segment : toujours réécrit en entier (voir SPEC.md pour le raisonnement).
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as out_f:
        for segment in segments:
            out_f.write(render_markdown(segment["type"], segment["text"]))
            out_f.write("\n")
    if job_state is not None:
        job_state.done = len(segments)
        job_state.finished = True
        state_mod.save_state(out_path, job_state)
    return len(segments)


def _persist(out_path: Path, job_state, done: int, finished: bool) -> None:
    if job_state is None:
        return
    job_state.done = done
    job_state.finished = finished
    state_mod.save_state(out_path, job_state)
