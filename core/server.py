"""
Serveur de traduction local de TRANSLAX, compatible avec l'API DeepL v2
(voir SPEC.md §5 tricies octies).

Sert à pré-traduire les textes du site ajtvirtual.com depuis la rubrique
Traductions de l'admin (dans le navigateur) : l'admin appelle ce serveur
comme elle appellerait DeepL, enregistre les résultats comme « pré-traduits »
et un humain relit.

    python cli.py serve --port 8765 --engine madlad-ct2
    python -m core.server --port 8765

Points clés :
  - lié à 127.0.0.1 UNIQUEMENT (jamais joignable depuis le réseau) ; l'en-tête
    Host est vérifié (protection contre le « DNS rebinding ») ;
  - moteurs à licence commerciale seulement (MADLAD-400, OPUS-MT) : NLLB est
    refusé au démarrage, voir `core/server_engines.py` ;
  - CORS limité aux origines de l'admin (configurable), réponse aux requêtes
    OPTIONS avec `Access-Control-Allow-Private-Network: true` (sans quoi
    Chrome bloque un site https qui appelle 127.0.0.1) ; une requête venant
    d'une autre origine est refusée AVANT tout travail ;
  - clé facultative : `Authorization: DeepL-Auth-Key <clé>` exigé seulement
    si une clé est configurée ;
  - le modèle est chargé une fois, en arrière-plan au démarrage (GET /health
    répond tout de suite, avec l'état du chargement), puis reste en mémoire.

Bibliothèque standard uniquement (http.server) : aucun framework ajouté.
"""
from __future__ import annotations

import argparse
import csv
import hmac
import io
import json
import os
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import server_engines
from . import settings as settings_mod
from . import version as version_mod
from .translation_service import TranslationService

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_CORS_ORIGINS: tuple[str, ...] = (
    "https://admin.ajtvirtual.com",
    "http://localhost:3000",
    "http://localhost:3001",
    "http://localhost:3002",
)
MAX_TEXTS = 50                  # même limite que DeepL par requête
MAX_BODY_BYTES = 2 * 1024 * 1024
AUTH_KEY_ENV = "TRANSLAX_SERVER_KEY"

SOURCE_LANGUAGES = {"FR": "French"}
# Code DeepL -> (code ISO du moteur, nom DeepL)
TARGET_LANGUAGES: dict[str, tuple[str, str]] = {
    "DE": ("de", "German"),
    "EN-GB": ("en", "English (British)"),
    "EN-US": ("en", "English (American)"),
    "IT": ("it", "Italian"),
    "PT-PT": ("pt", "Portuguese (European)"),
    "PT-BR": ("pt", "Portuguese (Brazilian)"),
}
TARGET_ALIASES = {"EN": "EN-GB", "PT": "PT-PT"}


def _log(message: str) -> None:
    print(f"{time.strftime('%H:%M:%S')}  {message}", flush=True)


# --------------------------------------------------------------------------
# Glossaires façon DeepL


class GlossaryError(ValueError):
    pass


def parse_glossary_entries(text: str, entries_format: str = "tsv") -> dict[str, str]:
    """Entrées « source<TAB>cible » (tsv, format par défaut de DeepL) ou csv."""
    if entries_format not in ("tsv", "csv"):
        raise GlossaryError(f"entries_format non pris en charge : {entries_format}")
    if entries_format == "csv":
        rows = list(csv.reader(io.StringIO(text)))
    else:
        rows = [line.split("\t") for line in text.splitlines()]
    entries: dict[str, str] = {}
    for number, row in enumerate(rows, start=1):
        if not row or all(not cell.strip() for cell in row):
            continue
        if len(row) < 2 or not row[0].strip() or not row[1].strip():
            raise GlossaryError(f"ligne {number} du glossaire invalide : {row!r}")
        entries[row[0].strip()] = row[1].strip()
    return entries


