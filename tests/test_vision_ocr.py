"""
Tests de `core/vision_ocr.py` -- sans jamais appeler la vraie API Anthropic
(payante, réseau requis) : un faux client reproduit sa forme exacte,
y compris ses vraies classes d'exception (`anthropic.AuthenticationError`
etc., construites avec de vrais objets `httpx2.Request`/`Response`) pour
vérifier que le bon message d'erreur sort du bon type d'échec.

Validation sur un vrai document, avec la vraie API : voir SPEC.md (Hindu
Magical Occultism Test.pdf, ~0,02 $/page mesuré avec Sonnet 5) -- ce fichier
ne fait tourner que la mécanique, pas la qualité de transcription réelle.

    python tests/test_vision_ocr.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anthropic  # noqa: E402
import httpx2  # noqa: E402
import pymupdf  # noqa: E402

from core import languages, vision_ocr  # noqa: E402
from core.translate import Cancelled  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK   {label}")
    else:
        print(f"  ECHEC {label} {detail}")
        failures.append(label)


def make_pdf(path: Path, pages: list[str]) -> None:
    """PDF réel et minimal, créé par pymupdf lui-même -- pas besoin d'un
    vrai scan pour tester la mécanique d'extraction/cache/erreurs."""
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()


def canned_response(text: str, in_tok: int = 100, out_tok: int = 50):
    class _Block:
        type = "text"
        def __init__(self, t):
            self.text = t

    class _Usage:
        def __init__(self, i, o):
            self.input_tokens = i
            self.output_tokens = o

    class _Response:
        def __init__(self, t, i, o):
            self.content = [_Block(t)]
            self.usage = _Usage(i, o)

    return _Response(text, in_tok, out_tok)


class FakeMessages:
    def __init__(self, items):
        # `items` : liste d'objets réponse OU d'exceptions à lever, un par appel.
        self._items = items
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self._items[len(self.calls) - 1]
        if isinstance(item, BaseException):
            raise item
        return item


class FakeClient:
    def __init__(self, items):
        self.messages = FakeMessages(items)


def install_fake_anthropic(items) -> tuple[FakeClient, "callable"]:
    """Monkey-patch anthropic.Anthropic pour renvoyer un client factice --
    même technique que pipeline.translate.PreciseEngine ailleurs dans ce
    projet. Retourne (client factice, fonction de restauration)."""
    fake = FakeClient(items)
    original = anthropic.Anthropic
    anthropic.Anthropic = lambda api_key=None: fake  # type: ignore[assignment]

    def restore():
        anthropic.Anthropic = original  # type: ignore[assignment]

    return fake, restore


def sdk_error(cls, message: str, status_code: int = 400):
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    if cls is anthropic.APIConnectionError:
        return cls(message=message, request=req)
    resp = httpx2.Response(status_code, request=req)
    return cls(message, response=resp, body=None)


def content_filter_error():
    """Reproduit exactement la forme réelle rencontrée en pratique (voir
    SPEC.md) -- un vrai `anthropic.BadRequestError` avec le corps JSON tel
    que l'API le renvoie, pas une exception approximative."""
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx2.Response(400, request=req)
    body = {
        "type": "error",
        "error": {"type": "invalid_request_error", "message": "Output blocked by content filtering policy"},
        "request_id": "req_test",
    }
    return anthropic.BadRequestError("Output blocked by content filtering policy", response=resp, body=body)


