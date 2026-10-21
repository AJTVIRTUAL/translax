"""
Moteurs de traduction autorisés en mode serveur (voir `core/server.py` et
SPEC.md §5 tricies octies).

**Règle absolue : licence commerciale uniquement.** Les traductions
produites par le serveur sont publiées sur un site commercial
(ajtvirtual.com). Seuls deux moteurs le permettent :
  - MADLAD-400 3B (Google, Apache 2.0) -- un seul modèle pour toutes les
    langues cibles, servi soit par CTranslate2 en int8 (`madlad-ct2`, le
    mode par défaut : ~3 Go de mémoire), soit par PyTorch en float32
    (`madlad`, ~12 Go de mémoire) ;
  - OPUS-MT (Helsinki-NLP, CC-BY 4.0) -- un modèle par paire, seulement
    là où Helsinki-NLP en a publié un (fr->de et fr->en ; pas de fr->it ni
    de fr->pt sous ce nom).
NLLB-200 (CC-BY-NC 4.0, usage commercial interdit) est REFUSÉ ici, avec un
message clair : il reste disponible ailleurs dans TRANSLAX pour l'usage
personnel de l'auteur, rien n'y est changé.

Différence avec les moteurs de `core/translate.py` : ceux-ci traduisent un
segment à la fois vers UNE langue fixée à la construction (fait pour un
livre). Ici, un modèle chargé une fois sert toutes les langues cibles, et
traduit des LOTS de textes (`translate_batch`) : le serveur reçoit des
dizaines de libellés courts par requête.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass

from . import languages as languages_mod
from . import translate as translate_mod

# Langues cibles du site (codes ISO 639-1, ceux des jetons <2xx> de MADLAD
# et des noms de modèles OPUS-MT). Source : toujours le français.
SERVER_TARGETS: tuple[str, ...] = ("de", "it", "en", "pt")
SOURCE_LANG = "fr"

COMMERCIAL_LICENSES = {"Apache-2.0", "CC-BY-4.0"}

# Réglages de génération du mode serveur -- PAS ceux de `core/translate.py` :
#  - pas de `no_repeat_ngram_size` : deux balises <strong> (ou deux
#    variables) dans une même phrase répètent forcément les mêmes trigrammes
#    de jetons ; les interdire forcerait le modèle à abîmer la seconde ;
#  - faisceau de 2 par défaut. Mesuré sur cette machine (40 textes courts,
#    allemand, SPEC.md) : 1,40 texte/s, contre 2,32 en faisceau 1 et 0,68 en
#    faisceau 4 ; le faisceau 4 ne donne pas de meilleures traductions que
#    le 2 (26 sorties sur 40 identiques, les autres équivalentes), le
#    faisceau 1 en donne de un peu moins soignées et perd plus souvent une
#    balise dans les paragraphes.
DEFAULT_BEAMS = 2
MAX_DECODING_TOKENS = 512


@dataclass(frozen=True)
class EngineSpec:
    key: str
    label: str
    repo_id: str
    license: str
    ram_gb: float        # mémoire vive nécessaire, ordre de grandeur (voir SPEC.md)
    description: str


SERVER_ENGINES: dict[str, EngineSpec] = {
    "madlad-ct2": EngineSpec(
        key="madlad-ct2",
        label="MADLAD-400 3B -- CTranslate2 int8",
        repo_id="google/madlad400-3b-mt",
        license="Apache-2.0",
        ram_gb=3.5,
        description=(
            "Mode par défaut. Les poids officiels de Google, convertis une seule fois en "
            "int8 (~3 Go au lieu de 11,8 Go en float32)."
        ),
    ),
    "madlad": EngineSpec(
        key="madlad",
        label="MADLAD-400 3B -- PyTorch float32",
        repo_id="google/madlad400-3b-mt",
        license="Apache-2.0",
        ram_gb=13.0,
        description="Les poids d'origine en float32 : ~12 Go de mémoire vive, réservé à une machine qui les a.",
    ),
    "opus-mt": EngineSpec(
        key="opus-mt",
        label="OPUS-MT (Helsinki-NLP) -- un modèle par paire",
        repo_id="Helsinki-NLP/opus-mt-fr-{tgt}",
        license="CC-BY-4.0",
        ram_gb=0.6,
        description="Très rapide, mais seulement fr->de et fr->en ; qualité inférieure à MADLAD-400.",
    ),
}
DEFAULT_SERVER_ENGINE = "madlad-ct2"

# Les quatre profils NLLB de TRANSLAX (600M, 1.3B, 3.3B, 600M-ct2) --
# retrouvés depuis MODEL_INFO plutôt que recopiés à la main.
NLLB_PROFILES = frozenset(
    key for key, info in translate_mod.MODEL_INFO.items() if "nllb" in info.repo_id.lower()
)

NLLB_REFUSAL = (
    "NLLB-200 est interdit en mode serveur : sa licence (CC-BY-NC 4.0) exclut tout usage "
    "commercial, et les traductions de ce serveur sont publiées sur un site commercial. "
    "NLLB reste disponible dans l'application TRANSLAX pour l'usage personnel. "
    "Moteurs autorisés ici : " + ", ".join(SERVER_ENGINES) + "."
)


class EngineRefused(Exception):
    """Moteur interdit en mode serveur (licence non commerciale) ou inconnu."""


class EngineUnavailable(Exception):
    """Moteur autorisé mais inutilisable ici (poids absents, langue sans modèle...)."""


def check_engine_allowed(key: str) -> EngineSpec:
    """Fiche du moteur `key`, ou `EngineRefused` avec un message clair."""
    name = (key or "").strip()
    lowered = name.lower()
    if name in NLLB_PROFILES or "nllb" in lowered or lowered in {"precise", "fast", "turbo"}:
        raise EngineRefused(NLLB_REFUSAL)
    spec = SERVER_ENGINES.get(lowered)
    if spec is None:
        raise EngineRefused(
            f"Moteur inconnu en mode serveur : « {key} ». Moteurs autorisés (licence commerciale) : "
            + ", ".join(SERVER_ENGINES) + "."
        )
    return spec


def ensure_engine_instance_allowed(engine) -> None:
    """
    Deuxième barrière, au moment de traduire : l'objet moteur lui-même doit
    déclarer une licence commerciale. Un moteur de `core/translate.py`
    (NLLB compris) n'en déclare aucune et est donc refusé, même passé
    directement par du code.
    """
    model_id = str(getattr(engine, "model_id", "") or "")
    if "nllb" in model_id.lower():
        raise EngineRefused(NLLB_REFUSAL)
    licence = getattr(engine, "license", None)
    if licence not in COMMERCIAL_LICENSES:
        raise EngineRefused(
            f"Moteur refusé en mode serveur : licence « {licence or 'non déclarée'} » "
            "(seules Apache-2.0 et CC-BY-4.0 sont permises pour un usage commercial)."
        )


def available_memory_gb() -> float | None:
    """Mémoire vive disponible (Go), ou None si on ne sait pas la lire."""
    try:
        import psutil

        return psutil.virtual_memory().available / 2**30
    except ImportError:
        pass
    if sys.platform == "win32":
        import ctypes

        class _MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = _MemoryStatus()
        status.dwLength = ctypes.sizeof(_MemoryStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return status.ullAvailPhys / 2**30
    return None


def _memory_note(spec: EngineSpec) -> str | None:
    available = available_memory_gb()
    if available is not None and available < spec.ram_gb:
        return (
            f"Attention : {available:.1f} Go de mémoire vive disponibles, {spec.label} en demande "
            f"environ {spec.ram_gb:.1f} -- le système va déplacer d'autres programmes dans la mémoire "
            "d'échange (chargement et traduction ralentis). Fermer des applications aide."
        )
    return None


def _flores(iso: str) -> str:
    for flores, code in languages_mod.FLORES_TO_ISO2.items():
        if code == iso:
            return flores
    raise EngineUnavailable(f"langue inconnue : {iso}")


def convert_t5_low_memory(model_id: str, output_dir, *, quantization: str = "int8",
                          local_files_only: bool = True) -> None:
    """
    Convertit un modèle T5 (MADLAD-400) au format CTranslate2 SANS charger
    le modèle PyTorch en mémoire.

    Pourquoi : la conversion standard (`TransformersConverter`) charge d'abord
    les 11,8 Go de poids float32 dans transformers -- mesuré sur cette
    machine : 14,8 Go de mémoire privée au bout de 4 minutes, sur 15,4 Go de
    RAM physique, conversion arrêtée avant d'épuiser la mémoire virtuelle.
    Ici, le fichier `.safetensors` est projeté en mémoire en LECTURE SEULE
    (`np.memmap`, mode "r") : ses pages sont relues depuis le disque au fil
    de la conversion et le système peut les reprendre à tout moment (une
    projection en copie-sur-écriture, elle, réserve d'emblée 11,4 Go de
    mémoire engagée sous Windows -- essayé, abandonné). Chaque tenseur n'est
    copié en mémoire qu'au moment de SA quantification, puis libéré.
    Le modèle CTranslate2 est construit par le MÊME `T5Loader` et quantifié
    par le MÊME code que la conversion standard, à partir d'objets qui
    imitent la structure des modules transformers : poids, fusions q/k/v et
    valeurs int8 sont donc identiques.
    """
    import json
    import struct
    from pathlib import Path
    from types import SimpleNamespace

    import numpy as np
    import transformers
    from ctranslate2.converters import TransformersConverter
    from ctranslate2.specs import model_spec as ct2_model_spec
    from huggingface_hub import hf_hub_download

    def download(filename: str) -> str:
        return hf_hub_download(model_id, filename, local_files_only=local_files_only)

    try:
        index_path = download("model.safetensors.index.json")
        weight_map = json.loads(Path(index_path).read_text(encoding="utf-8"))["weight_map"]
        files = sorted(set(weight_map.values()))
    except Exception:  # noqa: BLE001 - pas d'index : un seul fichier de poids
        files = ["model.safetensors"]

    tensors: dict[str, np.ndarray] = {}
    for filename in files:
        path = download(filename)
        with open(path, "rb") as handle:
            (header_size,) = struct.unpack("<Q", handle.read(8))
            header = json.loads(handle.read(header_size))
        aliases = header.pop("__metadata__", None) or {}
        data = np.memmap(path, dtype=np.uint8, mode="r", offset=8 + header_size)
        dtypes = {"F32": np.float32, "F16": np.float16}
        for name, info in header.items():
            if info["dtype"] not in dtypes:
                raise EngineUnavailable(f"type de poids inattendu dans {filename} : {info['dtype']}")
            begin, end = info["data_offsets"]
            tensors[name] = data[begin:end].view(dtypes[info["dtype"]]).reshape(info["shape"])
        # Poids partagés (embeddings) : safetensors n'en garde qu'un exemplaire
        # et note les autres noms dans ses métadonnées.
        for alias, original in aliases.items():
            if alias not in tensors and original in tensors:
                tensors[alias] = tensors[original]

    config = transformers.AutoConfig.from_pretrained(model_id, local_files_only=local_files_only)

    def weight(name: str):
        return SimpleNamespace(weight=tensors[name])

    def attention(prefix: str, relative: bool):
        module = SimpleNamespace(
            q=weight(prefix + ".q.weight"), k=weight(prefix + ".k.weight"),
            v=weight(prefix + ".v.weight"), o=weight(prefix + ".o.weight"),
            has_relative_attention_bias=relative,
            relative_attention_max_distance=config.relative_attention_max_distance,
        )
        if relative:
            module.relative_attention_bias = weight(prefix + ".relative_attention_bias.weight")
        return module

    def stack(prefix: str, count: int, decoder: bool):
        blocks = []
        for i in range(count):
            base = f"{prefix}.block.{i}.layer"
            layers = [SimpleNamespace(
                SelfAttention=attention(f"{base}.0.SelfAttention", relative=(i == 0)),
                layer_norm=weight(f"{base}.0.layer_norm.weight"),
            )]
            if decoder:
                layers.append(SimpleNamespace(
                    EncDecAttention=attention(f"{base}.1.EncDecAttention", relative=False),
                    layer_norm=weight(f"{base}.1.layer_norm.weight"),
                ))
            ffn = f"{base}.{len(layers)}"
            layers.append(SimpleNamespace(
                DenseReluDense=SimpleNamespace(
                    wi_0=weight(f"{ffn}.DenseReluDense.wi_0.weight"),
                    wi_1=weight(f"{ffn}.DenseReluDense.wi_1.weight"),
                    wo=weight(f"{ffn}.DenseReluDense.wo.weight"),
                ),
                layer_norm=weight(f"{ffn}.layer_norm.weight"),
            ))
            blocks.append(SimpleNamespace(layer=layers))
        return SimpleNamespace(
            block=blocks,
            embed_tokens=weight(f"{prefix}.embed_tokens.weight"),
            final_layer_norm=weight(f"{prefix}.final_layer_norm.weight"),
        )

    fake_model = SimpleNamespace(
        config=config,
        encoder=stack("encoder", config.num_layers, decoder=False),
        decoder=stack("decoder", config.num_decoder_layers, decoder=True),
        lm_head=weight("lm_head.weight"),
    )

    class _MemmapConverter(TransformersConverter):
        def load_model(self, model_class, model_name_or_path, **kwargs):  # noqa: ARG002 - signature imposée
            return fake_model

    # La quantification de CTranslate2 multiplie le tableau EN PLACE
    # (`value *= scale`) : impossible sur une projection en lecture seule.
    # Le temps de la conversion seulement, un tenseur en lecture seule est
    # copié juste avant d'être quantifié -- un seul à la fois en mémoire.
    original_to = ct2_model_spec.NumpyVariable.to

    def to_writable(self, dtype: str):
        if dtype == self.dtype and not self.array.flags.writeable:
            return ct2_model_spec.NumpyVariable(np.array(self.array))
        return original_to(self, dtype)

    ct2_model_spec.NumpyVariable.to = to_writable
    try:
        _MemmapConverter(model_id).convert(str(output_dir), quantization=quantization, force=True)
    finally:
        ct2_model_spec.NumpyVariable.to = original_to


class _MadladTokenMixin:
    """Jeton de langue cible de MADLAD-400 : `<2de>` préfixé au texte source
    (même mécanisme que `translate.MadladEngine`, voir SPEC.md §5 quindecies)."""

    @staticmethod
    def prompt(text: str, target: str) -> str:
        return f"<2{target}> {text}"


class MadladCt2Engine(_MadladTokenMixin):
    """
    MADLAD-400 3B servi par CTranslate2, quantifié en int8. Les poids
    officiels (cache HuggingFace) sont convertis une seule fois dans le
    dossier des modèles CTranslate2 de TRANSLAX (le même que le moteur Turbo,
    voir `translate.ctranslate2_model_dir`), puis réutilisés à chaque
    démarrage.
    """

    key = "madlad-ct2"
    license = "Apache-2.0"

    def __init__(self, *, beams: int = DEFAULT_BEAMS, threads: int | None = None,
                 allow_download: bool = False, max_batch_size: int = 8) -> None:
        self.spec = SERVER_ENGINES[self.key]
        self.model_id = self.spec.repo_id
        self.beams = beams
        self.threads = threads or os.cpu_count() or 4
        # Deux traducteurs de moitié moins de fils chacun, qui travaillent sur
        # deux lots en parallèle : mesuré +12 % de textes par seconde sur
        # cette machine (8 cœurs) par rapport à un seul traducteur à 8 fils.
        self.inter_threads = 2 if self.threads >= 4 else 1
        self.intra_threads = max(1, self.threads // self.inter_threads)
        self.allow_download = allow_download
        self.max_batch_size = max_batch_size
        self._tokenizer = None
        self._translator = None

    @property
    def loaded(self) -> bool:
        return self._translator is not None

    def supported_targets(self) -> list[str]:
        return list(SERVER_TARGETS)

    def describe(self) -> dict:
        return {
            "engine": self.key, "label": self.spec.label, "model": self.model_id,
            "license": self.license, "compute_type": "int8", "beams": self.beams,
            "threads": f"{self.inter_threads} x {self.intra_threads}", "targets": self.supported_targets(),
        }

    def load(self, on_status=None) -> None:
        import ctranslate2
        from transformers import AutoTokenizer
        from transformers.utils import logging as hf_logging

        hf_logging.set_verbosity_error()
        status = on_status or (lambda _message: None)
        model_dir = translate_mod.ctranslate2_model_dir(self.model_id)
        if not translate_mod.is_ctranslate2_ready(self.model_id):
            if not translate_mod.is_model_cached(self.model_id) and not self.allow_download:
                raise EngineUnavailable(
                    "Les poids de MADLAD-400 (google/madlad400-3b-mt, 11,8 Go) ne sont pas dans le "
                    "cache HuggingFace de cette machine. Relancer avec --autoriser-telechargement "
                    "pour les télécharger (une seule fois), puis les convertir en int8."
                )
            status(
                "Conversion de MADLAD-400 au format CTranslate2 int8 (une seule fois, "
                "plusieurs minutes, ~3 Go sur le disque)…"
            )
            model_dir.parent.mkdir(parents=True, exist_ok=True)
            convert_t5_low_memory(self.model_id, model_dir, local_files_only=not self.allow_download)
        note = _memory_note(self.spec)
        if note:
            status(note)
        status(f"Chargement de {self.model_id} (CTranslate2 int8, {self.inter_threads} x "
               f"{self.intra_threads} fils, faisceau {self.beams})…")
        self._tokenizer = AutoTokenizer.from_pretrained(self.model_id)
        self._translator = ctranslate2.Translator(
            str(model_dir), device="cpu", compute_type="int8",
            intra_threads=self.intra_threads, inter_threads=self.inter_threads,
        )

    def translate_batch(self, texts: list[str], target: str) -> list[str]:
        if not self.loaded:
            raise EngineUnavailable("Moteur non chargé.")
        if not texts:
            return []
        tokens = [
            self._tokenizer.convert_ids_to_tokens(self._tokenizer.encode(self.prompt(text, target)))
            for text in texts
        ]
        longest = max(len(item) for item in tokens)
        results = self._translator.translate_batch(
            tokens,
            beam_size=self.beams,
            max_batch_size=self.max_batch_size,
            max_decoding_length=min(MAX_DECODING_TOKENS, 2 * longest + 32),
        )
        outputs = []
        for result in results:
            ids = self._tokenizer.convert_tokens_to_ids(result.hypotheses[0])
            outputs.append(self._tokenizer.decode(ids, skip_special_tokens=True))
        return outputs

    def unload(self) -> None:
        self._tokenizer = None
        self._translator = None


class MadladTorchEngine(_MadladTokenMixin):
    """MADLAD-400 3B servi par transformers/PyTorch en float32 (~12 Go de mémoire)."""

    key = "madlad"
    license = "Apache-2.0"

    def __init__(self, *, beams: int = DEFAULT_BEAMS, threads: int | None = None,
                 allow_download: bool = False, max_batch_size: int = 8) -> None:
        self.spec = SERVER_ENGINES[self.key]
        self.model_id = self.spec.repo_id
        self.beams = beams
        self.threads = threads or os.cpu_count() or 4
        self.allow_download = allow_download
        self.max_batch_size = max_batch_size
        self.device = "cpu"
        self._tokenizer = None
        self._model = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def supported_targets(self) -> list[str]:
        return list(SERVER_TARGETS)

    def describe(self) -> dict:
        return {
            "engine": self.key, "label": self.spec.label, "model": self.model_id,
            "license": self.license, "compute_type": "float32", "beams": self.beams,
            "threads": self.threads, "device": self.device, "targets": self.supported_targets(),
        }

    def load(self, on_status=None) -> None:
        import torch
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
        from transformers.utils import logging as hf_logging

        hf_logging.set_verbosity_error()
        status = on_status or (lambda _message: None)
        if not translate_mod.is_model_cached(self.model_id) and not self.allow_download:
            raise EngineUnavailable(
                "Les poids de MADLAD-400 (11,8 Go) ne sont pas dans le cache HuggingFace. "
                "Relancer avec --autoriser-telechargement pour les télécharger."
            )
        note = _memory_note(self.spec)
        if note:
            status(note)
        torch.set_num_threads(self.threads)
        self.device = translate_mod._select_device()  # noqa: SLF001 - même choix CUDA > MPS > CPU que l'appli
        status(f"Chargement de {self.model_id} (PyTorch float32, {self.device})…")
        self._tokenizer = AutoTokenizer.from_pretrained(self.model_id)
        self._model = AutoModelForSeq2SeqLM.from_pretrained(self.model_id)
        self._model.to(self.device)
        self._model.eval()

    def translate_batch(self, texts: list[str], target: str) -> list[str]:
        import torch

        if not self.loaded:
            raise EngineUnavailable("Moteur non chargé.")
        prompts = [self.prompt(text, target) for text in texts]
        # Lots de longueurs voisines : moins de remplissage, donc moins de calcul perdu.
        order = sorted(range(len(prompts)), key=lambda i: len(prompts[i]))
        outputs: list[str] = [""] * len(prompts)
        for start in range(0, len(order), self.max_batch_size):
            chunk = order[start:start + self.max_batch_size]
            encoded = self._tokenizer([prompts[i] for i in chunk], return_tensors="pt", padding=True).to(self.device)
            with torch.no_grad():
                generated = self._model.generate(
                    **encoded,
                    num_beams=self.beams,
                    max_new_tokens=min(MAX_DECODING_TOKENS, 2 * encoded["input_ids"].shape[1] + 32),
                )
            for index, text in zip(chunk, self._tokenizer.batch_decode(generated, skip_special_tokens=True)):
                outputs[index] = text
        return outputs

    def unload(self) -> None:
        self._tokenizer = None
        self._model = None


class OpusMtServerEngine:
    """
    OPUS-MT : un modèle Helsinki-NLP par paire (`opus-mt-fr-de`,
    `opus-mt-fr-en`), chargés au démarrage pour chaque langue cible déjà
    présente dans le cache. Une langue sans modèle (italien, portugais) est
    refusée avec un message clair plutôt que traduite par un autre moteur
    sans le dire.
    """

    key = "opus-mt"
    license = "CC-BY-4.0"

    def __init__(self, *, beams: int = 4, threads: int | None = None,
                 allow_download: bool = False, max_batch_size: int = 16) -> None:
        self.spec = SERVER_ENGINES[self.key]
        self.model_id = self.spec.repo_id
        self.beams = beams
        self.threads = threads or os.cpu_count() or 4
        self.allow_download = allow_download
        self.max_batch_size = max_batch_size
        self.device = "cpu"
        self._models: dict[str, tuple] = {}
        self._loaded = False

    @property
    def loaded(self) -> bool:
        return self._loaded

    def repo_for(self, target: str) -> str | None:
        return translate_mod.opus_mt_repo_id(_flores(SOURCE_LANG), _flores(target))

    def supported_targets(self) -> list[str]:
        if self._loaded:
            return sorted(self._models)
        return [t for t in SERVER_TARGETS if (repo := self.repo_for(t)) and translate_mod.is_model_cached(repo)]

    def describe(self) -> dict:
        return {
            "engine": self.key, "label": self.spec.label,
            "model": {t: self.repo_for(t) for t in self.supported_targets()},
            "license": self.license, "compute_type": "float32", "beams": self.beams,
            "threads": self.threads, "targets": self.supported_targets(),
        }

    def load(self, on_status=None) -> None:
        import torch
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
        from transformers.utils import logging as hf_logging

        hf_logging.set_verbosity_error()
        status = on_status or (lambda _message: None)
        torch.set_num_threads(self.threads)
        self.device = translate_mod._select_device()  # noqa: SLF001
        targets = list(SERVER_TARGETS) if self.allow_download else self.supported_targets()
        for target in targets:
            repo = self.repo_for(target)
            try:
                status(f"Chargement de {repo}…")
                tokenizer = AutoTokenizer.from_pretrained(repo)
                model = AutoModelForSeq2SeqLM.from_pretrained(repo).to(self.device).eval()
            except OSError:
                status(f"Pas de modèle OPUS-MT publié pour fr->{target} ({repo}) : langue ignorée.")
                continue
            self._models[target] = (tokenizer, model)
        if not self._models:
            raise EngineUnavailable(
                "Aucun modèle OPUS-MT fr->xx dans le cache (attendus : Helsinki-NLP/opus-mt-fr-de, "
                "opus-mt-fr-en). Relancer avec --autoriser-telechargement (environ 300 Mo par paire)."
            )
        self._loaded = True

    def translate_batch(self, texts: list[str], target: str) -> list[str]:
        import torch

        if target not in self._models:
            raise EngineUnavailable(
                f"OPUS-MT n'a pas de modèle fr->{target} sur cette machine "
                f"(langues disponibles : {', '.join(sorted(self._models)) or 'aucune'}). "
                "Utiliser le moteur madlad-ct2 pour cette langue."
            )
        tokenizer, model = self._models[target]
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
        outputs: list[str] = [""] * len(texts)
        for start in range(0, len(order), self.max_batch_size):
            chunk = order[start:start + self.max_batch_size]
            encoded = tokenizer([texts[i] for i in chunk], return_tensors="pt", padding=True,
                                truncation=True, max_length=512).to(self.device)
            with torch.no_grad():
                generated = model.generate(
                    **encoded, num_beams=self.beams,
                    max_new_tokens=min(MAX_DECODING_TOKENS, 2 * encoded["input_ids"].shape[1] + 32),
                )
            for index, text in zip(chunk, tokenizer.batch_decode(generated, skip_special_tokens=True)):
                outputs[index] = text
        return outputs

    def unload(self) -> None:
        self._models.clear()
        self._loaded = False


_ENGINE_CLASSES = {
    "madlad-ct2": MadladCt2Engine,
    "madlad": MadladTorchEngine,
    "opus-mt": OpusMtServerEngine,
}


def create_engine(key: str, **options):
    """Moteur prêt à charger, après le contrôle de licence."""
    spec = check_engine_allowed(key)
    options = {name: value for name, value in options.items() if value is not None}
    return _ENGINE_CLASSES[spec.key](**options)
