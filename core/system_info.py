"""
Diagnostic matériel -- ce que TRANSLAX détecte réellement sur cette
machine, et ce qu'il utiliserait vraiment pour traduire. Demande explicite
de l'utilisateur, 25/08/2026 (page Paramètres) : savoir si la puissance
locale (CPU, GPU, RTX Nvidia...) est réellement exploitée, pas juste
supposée -- un indicateur, pas une promesse marketing.

Détecté à la demande (voir `detect()`), jamais mis en cache ni calculé au
démarrage de l'application -- ce module ne fait rien tant qu'on ne
l'appelle pas.

**Nuance réelle, pas glissée sous le tapis** : les quatre moteurs de
TRANSLAX ne se comportent PAS tous pareil vis-à-vis d'un GPU disponible
(vérifié en relisant `core/translate.py`, pas supposé) :
  - Précis, OPUS-MT, MADLAD-400 (tous basés sur `transformers`) utilisent
    automatiquement le meilleur GPU disponible -- CUDA (NVIDIA) ou MPS
    (Metal, Apple Silicon), voir `core/translate.py::_select_device`.
  - Turbo (CTranslate2) tourne TOUJOURS en CPU, même si un GPU est
    disponible -- ce moteur a été conçu et validé pour l'accélération CPU
    par quantification (voir SPEC.md §5 terdecies), pas pour CUDA/MPS.
`GPU_ENGINES`/`CPU_ONLY_ENGINES` ci-dessous reflètent cette distinction
réelle plutôt qu'une simplification qui induirait en erreur sur Turbo.

**MPS ajouté le 27/08/2026** (demande explicite de l'utilisateur, après
installation sur son MacBook Pro Apple Silicon) : CUDA n'existe QUE sur
du matériel NVIDIA -- jamais sur Mac, quelle que soit la puce -- MPS
(Metal Performance Shaders) est le vrai équivalent Apple Silicon à
détecter, pas une variante de CUDA à débloquer autrement.
"""
from __future__ import annotations

import os
import platform
import subprocess
from dataclasses import dataclass

# Moteurs qui utilisent un GPU CUDA disponible (voir core/translate.py) --
# noms tels qu'affichés dans le sélecteur (translate.MODEL_INFO[...].label),
# pas les clés internes, pour rester lisibles tels quels dans l'interface.
GPU_CAPABLE_ENGINES = ["Précis (600M/1.3B/3.3B)", "OPUS-MT", "MADLAD-400"]
CPU_ONLY_ENGINES = ["Turbo (CTranslate2)"]


@dataclass
class SystemInfo:
    os_name: str
    cpu_name: str
    cpu_cores: int
    gpu_available: bool     # PyTorch peut vraiment s'en servir (CUDA OU MPS) -- pas juste "un GPU existe"
    gpu_name: str | None
    gpu_backend: str | None  # "cuda", "mps", ou None -- LEQUEL des deux, pas juste "un GPU"
    device_for_gpu_capable_engines: str  # "cuda"/"mps"/"cpu" -- ce que Précis/OPUS-MT/MADLAD-400 utiliseraient réellement
    torch_available: bool
    detection_notes: list[str]  # ce qui n'a pas pu être détecté et pourquoi -- jamais caché


def _cpu_name_windows() -> str | None:
    """
    Nom lisible du CPU sur Windows, via PowerShell/WMI -- plus parlant que
    `platform.processor()`, qui ne donne souvent qu'une chaîne technique
    peu lisible (« AMD64 Family 23 Model 96... »). None si indisponible
    (PowerShell absent, délai dépassé, erreur) -- jamais bloquant, l'appelant
    retombe alors sur `platform.processor()`.
    """
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_Processor).Name"],
            capture_output=True, text=True, timeout=5,
        )
        name = result.stdout.strip()
        return name or None
    except Exception:
        return None


def detect() -> SystemInfo:
    """
    Détecte réellement le matériel de cette machine. Ne lève jamais
    d'exception -- toute détection impossible est notée dans
    `detection_notes` plutôt que de faire planter la page Paramètres.
    """
    notes: list[str] = []

    cpu_name: str | None = None
    if platform.system() == "Windows":
        cpu_name = _cpu_name_windows()
        if cpu_name is None:
            notes.append("Nom détaillé du CPU indisponible (PowerShell/WMI injoignable) -- repli générique utilisé.")
    cpu_name = cpu_name or platform.processor() or platform.machine() or "Inconnu"

    torch_available = True
    gpu_available = False
    gpu_name = None
    gpu_backend = None
    try:
        import torch
        if torch.cuda.is_available():
            gpu_available = True
            gpu_name = torch.cuda.get_device_name(0)
            gpu_backend = "cuda"
        else:
            # MPS (Metal Performance Shaders) : équivalent Apple Silicon de
            # CUDA -- jamais l'inverse, CUDA n'existe que sur du matériel
            # NVIDIA. `getattr` défensif : le module existe déjà sur des
            # machines non-Mac (renvoie simplement False là-bas), mais ne
            # pas supposer sa présence sur une version de PyTorch plus
            # ancienne qui ne l'aurait pas encore.
            mps_backend = getattr(torch.backends, "mps", None)
            if mps_backend is not None and mps_backend.is_available():
                gpu_available = True
                # PyTorch ne fournit pas de nom de puce précis via l'API
                # MPS (contrairement à `torch.cuda.get_device_name`) --
                # rien à cacher, juste rien de plus précis à afficher.
                gpu_name = "Apple Silicon (GPU via Metal/MPS)"
                gpu_backend = "mps"
    except ImportError:
        torch_available = False
        notes.append("PyTorch n'est pas installé -- impossible de détecter un GPU CUDA ou MPS.")
    except Exception as exc:  # noqa: BLE001 - un diagnostic ne doit jamais planter la page
        notes.append(f"Détection GPU incomplète : {exc}")

    return SystemInfo(
        os_name=f"{platform.system()} {platform.release()}",
        cpu_name=cpu_name,
        cpu_cores=os.cpu_count() or 1,
        gpu_available=gpu_available,
        gpu_name=gpu_name,
        gpu_backend=gpu_backend,
        device_for_gpu_capable_engines=gpu_backend or "cpu",
        torch_available=torch_available,
        detection_notes=notes,
    )
