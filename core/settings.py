"""
Réglages persistants de TRANSLAX -- pour l'instant, uniquement le dossier
de sortie par défaut (demande explicite de l'utilisateur : s'en souvenir
d'un lancement à l'autre, pas seulement pour la session en cours).

Stocké dans un petit fichier JSON, dans le dossier de configuration standard
de l'OS -- jamais à côté du code (qui peut être en lecture seule une fois
empaqueté en .exe/.app) :
  - Windows : %APPDATA%\\TRANSLAX\\settings.json
  - macOS   : ~/Library/Application Support/TRANSLAX/settings.json
  - Linux   : ~/.config/translax/settings.json (repli raisonnable, non
    testé -- TRANSLAX ne cible pas Linux pour l'instant)

Un fichier absent, corrompu ou illisible ne doit jamais empêcher l'appli de
démarrer -- on retombe silencieusement sur « aucun réglage », comme si
c'était un premier lancement.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

APP_NAME = "TRANSLAX"


def _settings_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        return Path(base) / APP_NAME if base else Path.home() / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path.home() / ".config" / APP_NAME.lower()


def settings_path() -> Path:
    return _settings_dir() / "settings.json"


def app_data_dir() -> Path:
    """
    Dossier de données de l'application, pas seulement le fichier de
    réglages -- sert de base à d'autres caches propres à TRANSLAX, comme
    les modèles convertis au format CTranslate2 (voir
    `core/translate.py::ctranslate2_model_dir`).
    """
    return _settings_dir()


def load_settings() -> dict:
    path = settings_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_settings(data: dict) -> None:
    path = settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)  # remplacement atomique, même raisonnement que core/state.py
    except OSError:
        # Ne bloque jamais l'appli pour un problème d'écriture de préférence
        # (dossier en lecture seule, disque plein...) -- le réglage sera
        # simplement redemandé au prochain lancement.
        pass


def get_default_output_dir() -> Path | None:
    """
    Dossier de sortie choisi par l'utilisateur, mémorisé d'un lancement à
    l'autre. None si jamais réglé, ou si le dossier mémorisé n'existe plus
    (déplacé/supprimé depuis le dernier lancement) -- dans ce cas on
    retombe silencieusement sur le comportement par défaut (même dossier
    que le fichier source) plutôt que d'écrire dans un dossier disparu.
    """
    raw = load_settings().get("default_output_dir")
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_dir() else None


def set_default_output_dir(path: Path | None) -> None:
    """`None` efface le réglage -- retour au comportement par défaut (même
    dossier que le fichier source à chaque traduction)."""
    data = load_settings()
    if path is None:
        data.pop("default_output_dir", None)
    else:
        data["default_output_dir"] = str(Path(path))
    save_settings(data)


def get_anthropic_api_key() -> str | None:
    """
    Clé API Anthropic de l'utilisateur, pour le bouton « Traduire X » (voir
    core/vision_ocr.py) -- jamais fournie par TRANSLAX lui-même, jamais
    codée en dur : l'utilisateur crée la sienne sur console.anthropic.com et
    la colle dans les Réglages. Stockée en clair dans le même fichier JSON
    que les autres réglages, sur cette seule machine -- cohérent avec le
    niveau de simplicité du reste de l'appli (aucune autre donnée sensible
    n'y transite), mais à garder en tête si ce fichier devait un jour être
    partagé ou sauvegardé ailleurs.
    """
    key = load_settings().get("anthropic_api_key")
    return key or None


def set_anthropic_api_key(key: str | None) -> None:
    """`None` ou une chaîne vide efface la clé enregistrée."""
    data = load_settings()
    if not key:
        data.pop("anthropic_api_key", None)
    else:
        data["anthropic_api_key"] = key.strip()
    save_settings(data)


def get_xai_api_key() -> str | None:
    """
    Clé API xAI (Grok) -- préparée pour une future intégration OCR/vision
    (demande explicite de l'utilisateur, 25/08/2026), pas encore utilisée
    par aucune fonctionnalité de TRANSLAX. Même mécanisme de stockage que
    `get_anthropic_api_key`.
    """
    key = load_settings().get("xai_api_key")
    return key or None


def set_xai_api_key(key: str | None) -> None:
    """`None` ou une chaîne vide efface la clé enregistrée."""
    data = load_settings()
    if not key:
        data.pop("xai_api_key", None)
    else:
        data["xai_api_key"] = key.strip()
    save_settings(data)


def get_openai_api_key() -> str | None:
    """
    Clé API OpenAI (ChatGPT) -- préparée pour une future intégration
    (demande explicite de l'utilisateur, 25/08/2026), pas encore utilisée
    par aucune fonctionnalité de TRANSLAX. Même mécanisme de stockage que
    `get_anthropic_api_key`.
    """
    key = load_settings().get("openai_api_key")
    return key or None


def set_openai_api_key(key: str | None) -> None:
    """`None` ou une chaîne vide efface la clé enregistrée."""
    data = load_settings()
    if not key:
        data.pop("openai_api_key", None)
    else:
        data["openai_api_key"] = key.strip()
    save_settings(data)


def get_pending_jobs() -> list[dict]:
    """
    Toutes les traductions lancées depuis l'interface et pas encore
    terminées avec succès ni abandonnées (demande explicite de
    l'utilisateur, 26/08/2026 : proposer TOUTES celles en attente au
    démarrage, pas seulement la dernière) -- sert à construire la liste de
    reprise. Ne garantit PAS que chacune est encore réellement reprenable :
    c'est `state.can_resume` qui tranche pour chacune, cette fonction ne
    fait que retrouver les jobs à vérifier.
    """
    return list(load_settings().get("pending_jobs", {}).values())


def add_pending_job(data: dict) -> None:
    """
    Enregistré à chaque lancement de traduction depuis l'interface (voir
    `MainWindow._start_impl`), AVANT même que le travail ne démarre --
    indexé par `data["output_path"]` : relancer le même job (ex. après une
    pause) met simplement à jour son entrée plutôt que d'en dupliquer une
    nouvelle.
    """
    stored = load_settings()
    jobs = stored.setdefault("pending_jobs", {})
    jobs[data["output_path"]] = data
    save_settings(stored)


def remove_pending_job(output_path: str) -> None:
    """
    Retire un job de la liste d'attente -- appelé quand il se termine avec
    succès (voir `MainWindow._on_finished`) ou quand l'utilisateur
    l'abandonne explicitement (bouton Stop rouge, ou « Abandonner » dans la
    liste des traductions interrompues). Ne fait rien si l'entrée n'existe
    déjà plus (jamais une erreur).
    """
    stored = load_settings()
    jobs = stored.get("pending_jobs", {})
    jobs.pop(output_path, None)
    stored["pending_jobs"] = jobs
    save_settings(stored)
