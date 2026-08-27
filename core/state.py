"""
État d'avancement d'une traduction, pour la reprise après interruption.

Le pipeline d'origine comptait les blocs « \n\n » déjà écrits dans le .md
pour savoir où reprendre. C'est fragile : un paragraphe traduit contenant
une ligne vide décale le compte et la reprise repart au mauvais endroit.

TRANSLAX écrit à la place un fichier compagnon JSON à côté de la sortie, qui
mémorise le nombre exact de segments écrits, plus une empreinte du fichier
source pour vérifier qu'on reprend bien la même traduction et pas un autre
document qui porterait le même nom.

Les fichiers de travail vivent dans un sous-dossier `.translax/` du dossier
de sortie, pour ne pas polluer le dossier de l'utilisateur.

Depuis que le titre du fichier de sortie est traduit DÈS LE DÉBUT (et non
plus seulement à la toute fin -- voir SPEC.md), un fichier « pointeur »
(`pointer_path`/`save_output_pointer`/`resolve_output_path`) fait le lien
entre le nom dérivé du fichier source (toujours calculable sans modèle) et
le vrai nom de sortie sous lequel le travail est réellement écrit : sans
ça, reprendre une traduction interrompue après un premier lancement de
l'appli ne retrouverait plus rien, le nom ayant changé entre-temps.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

WORK_DIR_NAME = ".translax"


@dataclass
class JobState:
    source_path: str
    source_hash: str
    total: int
    done: int = 0
    src_lang: str = ""
    tgt_lang: str = ""
    model: str = ""
    strategy: str = ""
    finished: bool = False
    extra: dict = field(default_factory=dict)


def work_dir(out_path: Path) -> Path:
    return Path(out_path).parent / WORK_DIR_NAME


def state_path(out_path: Path) -> Path:
    return work_dir(out_path) / (Path(out_path).stem + ".progress.json")


def segments_path(out_path: Path) -> Path:
    return work_dir(out_path) / (Path(out_path).stem + ".segments.jsonl")


def source_hash(path: Path) -> str:
    """SHA-256 du fichier source, lu par blocs (les PDF peuvent être gros)."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_state(out_path: Path) -> JobState | None:
    path = state_path(out_path)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return JobState(**data)
    except (json.JSONDecodeError, TypeError):
        # Fichier d'état corrompu (arrêt brutal en pleine écriture) :
        # on l'ignore et on repart de zéro plutôt que de planter.
        return None


def save_state(out_path: Path, state: JobState) -> None:
    path = state_path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(state), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)  # remplacement atomique : jamais d'état à moitié écrit


def pointer_path(out_path: Path) -> Path:
    return work_dir(out_path) / (Path(out_path).stem + ".target.json")


def save_output_pointer(original_out_path: Path, real_out_path: Path, hash_: str) -> None:
    """
    Enregistre que le vrai fichier de sortie de `original_out_path` (nom
    dérivé du fichier source, calculé par `pipeline.default_output_path`)
    est en réalité `real_out_path` -- typiquement le nom traduit, appliqué
    dès le début de la traduction (voir `pipeline._resolve_actual_output`).

    Indispensable pour que la reprise fonctionne : `pipeline.run_job`
    recalcule TOUJOURS `original_out_path` de la même façon (à partir du
    seul fichier source, sans modèle) au lancement suivant -- sans ce
    pointeur, il chercherait un état sous ce nom-là et ne trouverait rien,
    puisque le travail réel a été renommé entre-temps.
    """
    path = pointer_path(original_out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps({"source_hash": hash_, "output_path": str(real_out_path)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


def resolve_output_path(original_out_path: Path, source: Path) -> Path:
    """
    Redirige vers le VRAI fichier de sortie d'un job déjà commencé sous un
    nom traduit, si un pointeur valide existe -- sinon renvoie
    `original_out_path` inchangé (premier lancement de ce job, ou titre non
    traduit). Ne charge jamais le modèle : une simple lecture de fichier
    JSON, appelée en tout début de `run_job`, avant même l'extraction.
    """
    path = pointer_path(original_out_path)
    if not path.exists():
        return original_out_path
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return original_out_path
    if data.get("source_hash") != source_hash(source):
        return original_out_path  # pointeur d'un autre fichier source, même nom de sortie
    return Path(data.get("output_path", original_out_path))


def can_resume(out_path: Path, source: Path) -> JobState | None:
    """
    Retourne l'état réutilisable si une traduction du MÊME fichier source a
    été interrompue, sinon None (fichier différent, terminé, ou rien à
    reprendre).
    """
    state = load_state(out_path)
    if state is None or state.finished or state.done <= 0:
        return None
    if not Path(out_path).exists():
        return None
    if state.source_hash != source_hash(source):
        return None
    return state


def abandon(out_path: Path, source_path: Path | None = None) -> None:
    """
    Efface l'état de reprise (progression, segments, pointeur, cache OCR)
    de CE job précis -- demande explicite de l'utilisateur, 26/08/2026
    (bouton Stop rouge, ou « Abandonner » dans la liste des traductions
    interrompues). Après ça, `can_resume` renvoie forcément None pour ce
    job. Ne touche JAMAIS au fichier de sortie lui-même -- le texte déjà
    traduit reste sur le disque.

    Efface les fichiers UN PAR UN (jamais un `shutil.rmtree` du dossier
    `.translax/` entier) : plusieurs traductions interrompues partagent
    désormais le même dossier de sortie -- donc le même `.translax/` --
    quand elles vivent dans le même dossier (voir la liste de reprise,
    `ui/main_window.py::ResumeJobsDialog`). Effacer tout le dossier
    abandonnerait par erreur l'état de reprise des AUTRES jobs voisins,
    pas seulement celui-ci -- bug réel rencontré en écrivant les tests de
    cette fonction, pas une précaution théorique.

    `source_path` : optionnel, seulement pour effacer aussi le cache OCR
    (voir `core/vision_ocr.py`), indexé par le nom du fichier SOURCE et
    non celui de sortie (voir `core/pipeline.py::vision_cache`) -- omis,
    ce cache reste, sans conséquence (il ne sert qu'à accélérer une
    future extraction du même PDF, jamais consulté pour un job abandonné).
    """
    d = work_dir(out_path)
    if not d.exists():
        return
    stems = {Path(out_path).stem}
    if source_path is not None:
        stems.add(Path(source_path).stem)
    for f in d.iterdir():
        if f.is_file() and any(f.name.startswith(f"{stem}.") for stem in stems):
            try:
                f.unlink()
            except OSError:
                pass
    try:
        d.rmdir()  # ne réussit que si devenu vide (plus aucun autre job actif dedans) -- purement cosmétique
    except OSError:
        pass