def coerce_glossary(value) -> dict[str, str]:
    """`glossary_entries` (extension non-DeepL) : objet {source: cible},
    liste de paires, ou texte TSV."""
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return {str(k).strip(): str(v).strip() for k, v in value.items() if str(k).strip() and str(v).strip()}
    if isinstance(value, list):
        result = {}
        for item in value:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                result[str(item[0]).strip()] = str(item[1]).strip()
            elif isinstance(item, dict) and "source" in item and "target" in item:
                result[str(item["source"]).strip()] = str(item["target"]).strip()
            else:
                raise GlossaryError("glossary_entries : chaque élément doit être [source, cible]")
        return {k: v for k, v in result.items() if k and v}
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                return coerce_glossary(json.loads(stripped))
            except json.JSONDecodeError as exc:
                raise GlossaryError(f"glossary_entries : JSON invalide ({exc})") from exc
        return parse_glossary_entries(value)
    raise GlossaryError("glossary_entries : format non reconnu")


def coerce_terms(value) -> list[str]:
    """`keep_terms` : liste, ou texte séparé par des virgules / retours à la ligne."""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        value = [part for chunk in value.splitlines() for part in chunk.split(",")]
    if not isinstance(value, list):
        raise GlossaryError("keep_terms doit être une liste de termes")
    return [str(term).strip() for term in value if str(term).strip()]


