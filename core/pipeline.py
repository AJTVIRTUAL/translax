"""
Orchestration complète : fichier d'entrée -> fichier .md traduit.

C'est le seul module que l'interface a besoin d'appeler. Il enchaîne
extraction -> nettoyage des pages -> segmentation -> traduction ->
nettoyage typographique -> renommage selon le titre traduit, gère le
nommage automatique de la sortie et la reprise après interruption.

Règle de nommage : le nom dérivé du fichier source avec l'extension .md
(IlluVol1.txt -> IlluVol1.md, `default_output_path`) sert de point de
départ pour toute résolution de chemin -- reprise comprise, voir
`state.resolve_output_path` -- mais PAS forcément le nom sous lequel le
travail est réellement écrit. Dès qu'un job démarre vraiment de zéro
(`job.translate_title`, activé par défaut), le titre est traduit AVANT que
le premier segment ne soit traduit, et le fichier est créé directement
sous ce nom : « i am.pdf » -> « je suis.md » dès le premier segment écrit,
pas seulement une fois tout terminé (demande explicite de l'utilisateur).
Un job REPRIS (interrompu puis relancé) retrouve ce nom déjà établi via un
petit fichier « pointeur » (voir `core/state.py`) associé au fichier
source -- sans lui, un job renommé dès le début serait introuvable au
lancement suivant. Le bloc de renommage en fin de fonction reste un filet
de sécurité pour un job démarré avant cette fonctionnalité (voir
`_translate_title` pour le détail).

`job.extract_only` (Extraire / Extraire X) court-circuite tout ce qui suit
la segmentation : ni modèle NLLB chargé, ni titre traduit, ni renommage --
juste le texte nettoyé (en-têtes/pieds de page, vision IA le cas échéant),
structuré en Markdown, dans sa langue d'origine (voir
`translate.write_segments_plain`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import extract, page_cleanup, pdf_export, postprocess, segment as segment_mod, state as state_mod, translate, vision_ocr
from .languages import DEFAULT_SOURCE, DEFAULT_TARGET

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MAX_TITLE_LEN = 120


@dataclass
class Job:
    input_path: Path
    output_path: Path | None = None      # None -> nommage automatique
    src_lang: str = DEFAULT_SOURCE
    tgt_lang: str = DEFAULT_TARGET
    model_key: str = translate.DEFAULT_MODEL_KEY
    num_beams: int = translate.DEFAULT_NUM_BEAMS
    strategy: str = "auto"               # auto | blocks | flow
    target_words: int = segment_mod.DEFAULT_TARGET_WORDS
    cleanup: bool = True
    translate_title: bool = True         # renomme le .md selon le titre traduit en fin de job
    resume: str = "auto"                 # auto | restart
    limit: int | None = None             # test : ne traiter que N segments
    threads: int | None = None
    use_vision_ocr: bool = False         # Traduire X : extraction par vision IA (voir core/vision_ocr.py)
    # "paddleocr" (local, gratuit, par défaut -- voir OCR_VLM_COMPARATIF.md)
    # ou "anthropic" (clé API requise, payant, meilleure compréhension du
    # contexte sur les cas difficiles). anthropic_api_key n'est utilisé que
    # si vision_provider="anthropic".
    vision_provider: str = "paddleocr"
    anthropic_api_key: str | None = None       # requis seulement si use_vision_ocr ET vision_provider="anthropic"
    vision_model: str = vision_ocr.DEFAULT_MODEL
    extract_only: bool = False           # Extraire (X) : même nettoyage, sans traduction (aucun modèle NLLB chargé)
    # "md" (par défaut, inchangé) ou "pdf" -- demande explicite de
    # l'utilisateur (25/08/2026). Le .md reste TOUJOURS écrit et reste seul
    # utilisé pour la reprise/l'état (voir core/pdf_export.py) : le PDF est
    # généré EN PLUS, à la toute fin, jamais à la place.
    output_format: str = "md"

    def resolved_output(self) -> Path:
        return Path(self.output_path) if self.output_path else default_output_path(self.input_path)


@dataclass
class Result:
    output_path: Path
    total_segments: int
    translated_segments: int
    resumed_from: int = 0
    cancelled: bool = False
    cleanup_report: postprocess.CleanupReport | None = None
    page_cleanup_report: page_cleanup.PageCleanupReport | None = None
    vision_ocr_report: vision_ocr.VisionOcrReport | None = None
    renamed_from: Path | None = None     # nom d'avant renommage, si le titre a été traduit
    pdf_path: Path | None = None         # rempli seulement si job.output_format == "pdf" (voir core/pdf_export.py)
    notes: list[str] = field(default_factory=list)


def default_output_path(input_path: Path, output_dir: Path | None = None) -> Path:
    """<dossier de sortie ou dossier source>/<nom du source>.md"""
    input_path = Path(input_path)
    folder = Path(output_dir) if output_dir else input_path.parent
    return folder / (input_path.stem + ".md")


def _maybe_export_pdf(job: "Job", md_path: Path, status, notes: list[str]) -> Path | None:
    """
    Génère un PDF à côté du .md si `job.output_format == "pdf"` -- jamais à
    la place (voir la docstring de `Job.output_format`). Une erreur de
    rendu PDF (police manquante, contenu inattendu...) ne fait jamais
    échouer tout le job : le .md, déjà écrit et valide, reste le résultat
    utilisable, avec une note claire plutôt qu'un plantage pour un format
    d'export secondaire.
    """
    if job.output_format != "pdf":
        return None
    pdf_path = md_path.with_suffix(".pdf")
    status(f"Export PDF : {pdf_path.name}…")
    try:
        pdf_export.markdown_file_to_pdf(md_path, pdf_path)
    except Exception as exc:  # noqa: BLE001 - jamais faire échouer le job pour l'export PDF
        notes.append(f"Export PDF échoué ({exc}) -- le fichier .md reste disponible.")
        return None
    status("Export PDF terminé.")
    return pdf_path


def _sanitize_filename(name: str) -> str:
    """Retire les caractères invalides sur Windows/Mac/Linux et les
    espaces/points parasites en bord de nom."""
    name = _INVALID_FILENAME_CHARS.sub("", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:_MAX_TITLE_LEN].strip()


def _translate_title(engine: "translate.PreciseEngine | translate.FastEngine", stem: str) -> str | None:
    """
    Traduit le NOM du fichier (pas son contenu) pour nommer la sortie dans
    la langue cible -- « i am » -> « je suis ». Retourne None si la
    traduction échoue ou ne produit rien d'exploitable : on garde alors le
    nom d'origine plutôt que de faire échouer toute la traduction du
    document pour un simple nom de fichier.
    """
    normalized = re.sub(r"[_\-]+", " ", stem).strip()
    if not normalized:
        return None
    try:
        translated = engine.translate(normalized)
    except Exception:
        return None
    cleaned = _sanitize_filename(translated)
    return cleaned or None


def _unique_path(path: Path) -> Path:
    """Évite d'écraser un fichier déjà présent sous ce nom (rare, mais deux
    titres différents peuvent se traduire de façon identique)."""
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    n = 2
    while True:
        candidate = parent / f"{stem} ({n}){suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def run_job(
    job: Job,
    *,
    on_status=None,
    on_progress=None,
    should_stop=None,
    on_page_cleanup=None,
    heartbeat=None,
    on_vision_review=None,
    on_vision_progress=None,
) -> Result:
    """
    Exécute le job du début à la fin.

    `on_status(str)`        : messages d'étape (chargement, extraction…)
    `on_progress(Progress)` : après chaque segment traduit
    `should_stop()`         : retourne True pour interrompre proprement
    `on_page_cleanup(report)` : appelé UNIQUEMENT si des en-têtes/pieds de
        page répétés ou des numéros de page ont été détectés (voir
        `core/page_cleanup.py`) ; doit retourner "clean" (utiliser le texte
        nettoyé), "original" (ignorer la détection) ou "cancel" (abandonner
        le job). Sans callback (None), "clean" est appliqué automatiquement
        -- comportement adapté à un usage non interactif (`cli.py`).
    `heartbeat` (`heartbeat.Heartbeat`, optionnel) : transmis au moteur pour
        horodater chaque pas de génération À L'INTÉRIEUR d'un segment (voir
        `core/heartbeat.py`) -- sert au bouton Reboost de l'interface, sans
        influencer la traduction elle-même.
    `on_vision_review(report)` : appelé UNIQUEMENT si `job.use_vision_ocr`
        (bouton Traduire X, voir `core/vision_ocr.py`), une fois toutes les
        pages transcrites ; doit retourner "continue" ou "cancel". Sans
        callback (None), "continue" est appliqué automatiquement.
    `on_vision_progress(page_faite, total, report_partiel)` : appelé après
        chaque page transcrite par la vision (avant la traduction) --
        distinct de `on_progress`, qui ne concerne que la traduction des
        segments.

    Une interruption (Stop, ou "cancel" au rapport de nettoyage) n'est pas
    une erreur : le job revient avec `Result.cancelled = True`. Pour un
    Stop pendant la traduction, les segments déjà traduits restent écrits
    et l'état permet de reprendre au relancement ; annuler au stade du
    rapport de nettoyage se produit avant qu'aucun segment ne soit écrit.
    """
    def status(message: str) -> None:
        if on_status:
            on_status(message)

    input_path = Path(job.input_path)
    original_out_path = job.resolved_output()
    # Redirige vers le vrai nom de sortie si ce job a déjà été renommé selon
    # son titre traduit lors d'un lancement précédent (voir
    # state.resolve_output_path) -- sans ça, un job interrompu après ce
    # renommage ne serait jamais retrouvé au lancement suivant, puisque
    # `original_out_path` (dérivé du seul fichier source) ne correspond
    # plus au nom réel. Ne charge jamais le modèle : simple lecture JSON.
    out_path = state_mod.resolve_output_path(original_out_path, input_path)
    notes: list[str] = []

    if not input_path.exists():
        raise FileNotFoundError(f"Fichier introuvable : {input_path}")
    if not extract.is_supported(input_path):
        raise extract.UnsupportedFormat(f"Format non pris en charge : {input_path.suffix}")

    # --- Reprise éventuelle -------------------------------------------------
    previous = None if job.resume == "restart" else state_mod.can_resume(out_path, input_path)
    cached_segments = state_mod.segments_path(out_path)
    page_report = None

    vision_report = None
    if previous is not None and cached_segments.exists():
        segments = segment_mod.load_segments(cached_segments)
        start_index = min(previous.done, len(segments))
        job_state = previous
        strategy = previous.strategy or job.strategy
        status(f"Reprise : {start_index}/{len(segments)} segments déjà traduits.")
        # Reprendre avec un moteur différent de celui d'origine est
        # explicitement permis (voir ui/main_window.py::_offer_resume_pending_jobs,
        # « Choisir un autre moteur… ») -- les segments déjà écrits ne sont
        # jamais retouchés, seuls ceux qui restent passeront par le nouveau
        # moteur. `job_state.model` (juste une métadonnée persistée, jamais
        # lue par ce module pour décider quoi charger -- voir plus bas) est
        # mis à jour ici pour ne pas mentir sur quel moteur a réellement
        # traduit la suite du document.
        if job_state.model != job.model_key:
            status(f"Reprise avec un moteur différent : « {job_state.model} » -> « {job.model_key} ».")
            job_state.model = job.model_key
    else:
        if job.use_vision_ocr:
            using_local_ocr = job.vision_provider != "anthropic"
            status(
                "Extraction par OCR local (PaddleOCR, gratuit)…" if using_local_ocr
                else f"Extraction par vision IA ({job.vision_model})…"
            )
            vision_cache = state_mod.work_dir(out_path) / (input_path.stem + ".vision_cache.jsonl")

            def vision_progress(done: int, total: int, partial_report) -> None:
                last_page = partial_report.pages[done - 1]
                if last_page.vision_failed:
                    status(
                        f"Vision : page {done}/{total} bloquée par le filtre de contenu Anthropic -- "
                        "texte original conservé, la traduction continue."
                    )
                elif using_local_ocr:
                    status(f"OCR local : page {done}/{total} transcrite…")
                else:
                    status(f"Vision : page {done}/{total} transcrite (~{partial_report.estimated_cost_usd():.2f} $)…")
                if on_vision_progress is not None:
                    on_vision_progress(done, total, partial_report)

            try:
                if using_local_ocr:
                    raw_text, vision_report = vision_ocr.extract_text_paddleocr(
                        input_path,
                        vision_cache,
                        src_lang=job.src_lang,
                        on_progress=vision_progress,
                        should_stop=should_stop,
                    )
                else:
                    raw_text, vision_report = vision_ocr.extract_text_vision(
                        input_path,
                        job.anthropic_api_key or "",
                        vision_cache,
                        model=job.vision_model,
                        on_progress=vision_progress,
                        should_stop=should_stop,
                    )
            except translate.Cancelled as exc:
                # Rien à traduire encore (l'arrêt a eu lieu avant la
                # segmentation) -- mais les pages déjà transcrites restent
                # dans le cache : relancer Traduire X sur ce même fichier ne
                # les repaiera pas (voir core/vision_ocr.py).
                return Result(
                    output_path=out_path,
                    total_segments=0,
                    translated_segments=0,
                    cancelled=True,
                    notes=[f"{exc} Relancer Traduire X reprendra sans repayer les pages déjà transcrites."],
                )
            failed_note = (
                f", {vision_report.failed_count} bloquée(s) par le filtre de contenu (texte original conservé)"
                if vision_report.failed_count else ""
            )
            status(
                f"Extraction vision terminée : {vision_report.changed_count} page(s) corrigée(s), "
                f"{vision_report.flagged_count} signalée(s) comme incertaine(s){failed_note} -- "
                f"~{vision_report.estimated_cost_usd():.2f} $ estimé."
            )
            decision = on_vision_review(vision_report) if on_vision_review is not None else "continue"
            if decision == "cancel":
                return Result(
                    output_path=out_path,
                    total_segments=0,
                    translated_segments=0,
                    cancelled=True,
                    vision_ocr_report=vision_report,
                    notes=["Annulé après revue de l'extraction vision — aucun segment n'a été traduit."],
                )
        else:
            status(f"Extraction du texte ({input_path.suffix.lower().lstrip('.') or 'texte'})…")
            raw_text = extract.extract_text(
                input_path,
                on_progress=lambda page, total: status(f"Extraction page {page}/{total}…"),
            )

        # --- Nettoyage des en-têtes/pieds de page répétés et numéros ------
        cleaned_text, page_report = page_cleanup.clean_pdf_pages(raw_text)
        page_decision = "clean"
        if page_report.groups:
            status(
                f"{page_report.lines_removed} ligne(s) d'en-tête/pied de page ou de "
                f"numérotation détectées sur {page_report.total_pages} pages."
            )
            page_decision = on_page_cleanup(page_report) if on_page_cleanup is not None else "clean"
            if page_decision == "cancel":
                return Result(
                    output_path=out_path,
                    total_segments=0,
                    translated_segments=0,
                    cancelled=True,
                    page_cleanup_report=page_report,
                    notes=["Annulé après revue du nettoyage des pages — aucun segment n'a été traduit."],
                )
        raw_text = cleaned_text if page_decision == "clean" else raw_text

        strategy = segment_mod.detect_strategy(raw_text) if job.strategy == "auto" else job.strategy
        status(
            "Segmentation : "
            + ("paragraphes détectés par lignes vides" if strategy == "blocks"
               else "texte continu, paragraphes reconstruits par phrases")
        )
        segments = segment_mod.segment_text(raw_text, strategy=strategy, target_words=job.target_words)
        if job.limit:
            segments = segments[: job.limit]

        segment_mod.save_segments(segments, cached_segments)
        start_index = 0
        job_state = state_mod.JobState(
            source_path=str(input_path),
            source_hash=state_mod.source_hash(input_path),
            total=len(segments),
            done=0,
            src_lang=job.src_lang,
            tgt_lang=job.tgt_lang,
            model=job.model_key,
            strategy=strategy,
        )
        state_mod.save_state(out_path, job_state)

    status(f"{len(segments)} segments à {'extraire' if job.extract_only else 'traduire'}.")

    # --- Extraction seulement (Extraire / Extraire X) -----------------------
    # Bénéficie de tout ce qui précède (nettoyage des en-têtes/pieds de
    # page, vision IA le cas échéant, segmentation) SANS traduire : aucun
    # modèle NLLB n'est chargé, aucun titre n'est traduit -- le texte reste
    # dans sa langue d'origine, juste nettoyé et structuré en Markdown.
    if job.extract_only:
        status("Écriture du texte extrait (sans traduction)…")
        done = translate.write_segments_plain(segments, out_path, job_state=job_state)
        report = None
        if job.cleanup:
            status("Nettoyage des titres et des traits d'union…")
            report = postprocess.cleanup_file(segments, out_path, apply=True)
            status(report.summary())
        pdf_path = _maybe_export_pdf(job, out_path, status, notes)
        return Result(
            output_path=out_path,
            total_segments=len(segments),
            translated_segments=done,
            resumed_from=start_index,
            cancelled=False,
            cleanup_report=report,
            page_cleanup_report=page_report,
            vision_ocr_report=vision_report,
            pdf_path=pdf_path,
            notes=notes,
        )

    # --- Traduction ---------------------------------------------------------
    # Le moteur dépend du profil choisi (voir translate.MODEL_INFO[...].engine) :
    # "precise" -> transformers (moteur d'origine, validé) ; "fast" ->
    # CTranslate2 (voir translate.FastEngine) ; "opus-mt" -> Helsinki-NLP,
    # licence commerciale (voir translate.OpusMtEngine). Même interface
    # publique dans les trois cas (load/translate/unload) : rien d'autre
    # dans cette fonction n'a besoin de savoir lequel tourne réellement.
    _ENGINE_CLASSES = {
        "fast": translate.FastEngine,
        "opus-mt": translate.OpusMtEngine,
        "madlad": translate.MadladEngine,
    }
    engine_cls = _ENGINE_CLASSES.get(translate.MODEL_INFO[job.model_key].engine, translate.PreciseEngine)
    engine = engine_cls(
        model_key=job.model_key,
        src_lang=job.src_lang,
        tgt_lang=job.tgt_lang,
        num_beams=job.num_beams,
        threads=job.threads,
    )
    engine.load(on_status=status)

    # Inutile de retraduire le titre à chaque reprise : si `out_path` a déjà
    # été redirigé vers un nom établi lors d'un lancement précédent (voir le
    # pointeur en haut de fonction), c'est que le titre a déjà été traduit
    # et appliqué -- retraduire coûterait un appel modèle de plus pour un
    # résultat de toute façon déjà là (la traduction est déterministe).
    translated_title = None
    if job.translate_title and (start_index == 0 or out_path == original_out_path):
        translated_title = _translate_title(engine, input_path.stem)

    # --- Renommage AVANT la traduction du corps du texte --------------------
    # Demande explicite de l'utilisateur : que le .md porte déjà son nom
    # définitif dès le premier segment écrit, pas seulement une fois tout
    # terminé. Seulement pour un job qui démarre VRAIMENT de zéro
    # (start_index == 0) -- un job repris (partiellement traduit sous un nom
    # déjà établi lors d'un lancement précédent, avec ou sans pointeur) n'est
    # jamais renommé en cours de route : le bloc de renommage de fin (plus
    # bas) reste le filet de sécurité pour ce cas.
    if translated_title and start_index == 0 and translated_title.lower() != out_path.stem.lower():
        candidate = _unique_path(out_path.with_name(translated_title + out_path.suffix))
        segment_mod.save_segments(segments, state_mod.segments_path(candidate))
        state_mod.save_state(candidate, job_state)
        state_mod.save_output_pointer(original_out_path, candidate, job_state.source_hash)
        # Fichiers de travail de l'ancien nom : plus jamais lus une fois le
        # pointeur en place, autant ne pas les laisser traîner.
        state_mod.state_path(out_path).unlink(missing_ok=True)
        state_mod.segments_path(out_path).unlink(missing_ok=True)
        status(f"Fichier créé sous le titre traduit : {candidate.name}")
        out_path = candidate

    cancelled = False
    try:
        done = translate.translate_segments(
            segments,
            out_path,
            engine,
            start_index=start_index,
            job_state=job_state,
            on_progress=on_progress,
            should_stop=should_stop,
            heartbeat=heartbeat,
        )
    except translate.Cancelled:
        cancelled = True
        done = job_state.done
        notes.append(f"Traduction interrompue à {done}/{len(segments)} — relancer reprendra ici.")
    finally:
        engine.unload()

    # --- Nettoyage typographique --------------------------------------------
    report = None
    if job.cleanup and not cancelled:
        status("Nettoyage des titres et des traits d'union…")
        report = postprocess.cleanup_file(segments, out_path, apply=True)
        status(report.summary())

    # --- Renommage selon le titre traduit (filet de sécurité) ---------------
    # Ne s'applique en pratique qu'à un job REPRIS sans être passé par le
    # renommage précoce ci-dessus (démarré avant cette fonctionnalité, ou
    # interrompu avant que le titre ait pu être traduit) -- comparer au stem
    # de `out_path` (pas `input_path`) et pas à celui d'origine est ce qui
    # évite un second renommage inutile (« je suis.md » -> « je suis (2).md »)
    # quand le nom définitif a déjà été appliqué dès le départ.
    final_path = out_path
    renamed_from = None
    if translated_title and not cancelled and translated_title.lower() != out_path.stem.lower():
        candidate = _unique_path(out_path.with_name(translated_title + out_path.suffix))
        try:
            out_path.rename(candidate)
        except OSError as exc:
            notes.append(f"Le renommage en « {candidate.name} » a échoué ({exc}) — resté sous {out_path.name}.")
        else:
            final_path = candidate
            renamed_from = out_path
            status(f"Fichier renommé selon le titre traduit : {candidate.name}")

    pdf_path = _maybe_export_pdf(job, final_path, status, notes) if not cancelled else None
    return Result(
        output_path=final_path,
        total_segments=len(segments),
        translated_segments=done,
        resumed_from=start_index,
        cancelled=cancelled,
        cleanup_report=report,
        page_cleanup_report=page_report,
        vision_ocr_report=vision_report,
        renamed_from=renamed_from,
        pdf_path=pdf_path,
        notes=notes,
    )