def main() -> int:
    print("\n1. _parse_response")
    num, header, body, flagged = vision_ocr._parse_response(  # noqa: SLF001 - test délibéré
        'PAGE_NUMBER: 81\nHEADER: THOU ART ADMONISHED\n---\nSome real text.'
    )
    check("numéro de page extrait", num == "81")
    check("en-tête extrait", header == "THOU ART ADMONISHED")
    check("corps extrait", body == "Some real text.")
    check("non signalé sans balise <uncertain>", not flagged)

    num2, header2, body2, flagged2 = vision_ocr._parse_response(  # noqa: SLF001
        'PAGE_NUMBER: none\nHEADER: none\n---\nWord <uncertain>wbo</uncertain> kept.'
    )
    check("« none » devient None (numéro)", num2 is None)
    check("« none » devient None (en-tête)", header2 is None)
    check("le texte incertain est conservé, la balise retirée", body2 == "Word wbo kept.")
    check("signalé quand une balise <uncertain> est présente", flagged2)

    check("_is_content_filter_block reconnaît la vraie forme de l'erreur (voir SPEC.md)",
          vision_ocr._is_content_filter_block(content_filter_error()))  # noqa: SLF001
    other_400 = sdk_error(anthropic.BadRequestError, "model: claude-bogus-1 not found")
    check("_is_content_filter_block ne se déclenche PAS sur une autre erreur 400",
          not vision_ocr._is_content_filter_block(other_400))  # noqa: SLF001

    workdir = Path(tempfile.mkdtemp(prefix="translax_vision_"))
    try:
        pdf_path = workdir / "sample.pdf"
        make_pdf(pdf_path, ["Page one real content.", "Page two real content."])
        cache_path = workdir / ".translax" / "sample.vision_cache.jsonl"

        print("\n2. Extraction complète (deux pages), rapport et coût")
        fake, restore = install_fake_anthropic([
            canned_response("PAGE_NUMBER: 1\nHEADER: none\n---\nCorrected page one.", 1000, 200),
            canned_response("PAGE_NUMBER: 2\nHEADER: none\n---\nCorrected <uncertain>page</uncertain> two.", 1100, 210),
        ])
        try:
            text, report = vision_ocr.extract_text_vision(pdf_path, "fake-key", cache_path)
        finally:
            restore()

        check("2 pages transcrites", len(report.pages) == 2)
        check("texte joint par \\f", text == "Corrected page one.\fCorrected page two.")
        check("total_pages correct", report.total_pages == 2)
        check("tokens d'entrée cumulés", report.total_input_tokens == 2100, f"({report.total_input_tokens})")
        check("tokens de sortie cumulés", report.total_output_tokens == 410, f"({report.total_output_tokens})")
        check("une page signalée incertaine", report.flagged_count == 1, f"({report.flagged_count})")
        check("coût estimé positif et cohérent",
              0 < report.estimated_cost_usd() < 1, f"({report.estimated_cost_usd()})")
        check("le cache a été écrit sur disque (2 lignes)",
              cache_path.exists() and len(cache_path.read_text(encoding="utf-8").splitlines()) == 2)

        print("\n3. Reprise depuis le cache : aucun nouvel appel à l'API")
        fake2, restore2 = install_fake_anthropic([RuntimeError("ne doit jamais être appelé")])
        try:
            text2, report2 = vision_ocr.extract_text_vision(pdf_path, "fake-key", cache_path)
        finally:
            restore2()
        check("aucun appel API pour des pages déjà en cache", len(fake2.messages.calls) == 0)
        check("même texte reconstruit depuis le cache", text2 == text)
        check("mêmes totaux reconstruits depuis le cache",
              report2.total_input_tokens == report.total_input_tokens)

        print("\n4. Arrêt en cours de route (Stop) : cache partiel conservé")
        pdf_path3 = workdir / "sample3.pdf"
        make_pdf(pdf_path3, ["Page A.", "Page B.", "Page C."])
        cache_path3 = workdir / ".translax" / "sample3.vision_cache.jsonl"
        fake3, restore3 = install_fake_anthropic([
            canned_response("PAGE_NUMBER: none\nHEADER: none\n---\nCorrected A."),
            canned_response("PAGE_NUMBER: none\nHEADER: none\n---\nCorrected B."),
            canned_response("PAGE_NUMBER: none\nHEADER: none\n---\nCorrected C."),
        ])
        stop_after = {"count": 0}

        def stop_after_one():
            stop_after["count"] += 1
            return stop_after["count"] > 1  # laisse passer la 1ère page, arrête avant la 2e

        try:
            raised = False
            try:
                vision_ocr.extract_text_vision(
                    pdf_path3, "fake-key", cache_path3, should_stop=stop_after_one
                )
            except Cancelled:
                raised = True
        finally:
            restore3()
        check("Cancelled levée à l'arrêt demandé", raised)
        check("une seule page traitée avant l'arrêt", len(fake3.messages.calls) == 1)
        check("cette page reste dans le cache malgré l'arrêt",
              cache_path3.exists() and len(cache_path3.read_text(encoding="utf-8").splitlines()) == 1)

        print("\n5. Erreurs de l'API traduites en VisionOcrError lisible")
        error_cases = [
            (anthropic.AuthenticationError, "clé invalide", "clé api"),
            (anthropic.RateLimitError, "trop de requêtes", "débit"),
            (anthropic.APIConnectionError, "pas de réseau", "connexion internet"),
        ]
        for exc_cls, raw_message, expected_snippet in error_cases:
            pdf_err = workdir / f"err_{exc_cls.__name__}.pdf"
            make_pdf(pdf_err, ["Une seule page."])
            cache_err = workdir / ".translax" / f"err_{exc_cls.__name__}.jsonl"
            fake_err, restore_err = install_fake_anthropic([sdk_error(exc_cls, raw_message)])
            try:
                got_it = False
                message = ""
                try:
                    vision_ocr.extract_text_vision(pdf_err, "fake-key", cache_err)
                except vision_ocr.VisionOcrError as exc:
                    got_it = True
                    message = str(exc).lower()
            finally:
                restore_err()
            check(f"{exc_cls.__name__} -> VisionOcrError", got_it)
            check(f"{exc_cls.__name__} : message explicite ({expected_snippet!r})",
                  expected_snippet in message, f"({message!r})")

        print("\n6. Pas de clé API : refus immédiat, aucun appel réseau")
        no_key_pdf = workdir / "no_key.pdf"
        make_pdf(no_key_pdf, ["Contenu."])
        fake_nk, restore_nk = install_fake_anthropic([RuntimeError("ne doit jamais être appelé")])
        try:
            got_it = False
            try:
                vision_ocr.extract_text_vision(no_key_pdf, "", workdir / ".translax" / "no_key.jsonl")
            except vision_ocr.VisionOcrError:
                got_it = True
        finally:
            restore_nk()
        check("VisionOcrError sans clé API", got_it)
        check("aucun appel tenté sans clé", len(fake_nk.messages.calls) == 0)

        print("\n7. Filtre de contenu sur UNE page : le livre entier continue quand même")
        # Cas réel rencontré (voir SPEC.md) : une page précise d'un vrai
        # livre bloquée par le filtre de contenu d'Anthropic, au milieu
        # d'un job par ailleurs normal -- ne doit jamais faire échouer tout
        # le reste du livre.
        cf_pdf = workdir / "content_filter.pdf"
        make_pdf(cf_pdf, ["Page zero.", "Page one (filtrée).", "Page two."])
        cf_cache = workdir / ".translax" / "content_filter.jsonl"
        fake_cf, restore_cf = install_fake_anthropic([
            canned_response("PAGE_NUMBER: none\nHEADER: none\n---\nCorrected zero."),
            content_filter_error(),
            canned_response("PAGE_NUMBER: none\nHEADER: none\n---\nCorrected two."),
        ])
        try:
            text_cf, report_cf = vision_ocr.extract_text_vision(cf_pdf, "fake-key", cf_cache)
        finally:
            restore_cf()

        check("le job entier n'a PAS échoué malgré la page bloquée", len(report_cf.pages) == 3)
        check("1 seule page en échec de vision", report_cf.failed_count == 1, f"({report_cf.failed_count})")
        check("la page bloquée est signalée (comme une incertitude)", report_cf.pages[1].flagged)
        check("la page bloquée est bien marquée vision_failed", report_cf.pages[1].vision_failed)
        check("la page bloquée garde son texte ORIGINAL (rien perdu)",
              "Page one (filtrée)." in report_cf.pages[1].corrected_text)
        check("les pages avant/après la page bloquée sont normalement corrigées",
              report_cf.pages[0].corrected_text == "Corrected zero."
              and report_cf.pages[2].corrected_text == "Corrected two."
              and not report_cf.pages[0].vision_failed and not report_cf.pages[2].vision_failed)
        check("le texte de la page bloquée est entouré du marqueur début/fin, numéro de page inclus",
              report_cf.pages[1].corrected_text.count("⛔ TRANSLAX") == 2
              and "page 2" in report_cf.pages[1].corrected_text)
        check("le texte final contient les 3 pages dans l'ordre, page bloquée marquée",
              text_cf.startswith("Corrected zero.\f⛔ TRANSLAX")
              and text_cf.endswith("\fCorrected two.")
              and "Page one (filtrée)." in text_cf)

        print("  7b. Reprise : la page bloquée n'est pas retentée (déjà en cache)")
        fake_cf2, restore_cf2 = install_fake_anthropic([RuntimeError("ne doit jamais être appelé")])
        try:
            text_cf2, report_cf2 = vision_ocr.extract_text_vision(cf_pdf, "fake-key", cf_cache)
        finally:
            restore_cf2()
        check("aucun nouvel appel API (page bloquée comprise, déjà en cache)",
              len(fake_cf2.messages.calls) == 0)
        check("même résultat reconstruit depuis le cache", text_cf2 == text_cf)

        print("\n8. OCR local (PaddleOCR) -- mécanique rapide, sans le module réel")
        check("langue connue -> code PaddleOCR correct",
              languages.paddleocr_lang("eng_Latn") == "en" and languages.paddleocr_lang("zho_Hans") == "ch"
              and languages.paddleocr_lang("jpn_Jpan") == "japan" and languages.paddleocr_lang("kor_Hang") == "korean")
        check("langue hors table (lingala) -> None, jamais deviné", languages.paddleocr_lang("lin_Latn") is None)

        check("lignes recollées avec un espace, cas normal",
              vision_ocr._join_ocr_lines(["Hello world.", "Second line."]) == "Hello world. Second line.")
        check("mot coupé par un trait d'union en fin de ligne -> recollé sans espace",
              vision_ocr._join_ocr_lines(["This is Appro-", "priate indeed."]) == "This is Appropriate indeed.")
        check("un tiret qui n'est PAS en fin de mot (précédé d'un non-lettre) reste tel quel",
              vision_ocr._join_ocr_lines(["A dash -", "not a word break."]) == "A dash - not a word break.")
        check("lignes vides ignorées", vision_ocr._join_ocr_lines(["", "  ", "Real text."]) == "Real text.")

        langue_manquante_cache = workdir / "no_lang.jsonl"
        raised_no_lang = False
        try:
            vision_ocr.extract_text_paddleocr(cf_pdf, langue_manquante_cache, src_lang="lin_Latn")
        except vision_ocr.VisionOcrError:
            raised_no_lang = True
        check("langue sans correspondance PaddleOCR -> VisionOcrError, pas une trace brute", raised_no_lang)

        print("\n9. OCR local (PaddleOCR) -- UN vrai appel, sur un vrai document déjà utilisé plus haut")
        # Gratuit et local (contrairement à Anthropic) : un vrai appel ici
        # ne coûte ni argent ni réseau, juste du temps CPU (mesuré : 40 à
        # 50 s la première fois par page sur cette machine, chargement du
        # modèle inclus) -- volontairement limité à UNE seule page, pas le
        # document entier, pour rester raisonnable à exécuter à chaque
        # lancement de cette suite.
        try:
            import paddleocr  # noqa: F401
            paddleocr_available = True
        except ImportError:
            paddleocr_available = False

        if not paddleocr_available:
            print("  SAUTÉ -- paddleocr n'est pas installé dans cet environnement.")
        else:
            one_page_pdf = pymupdf.open()
            one_page_pdf.insert_pdf(pymupdf.open(str(cf_pdf)), from_page=0, to_page=0)
            one_page_path = workdir / "paddleocr_real_page.pdf"
            one_page_pdf.save(str(one_page_path))
            one_page_pdf.close()

            paddleocr_cache = workdir / "paddleocr_real.jsonl"
            text_real, report_real = vision_ocr.extract_text_paddleocr(
                one_page_path, paddleocr_cache, src_lang="eng_Latn",
            )
            check("une page traitée", report_real.total_pages == 1)
            check("coût nul (gratuit, pas d'appel API)", report_real.estimated_cost_usd() == 0.0)
            # La page réelle contient "Page zero." (voir make_pdf ci-dessus) --
            # vérifie que le VRAI texte a été lu, pas juste "une page non
            # vide" (une image blanche ne prouverait rien).
            check("le texte réellement détecté correspond au vrai contenu de la page",
                  "zero" in text_real.lower(), f"({text_real[:80]!r})")

            print("  Reprise : la page déjà en cache n'est pas retraitée")
            calls_before = {"count": 0}
            original_get_engine = vision_ocr._get_paddleocr_engine

            def fail_if_called(lang):
                calls_before["count"] += 1
                raise AssertionError("ne doit jamais être appelé -- la page est déjà en cache")

            vision_ocr._get_paddleocr_engine = fail_if_called
            try:
                text_real2, report_real2 = vision_ocr.extract_text_paddleocr(
                    one_page_path, paddleocr_cache, src_lang="eng_Latn",
                )
            finally:
                vision_ocr._get_paddleocr_engine = original_get_engine
            check("aucun nouvel appel au moteur (page déjà en cache)", calls_before["count"] == 0)
            check("même résultat reconstruit depuis le cache", text_real2 == text_real)

    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    if failures:
        print(f"{len(failures)} test(s) en échec : " + ", ".join(failures))
        return 1
    print("Tous les tests de vision OCR passent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