class GlossaryStore:
    """Glossaires créés par POST /v2/glossaries, gardés dans un petit fichier
    JSON (dossier de données de TRANSLAX) pour survivre à un redémarrage."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._items: dict[str, dict] = {}
        self._lock = threading.Lock()
        if path is not None and path.exists():
            try:
                self._items = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self._items = {}

    @staticmethod
    def _info(item: dict) -> dict:
        return {key: item[key] for key in ("glossary_id", "name", "ready", "source_lang", "target_lang",
                                           "creation_time", "entry_count")}

    def _save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._items, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass  # un glossaire non persisté reste utilisable jusqu'à l'arrêt du serveur

    def create(self, name: str, source_lang: str, target_lang: str, entries: dict[str, str]) -> dict:
        item = {
            "glossary_id": str(uuid.uuid4()),
            "name": name,
            "ready": True,
            "source_lang": source_lang.lower(),
            "target_lang": target_lang.lower(),
            "creation_time": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "entry_count": len(entries),
            "entries": entries,
        }
        with self._lock:
            self._items[item["glossary_id"]] = item
            self._save()
        return self._info(item)

    def list(self) -> list[dict]:
        with self._lock:
            return [self._info(item) for item in self._items.values()]

    def get(self, glossary_id: str) -> dict | None:
        with self._lock:
            item = self._items.get(glossary_id)
            return dict(item) if item else None

    def delete(self, glossary_id: str) -> bool:
        with self._lock:
            found = self._items.pop(glossary_id, None) is not None
            if found:
                self._save()
            return found


def default_glossary_path() -> Path:
    return settings_mod.app_data_dir() / "serveur" / "glossaires.json"


# --------------------------------------------------------------------------
# État du serveur


class ServerState:
    def __init__(
        self,
        engine,
        *,
        auth_key: str | None = None,
        cors_origins: tuple[str, ...] | list[str] = DEFAULT_CORS_ORIGINS,
        max_texts: int = MAX_TEXTS,
        glossary_path: Path | None = None,
        log=_log,
    ) -> None:
        # Contrôle de licence AVANT tout : un moteur NLLB (ou sans licence
        # commerciale déclarée) lève EngineRefused ici.
        self.service = TranslationService(engine)
        self.engine = engine
        self.auth_key = auth_key or None
        self.cors_origins = tuple(origin.rstrip("/") for origin in cors_origins)
        self.max_texts = max_texts
        self.glossaries = GlossaryStore(glossary_path)
        self.log = log
        self.status = "ready" if getattr(engine, "loaded", False) else "loading"
        self.error: str | None = None
        self.load_seconds: float | None = None
        self.started = time.time()
        self.requests = 0
        self.character_count = 0
        self._counter_lock = threading.Lock()

    def load_engine(self) -> None:
        """Chargement du modèle (appelé dans un fil à part au démarrage)."""
        started = time.perf_counter()
        try:
            self.engine.load(on_status=self.log)
        except Exception as exc:  # noqa: BLE001 - l'erreur est rendue visible via /health
            self.status = "error"
            self.error = f"{type(exc).__name__}: {exc}"
            self.log(f"ÉCHEC du chargement du modèle : {self.error}")
            return
        self.load_seconds = round(time.perf_counter() - started, 1)
        self.status = "ready"
        self.log(f"Modèle prêt en {self.load_seconds} s.")

    def count(self, characters: int) -> None:
        with self._counter_lock:
            self.requests += 1
            self.character_count += characters

    def deepl_targets(self) -> list[str]:
        supported = set(self.engine.supported_targets())
        return [code for code, (iso, _name) in TARGET_LANGUAGES.items() if iso in supported]

    def health(self) -> dict:
        describe = self.engine.describe() if hasattr(self.engine, "describe") else {}
        return {
            "status": self.status,
            "ready": self.status == "ready",
            "error": self.error,
            "engine": describe.get("engine", getattr(self.engine, "key", "?")),
            "label": describe.get("label"),
            "model": describe.get("model", getattr(self.engine, "model_id", None)),
            "license": getattr(self.engine, "license", None),
            "beams": describe.get("beams"),
            "source_languages": list(SOURCE_LANGUAGES),
            "target_languages": self.deepl_targets(),
            "load_seconds": self.load_seconds,
            "uptime_seconds": round(time.time() - self.started),
            "requests": self.requests,
            "character_count": self.character_count,
            "auth_required": self.auth_key is not None,
            "version": f"TRANSLAX {version_mod.VERSION}",
        }


class TranslaxHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    # Sous Windows, SO_REUSEADDR permet à DEUX serveurs d'écouter le même
    # port : refusé, un second lancement doit échouer clairement.
    allow_reuse_address = False
    state: ServerState


# --------------------------------------------------------------------------
# Requêtes HTTP


class _HttpError(Exception):
    def __init__(self, status: int, message: str, headers: dict | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.headers = headers or {}


class Handler(BaseHTTPRequestHandler):
    server_version = f"TRANSLAX/{version_mod.VERSION}"
    protocol_version = "HTTP/1.1"
    server: TranslaxHTTPServer

    # Les journaux par défaut de http.server (une ligne par requête, sur
    # stderr) sont remplacés par des lignes plus utiles (voir _translate).
    def log_message(self, format, *args):  # noqa: A002 - signature imposée
        pass

    @property
    def state(self) -> ServerState:
        return self.server.state

    # ------------------------------------------------------------ réponses

    def _cors_headers(self) -> dict:
        origin = self.headers.get("Origin")
        if origin and self._origin_allowed(origin):
            return {"Access-Control-Allow-Origin": origin, "Vary": "Origin"}
        return {}

    def _send(self, status: int, body: bytes, content_type: str, headers: dict | None = None) -> None:
        self.send_response(status)
        all_headers = {"Content-Type": content_type, "Content-Length": str(len(body)),
                       "Cache-Control": "no-store", **self._cors_headers(), **(headers or {})}
        for name, value in all_headers.items():
            self.send_header(name, value)
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, status: int, payload, headers: dict | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", headers)

    def _send_error_json(self, error: _HttpError) -> None:
        # Le corps d'une requête refusée n'a pas toujours été lu : fermer la
        # connexion évite de le relire comme une nouvelle requête.
        self.close_connection = True
        self._send_json(error.status, {"message": error.message}, error.headers)

    # ------------------------------------------------------------ contrôles

    def _origin_allowed(self, origin: str) -> bool:
        origins = self.state.cors_origins
        return "*" in origins or origin.rstrip("/") in origins

    def _check_host_and_origin(self) -> None:
        host = (self.headers.get("Host") or "").strip().lower()
        if host:
            hostname = host[1:].split("]")[0] if host.startswith("[") else host.rsplit(":", 1)[0]
            if hostname not in ("127.0.0.1", "localhost", "::1"):
                raise _HttpError(HTTPStatus.FORBIDDEN, f"Hôte refusé : {host} (serveur local uniquement).")
        origin = self.headers.get("Origin")
        if origin is not None and not self._origin_allowed(origin):
            raise _HttpError(
                HTTPStatus.FORBIDDEN,
                f"Origine non autorisée : {origin}. Origines permises : {', '.join(self.state.cors_origins)} "
                "(option --cors-origin).",
            )

    def _check_auth(self, params: dict | None = None) -> None:
        key = self.state.auth_key
        if key is None:
            return
        supplied = None
        header = self.headers.get("Authorization") or ""
        scheme, _, value = header.partition(" ")
        if scheme.lower() == "deepl-auth-key" and value.strip():
            supplied = value.strip()
        elif params:
            candidate = params.get("auth_key")
            if isinstance(candidate, list):
                candidate = candidate[0] if candidate else None
            supplied = candidate
        if not supplied or not hmac.compare_digest(str(supplied).encode("utf-8"), key.encode("utf-8")):
            raise _HttpError(HTTPStatus.FORBIDDEN, "Authorization failure, check auth_key")

    def _read_params(self) -> dict:
        length_header = self.headers.get("Content-Length")
        if length_header is None:
            self.close_connection = True
            raise _HttpError(HTTPStatus.LENGTH_REQUIRED, "En-tête Content-Length manquant.")
        try:
            length = int(length_header)
        except ValueError as exc:
            self.close_connection = True
            raise _HttpError(HTTPStatus.BAD_REQUEST, "Content-Length invalide.") from exc
        if length > MAX_BODY_BYTES:
            self.close_connection = True
            raise _HttpError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                             f"Requête trop grosse ({length} octets, maximum {MAX_BODY_BYTES}).")
        raw = self.rfile.read(length) if length else b""
        try:
            body = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _HttpError(HTTPStatus.BAD_REQUEST, "Le corps de la requête doit être en UTF-8.") from exc
        content_type = self.headers.get_content_type()
        if content_type == "application/json" or (content_type != "application/x-www-form-urlencoded"
                                                  and body.lstrip().startswith("{")):
            try:
                params = json.loads(body) if body.strip() else {}
            except json.JSONDecodeError as exc:
                raise _HttpError(HTTPStatus.BAD_REQUEST, f"JSON invalide : {exc}") from exc
            if not isinstance(params, dict):
                raise _HttpError(HTTPStatus.BAD_REQUEST, "Le corps JSON doit être un objet.")
            return params
        # Formulaire encodé, comme les clients DeepL historiques : plusieurs
        # paramètres `text` possibles.
        form = parse_qs(body, keep_blank_values=True)
        params: dict = {}
        for name, values in form.items():
            params[name] = values if name in ("text", "keep_terms") else values[-1]
        return params

    # ------------------------------------------------------------ verbes

    def do_OPTIONS(self):  # noqa: N802 - nom imposé par http.server
        try:
            self._check_host_and_origin()
            if self.headers.get("Origin") is None:
                raise _HttpError(HTTPStatus.BAD_REQUEST, "Requête OPTIONS sans en-tête Origin.")
        except _HttpError as error:
            self._send_error_json(error)
            return
        headers = {
            "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
            "Access-Control-Allow-Private-Network": "true",
            "Access-Control-Max-Age": "600",
            "Vary": "Origin, Access-Control-Request-Headers, Access-Control-Request-Private-Network",
        }
        self._send(HTTPStatus.NO_CONTENT, b"", "text/plain; charset=utf-8", headers)

    def do_GET(self):  # noqa: N802
        url = urlsplit(self.path)
        path = url.path.rstrip("/") or "/"
        query = {name: values[-1] for name, values in parse_qs(url.query).items()}
        try:
            self._check_host_and_origin()
            if path in ("/", "/health", "/v2/health"):
                self._send_json(HTTPStatus.OK, self.state.health())
                return
            self._check_auth(query)
            if path == "/v2/languages":
                self._send_json(HTTPStatus.OK, self._languages(query.get("type", "source")))
            elif path == "/v2/usage":
                self._send_json(HTTPStatus.OK, {"character_count": self.state.character_count,
                                                "character_limit": 1_000_000_000_000})
            elif path == "/v2/glossary-language-pairs":
                pairs = [{"source_lang": "fr", "target_lang": iso}
                         for iso in dict.fromkeys(TARGET_LANGUAGES[c][0] for c in self.state.deepl_targets())]
                self._send_json(HTTPStatus.OK, {"supported_languages": pairs})
            elif path == "/v2/glossaries":
                self._send_json(HTTPStatus.OK, {"glossaries": self.state.glossaries.list()})
            elif path.startswith("/v2/glossaries/"):
                self._get_glossary(path[len("/v2/glossaries/"):])
            else:
                raise _HttpError(HTTPStatus.NOT_FOUND, f"Chemin inconnu : {path}")
        except _HttpError as error:
            self._send_error_json(error)

    def do_POST(self):  # noqa: N802
        path = urlsplit(self.path).path.rstrip("/")
        try:
            self._check_host_and_origin()
            params = self._read_params()
            self._check_auth(params)
            if path == "/v2/translate":
                self._translate(params)
            elif path == "/v2/glossaries":
                self._create_glossary(params)
            else:
                raise _HttpError(HTTPStatus.NOT_FOUND, f"Chemin inconnu : {path}")
        except _HttpError as error:
            self._send_error_json(error)
        except Exception as exc:  # noqa: BLE001 - jamais de trace brute renvoyée au navigateur
            self.state.log(f"ERREUR interne : {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"message": f"Erreur interne : {type(exc).__name__}: {exc}"})

    def do_DELETE(self):  # noqa: N802
        path = urlsplit(self.path).path.rstrip("/")
        try:
            self._check_host_and_origin()
            self._check_auth()
            if not path.startswith("/v2/glossaries/"):
                raise _HttpError(HTTPStatus.NOT_FOUND, f"Chemin inconnu : {path}")
            if not self.state.glossaries.delete(path[len("/v2/glossaries/"):]):
                raise _HttpError(HTTPStatus.NOT_FOUND, "Glossaire introuvable.")
            self._send(HTTPStatus.NO_CONTENT, b"", "text/plain; charset=utf-8")
        except _HttpError as error:
            self._send_error_json(error)

    # ------------------------------------------------------------ points d'entrée

    def _languages(self, kind: str) -> list[dict]:
        if kind == "target":
            return [{"language": code, "name": TARGET_LANGUAGES[code][1], "supports_formality": False}
                    for code in self.state.deepl_targets()]
        return [{"language": code, "name": name} for code, name in SOURCE_LANGUAGES.items()]

    def _resolve_target(self, value) -> tuple[str, str]:
        code = str(value or "").strip().upper()
        if not code:
            raise _HttpError(HTTPStatus.BAD_REQUEST, "Parameter 'target_lang' not specified.")
        code = TARGET_ALIASES.get(code, code)
        if code not in TARGET_LANGUAGES:
            raise _HttpError(HTTPStatus.BAD_REQUEST,
                             f"Value for 'target_lang' not supported: {value} "
                             f"(langues permises : {', '.join(TARGET_LANGUAGES)}).")
        iso = TARGET_LANGUAGES[code][0]
        if iso not in self.state.engine.supported_targets():
            raise _HttpError(HTTPStatus.BAD_REQUEST,
                             f"Langue cible {code} indisponible avec le moteur {self.state.engine.key} "
                             f"(disponibles : {', '.join(self.state.deepl_targets()) or 'aucune'}).")
        return code, iso

    def _translate(self, params: dict) -> None:
        texts = params.get("text")
        if isinstance(texts, str):
            texts = [texts]
        if not texts or not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
            raise _HttpError(HTTPStatus.BAD_REQUEST, "Parameter 'text' not specified.")
        if len(texts) > self.state.max_texts:
            raise _HttpError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                             f"Trop de textes : {len(texts)} (maximum {self.state.max_texts} par requête).")
        code, iso = self._resolve_target(params.get("target_lang"))
        source = str(params.get("source_lang") or "FR").strip().upper()
        if source not in SOURCE_LANGUAGES:
            raise _HttpError(HTTPStatus.BAD_REQUEST,
                             f"Value for 'source_lang' not supported: {source} (TRANSLAX traduit depuis le français).")
        split_sentences = str(params.get("split_sentences", "1")).strip().lower() not in ("0", "false")
        try:
            glossary: dict[str, str] = {}
            glossary_id = params.get("glossary_id")
            if glossary_id:
                item = self.state.glossaries.get(str(glossary_id))
                if item is None:
                    raise _HttpError(HTTPStatus.NOT_FOUND, f"Glossaire introuvable : {glossary_id}")
                if item["target_lang"] != iso:
                    raise _HttpError(HTTPStatus.BAD_REQUEST,
                                     f"Le glossaire {glossary_id} est fait pour {item['target_lang']}, pas pour {iso}.")
                glossary.update(item["entries"])
            glossary.update(coerce_glossary(params.get("glossary_entries")))
            keep_terms = coerce_terms(params.get("keep_terms"))
        except GlossaryError as exc:
            raise _HttpError(HTTPStatus.BAD_REQUEST, str(exc)) from exc

        state = self.state
        if state.status != "ready":
            message = ("Modèle en cours de chargement, réessayer dans un instant."
                       if state.status == "loading" else f"Moteur indisponible : {state.error}")
            raise _HttpError(HTTPStatus.SERVICE_UNAVAILABLE, message, {"Retry-After": "10"})
        try:
            results = state.service.translate(texts, iso, keep_terms=keep_terms, glossary=glossary,
                                              split_sentences=split_sentences)
        except server_engines.EngineRefused as exc:
            raise _HttpError(HTTPStatus.FORBIDDEN, str(exc)) from exc
        except server_engines.EngineUnavailable as exc:
            raise _HttpError(HTTPStatus.SERVICE_UNAVAILABLE, str(exc)) from exc
        state.count(sum(len(t) for t in texts))
        translations = []
        for result in results:
            item = {"detected_source_language": "FR", "text": result.text}
            if result.warnings:
                item["warnings"] = result.warnings
            translations.append(item)
        stats = state.service.last_stats
        state.log(
            f"{len(texts)} texte(s) FR -> {code} en {stats.get('seconds', 0):.1f} s "
            f"({stats.get('segments', 0)} phrases, {stats.get('model_inputs', 0)} envoyées au modèle, "
            f"{stats.get('repaired', 0)} réparées, {stats.get('pieces_segments', 0)} retraduites par morceaux, "
            f"{stats.get('warnings', 0)} texte(s) avec avertissement)"
        )
        self._send_json(HTTPStatus.OK, {"translations": translations})

    def _create_glossary(self, params: dict) -> None:
        name = str(params.get("name") or "").strip()
        source = str(params.get("source_lang") or "").strip().lower()
        target = str(params.get("target_lang") or "").strip().lower()
        entries_text = params.get("entries")
        entries_format = str(params.get("entries_format") or "tsv").strip().lower()
        if not name or not source or not target or entries_text in (None, ""):
            raise _HttpError(HTTPStatus.BAD_REQUEST,
                             "Paramètres requis : name, source_lang, target_lang, entries.")
        if source != "fr":
            raise _HttpError(HTTPStatus.BAD_REQUEST, "source_lang doit être fr.")
        target_iso = TARGET_LANGUAGES.get(TARGET_ALIASES.get(target.upper(), target.upper()), (target, ""))[0]
        if target_iso not in server_engines.SERVER_TARGETS:
            raise _HttpError(HTTPStatus.BAD_REQUEST, f"target_lang non pris en charge : {target}")
        try:
            entries = parse_glossary_entries(str(entries_text), entries_format)
        except GlossaryError as exc:
            raise _HttpError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
        if not entries:
            raise _HttpError(HTTPStatus.BAD_REQUEST, "Glossaire vide.")
        info = self.state.glossaries.create(name, source, target_iso, entries)
        self.state.log(f"Glossaire créé : {name} (fr -> {target_iso}, {len(entries)} entrées)")
        self._send_json(HTTPStatus.CREATED, info)

    def _get_glossary(self, rest: str) -> None:
        glossary_id, _, tail = rest.partition("/")
        item = self.state.glossaries.get(glossary_id)
        if item is None:
            raise _HttpError(HTTPStatus.NOT_FOUND, "Glossaire introuvable.")
        if tail == "entries":
            body = "".join(f"{src}\t{tgt}\n" for src, tgt in item["entries"].items()).encode("utf-8")
            self._send(HTTPStatus.OK, body, "text/tab-separated-values; charset=utf-8")
        elif tail == "":
            self._send_json(HTTPStatus.OK, GlossaryStore._info(item))  # noqa: SLF001
        else:
            raise _HttpError(HTTPStatus.NOT_FOUND, f"Chemin inconnu : /v2/glossaries/{rest}")


# --------------------------------------------------------------------------
# Démarrage


def build_server(
    engine,
    *,
    port: int = DEFAULT_PORT,
    auth_key: str | None = None,
    cors_origins: tuple[str, ...] | list[str] = DEFAULT_CORS_ORIGINS,
    max_texts: int = MAX_TEXTS,
    glossary_path: Path | None = None,
    log=_log,
) -> TranslaxHTTPServer:
    """Serveur prêt à servir (pas encore lancé), lié à 127.0.0.1 uniquement.
    Lève `server_engines.EngineRefused` pour un moteur sans licence commerciale."""
    state = ServerState(engine, auth_key=auth_key, cors_origins=cors_origins, max_texts=max_texts,
                        glossary_path=glossary_path, log=log)
    httpd = TranslaxHTTPServer((HOST, port), Handler)
    httpd.state = state
    return httpd


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="translax serve",
        description=(
            "Serveur de traduction local compatible avec l'API DeepL v2 (127.0.0.1 uniquement). "
            "Moteurs à licence commerciale seulement : NLLB est refusé."
        ),
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port d'écoute (défaut {DEFAULT_PORT})")
    parser.add_argument("--engine", default=server_engines.DEFAULT_SERVER_ENGINE,
                        help="moteur : " + ", ".join(server_engines.SERVER_ENGINES)
                        + f" (défaut {server_engines.DEFAULT_SERVER_ENGINE})")
    parser.add_argument("--beams", type=int, default=None,
                        help=f"taille du faisceau (défaut {server_engines.DEFAULT_BEAMS} pour MADLAD-400)")
    parser.add_argument("--threads", type=int, default=None, help="fils de calcul (défaut : tous les cœurs)")
    parser.add_argument("--auth-key", default=None,
                        help=f"clé exigée dans « Authorization: DeepL-Auth-Key <clé> » (ou variable {AUTH_KEY_ENV})")
    parser.add_argument("--cors-origin", action="append", default=None, metavar="ORIGINE",
                        help="origine autorisée (répétable ; remplace la liste par défaut : "
                        + ", ".join(DEFAULT_CORS_ORIGINS) + ")")
    parser.add_argument("--max-texts", type=int, default=MAX_TEXTS,
                        help=f"textes maximum par requête (défaut {MAX_TEXTS}, comme DeepL)")
    parser.add_argument("--autoriser-telechargement", dest="allow_download", action="store_true",
                        help="autorise le téléchargement des poids absents du cache (MADLAD-400 : 11,8 Go)")
    return parser


def main(argv: list[str] | None = None) -> int:
    # Journal lisible même redirigé vers un fichier (sinon cp1252 sous
    # Windows, et les accents deviennent illisibles dans un éditeur UTF-8).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = _parser().parse_args(argv)

    try:
        spec = server_engines.check_engine_allowed(args.engine)
    except server_engines.EngineRefused as exc:
        print(f"Refus : {exc}", file=sys.stderr)
        return 2

    engine = server_engines.create_engine(spec.key, beams=args.beams, threads=args.threads,
                                          allow_download=args.allow_download)
    auth_key = args.auth_key or os.environ.get(AUTH_KEY_ENV) or None
    origins = tuple(args.cors_origin) if args.cors_origin else DEFAULT_CORS_ORIGINS
    try:
        httpd = build_server(engine, port=args.port, auth_key=auth_key, cors_origins=origins,
                             max_texts=args.max_texts, glossary_path=default_glossary_path())
    except server_engines.EngineRefused as exc:
        print(f"Refus : {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"Impossible d'écouter sur {HOST}:{args.port} ({exc}). Port déjà utilisé ? "
              "Choisir un autre port avec --port.", file=sys.stderr)
        return 1

    port = httpd.server_address[1]
    _log(f"TRANSLAX {version_mod.VERSION} -- serveur de traduction compatible DeepL")
    _log(f"Moteur : {spec.label} (licence {spec.license})")
    _log(f"Adresse : http://{HOST}:{port}/v2/translate   (état : http://{HOST}:{port}/health)")
    _log("Origines autorisées : " + ", ".join(origins))
    _log("Clé d'accès : " + ("exigée" if auth_key else "aucune (serveur local, 127.0.0.1 seulement)"))
    _log("Chargement du modèle en arrière-plan… (Ctrl+C pour arrêter)")
    threading.Thread(target=httpd.state.load_engine, name="chargement-modele", daemon=True).start()
    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        _log("Arrêt demandé.")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
