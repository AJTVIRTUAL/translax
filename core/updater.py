"""
Vérification et téléchargement des mises à jour de TRANSLAX -- demande
explicite de l'utilisateur, 27/08/2026 : « comme sur VS Code, avec un
bouton update où on clique et tout le reste se lance ».

S'appuie sur les Releases du dépôt GitHub public (voir SPEC.md) :
`GET https://api.github.com/repos/AJTVIRTUAL/translax/releases/latest`
-- pas de serveur de mise à jour dédié, pas de clé d'API nécessaire
(dépôt public ; la limite de 60 requêtes/heure sans authentification est
largement suffisante pour un clic occasionnel sur « Chercher une mise à
jour »).

`urllib.request` (bibliothèque standard), PAS `requests` : sur Windows,
`ssl.create_default_context()` utilise le magasin de certificats de l'OS
lui-même, pas un fichier `certifi` embarqué séparément -- une dépendance
de moins à empaqueter correctement dans l'exe gelé (voir les correctifs
de packaging PaddleOCR de ce même projet, tous causés par des fichiers
que PyInstaller ne devine pas qu'il faut embarquer).

Ce module ne fait JAMAIS rien tout seul : chaque étape (vérifier,
télécharger, lancer l'installeur) n'est appelée que depuis un clic
explicite dans l'interface (voir ui/main_window.py) -- jamais de
vérification silencieuse au démarrage, jamais de téléchargement sans
confirmation préalable.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.request import Request, urlopen

REPO = "AJTVIRTUAL/translax"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
REQUEST_TIMEOUT_S = 10
DOWNLOAD_TIMEOUT_S = 60  # par bloc lu, pas pour le téléchargement entier
ASSET_NAME_PREFIX = "TRANSLAX-Setup-"
_USER_AGENT = "TRANSLAX-updater"


class UpdateCheckError(Exception):
    """
    Levée pour tout problème de vérification/téléchargement (réseau, dépôt
    sans publication, format de réponse inattendu...) -- toujours un
    message déjà lisible pour l'utilisateur, jamais une exception brute
    de bibliothèque réseau à retranscrire soi-même.
    """


@dataclass
class ReleaseInfo:
    version: str          # "1.17.0" -- le "v" éventuel du tag est retiré
    download_url: str
    asset_size: int
    notes: str


def _parse_version(text: str) -> tuple[int, ...]:
    """
    "v1.17.0" ou "1.17.0" -> (1, 17, 0). Segments non numériques réduits à
    0 plutôt que de lever une exception -- une étiquette de version
    inhabituelle ne doit jamais faire planter la comparaison, seulement la
    rendre peu fiable pour CE cas précis.
    """
    cleaned = text.lstrip("vV")
    parts = []
    for piece in cleaned.split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def is_newer(remote_version: str, local_version: str) -> bool:
    return _parse_version(remote_version) > _parse_version(local_version)


def check_latest_release() -> ReleaseInfo:
    """
    Interroge la dernière publication GitHub. Lève `UpdateCheckError` avec
    un message clair en cas de problème -- jamais une exception brute.
    """
    req = Request(
        API_URL,
        headers={"Accept": "application/vnd.github+json", "User-Agent": _USER_AGENT},
    )
    try:
        with urlopen(req, timeout=REQUEST_TIMEOUT_S) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - toute erreur réseau/HTTP devient un message clair
        raise UpdateCheckError(f"Impossible de contacter GitHub : {exc}") from exc

    tag = data.get("tag_name")
    if not tag:
        raise UpdateCheckError("Réponse de GitHub inattendue (aucune version publiée).")

    asset = next(
        (a for a in data.get("assets", []) if a.get("name", "").startswith(ASSET_NAME_PREFIX)),
        None,
    )
    if asset is None:
        raise UpdateCheckError(
            f"La version {tag} est publiée sur GitHub, mais sans installeur Windows joint."
        )

    return ReleaseInfo(
        version=tag.lstrip("vV"),
        download_url=asset["browser_download_url"],
        asset_size=asset.get("size", 0),
        notes=(data.get("body") or "").strip(),
    )


def download_installer(
    url: str,
    dest_path: Path,
    *,
    on_progress=None,
    should_stop=None,
) -> None:
    """
    Télécharge l'installeur par blocs de 1 Mo, avec progression --
    `on_progress(fait, total)` appelé après chaque bloc (`total` peut être
    0 si GitHub ne renvoie pas Content-Length : l'appelant doit gérer ce
    cas, jamais supposer un pourcentage toujours calculable).
    `should_stop()` : permet d'annuler un téléchargement de plusieurs
    centaines de Mo en cours de route.

    Écrit d'abord dans un fichier `.part` puis renomme à la fin --
    n'importe quel arrêt en cours de route (annulation, coupure réseau)
    laisse un fichier `.part` reconnaissable comme incomplet, jamais un
    `.exe` à moitié écrit qui pourrait être lancé par erreur.
    """
    req = Request(url, headers={"User-Agent": _USER_AGENT})
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest_path.with_suffix(dest_path.suffix + ".part")
    try:
        with urlopen(req, timeout=DOWNLOAD_TIMEOUT_S) as response:
            total = int(response.headers.get("Content-Length", 0) or 0)
            done = 0
            with tmp_path.open("wb") as f:
                while True:
                    if should_stop is not None and should_stop():
                        raise UpdateCheckError("Téléchargement annulé.")
                    chunk = response.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if on_progress is not None:
                        on_progress(done, total)
    except UpdateCheckError:
        tmp_path.unlink(missing_ok=True)
        raise
    except Exception as exc:  # noqa: BLE001
        tmp_path.unlink(missing_ok=True)
        raise UpdateCheckError(f"Échec du téléchargement : {exc}") from exc
    tmp_path.replace(dest_path)


def launch_installer_and_quit(installer_path: Path) -> None:
    """
    Lance l'installeur téléchargé en arrière-plan, en mode silencieux avec
    relance automatique (voir `installer/translax.iss` : entrée `[Run]`
    `postinstall` SANS `skipifsilent`, `CloseApplications`/
    `RestartApplications` activés) -- puis NE FAIT RIEN D'AUTRE.

    C'est à l'APPELANT (l'interface) de fermer TRANSLAX immédiatement
    après cet appel : le fichier .exe en cours d'exécution reste verrouillé
    par Windows tant que ce processus tourne, l'installeur ne peut le
    remplacer qu'une fois TRANSLAX réellement terminé. `/CLOSEAPPLICATIONS`
    reste une sécurité supplémentaire (utile si l'appelant ne se fermait
    pas assez vite, ou si une AUTRE instance de TRANSLAX tournait), pas le
    mécanisme principal.
    """
    subprocess.Popen(
        [
            str(installer_path),
            "/SILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/CLOSEAPPLICATIONS",
            "/NOCANCEL",
        ],
        close_fds=True,
    )
