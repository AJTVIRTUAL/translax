"""
Tests de `core/page_markers.py` -- liste noire des numéros de page
(« 17 of 103 »…), demande explicite de l'utilisateur (19/11/2026).

    python tests/test_page_markers.py

Chaque exemple déclaré dans `page_markers.BLACKLIST` est vérifié
automatiquement (§4) : ajouter un marqueur avec son exemple suffit pour qu'il
soit testé.
"""
from __future__ import annotations

import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import md_cleanup, page_cleanup, page_markers, pipeline, translate  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK   {label}")
    else:
        print(f"  ECHEC {label} {detail}")
        failures.append(label)


def flat(text: str) -> str:
    return " ".join(text.split())


FILLER = "The careful student notices many things that others ignore, and draws lessons from them."

# L'exemple exact de l'utilisateur, et ses voisins de pagination.
USER_BEFORE = (
    "Miss X. retained subconsciously the knowledge and powers that had been hers "
    "during the previous life when she was"
)
USER_AFTER = (
    "implicated in the witch-cult. She also retained her passion for Mr. C., a passion "
    "which was obviously unrequited."
)
USER_EXPECTED = f"{USER_BEFORE} {USER_AFTER}"


def user_document(separator: str = "\n") -> str:
    """Pages 15 à 18 : chaque pied de page « N of 103 » seul sur sa ligne."""
    return "\n\n".join([
        f"{FILLER}\n15 of 103\n{FILLER}",
        f"{FILLER}\n16 of 103\n{FILLER}",
        f"{USER_BEFORE}{separator}17 of 103{separator}{USER_AFTER}",
        f"{FILLER}\n18 of 103\n{FILLER}",
    ]) + "\n"


def main() -> int:
    print("\n1. L'exemple de l'utilisateur")
    text, report = page_markers.strip_markers(user_document())
    check("« 17 of 103 » qui coupait la phrase : retiré", "of 103" not in text, f"({text!r})")
    check("la phrase se retrouve d'un seul tenant", USER_EXPECTED in flat(text))
    check("rapport : 4 numéros « N of 103 »", report.removed == {"N of 103": 4}, f"({report.removed})")
    check("résumé lisible", report.summary() == "Numéros de page retirés (liste noire) : 4 — « N of 103 » ×4.",
          f"({report.summary()})")

    cleaned, md_report = md_cleanup.clean_markdown(user_document(separator="\n\n"))
    check("entouré de lignes vides : retiré ET phrase recollée par le nettoyage avancé",
          USER_EXPECTED in flat(cleaned) and "of 103" not in cleaned, f"({cleaned!r})")
    check("… et annoncé dans le rapport du nettoyage avancé",
          any("liste noire" in line for line in md_report.summary_lines()))

    raw = user_document().replace("\n17 of 103\n", "\n17 of 103\f")
    stripped, _ = page_markers.strip_markers(raw)
    check("dernière ligne d'une page (texte brut d'un PDF) : retirée, saut de page gardé",
          "of 103" not in stripped and f"{USER_BEFORE}\f{USER_AFTER}" in stripped, f"({stripped!r})")

    print("\n2. En pleine phrase (lignes déjà recollées)")
    pages = [f"{FILLER} Text before page {n} {n} of 250 goes on after it." for n in range(20, 26)]
    inline, inline_report = page_markers.strip_markers(" ".join(pages))
    check("6 numéros qui avancent comme des pages : retirés",
          "of 250" not in inline and "page 20 goes on" in inline, f"({inline_report.removed})")
    few = " ".join(f"{FILLER} Step {n} of 250 is done." for n in (20, 21, 22))
    check("seulement 3, en pleine phrase : gardés (pas assez de preuve)",
          page_markers.strip_markers(few)[0] == few)

    print("\n3. Garde-fous : jamais un vrai texte")
    stats = "In the trial, 3 of 10 patients improved, 7 of 10 relapsed and 5 of 10 slept better."
    check("vrais « 3 of 10 patients » en pleine phrase : intacts", page_markers.strip_markers(stats)[0] == stats)
    shuffled = " ".join(f"Group {n} of 40 agreed." for n in (7, 3, 9, 2, 5, 8))
    check("même nombreux, s'ils n'avancent pas comme des pages : intacts",
          page_markers.strip_markers(shuffled)[0] == shuffled)
    fractions = "Add 1/4 cup, then 3/4 cup and 2/4 of it. Born 12/05/1920, died 3/6/1980."
    check("fractions et dates (sans espaces autour de « / ») : intactes",
          page_markers.strip_markers(fractions)[0] == fractions)
    refs = "See page 12 for details, page 45 for the map and page 78 for the index."
    check("« voir page 12 » en pleine phrase : intact", page_markers.strip_markers(refs)[0] == refs)
    bigger = "\n".join(["Intro.", "150 of 103", "Middle.", "160 of 103", "End."])
    check("numéro plus grand que le total : ignoré", page_markers.strip_markers(bigger)[0] == bigger)
    single = "Intro.\n17 of 103\nEnd.\n"
    check("une seule occurrence, même seule sur sa ligne : gardée", page_markers.strip_markers(single)[0] == single)

    print("\n4. Chaque exemple de la liste noire est reconnu (ajout d'un marqueur = testé ici)")
    for marker in page_markers.BLACKLIST:
        for example in marker.examples:
            lines = []
            for n in (15, 16, 17):
                lines += [FILLER, example.replace("17", str(n))]
            document = "\n".join(lines + [FILLER]) + "\n"
            out, rep = page_markers.strip_markers(document)
            check(f"{marker.name} : « {example} » seul sur sa ligne",
                  rep.total == 3 and out.count(FILLER) == 4 and not re.search(r"\b1[567]\b", out),
                  f"({rep.removed}, {out!r})")

    print("\n5. Fenêtre de vérification des pages : « of » n'est plus pris pour un en-tête")
    pdf_like = "\f".join(f"{FILLER}\n{FILLER} page body {n}.\n{n} of 6" for n in range(1, 7))
    _, page_report = page_cleanup.clean_pdf_pages(pdf_like)
    check("pagination en bord de page déjà attrapée par le nettoyage des pages",
          any("Numéros de page « N of M »" in line for line in page_report.summary_lines()),
          f"({page_report.summary_lines()})")
    preview = md_cleanup.preview_lines(user_document())
    check("aperçu du nettoyage avancé : la liste noire y figure", any("liste noire" in line for line in preview),
          f"({preview})")

    print("\n6. Pipeline : extraction et traduction (faux moteur)")
    received: list[str] = []

    class RecordingEngine:
        def __init__(self, *args, **kwargs):
            pass

        def load(self, on_status=None):
            pass

        def translate(self, text: str, *, heartbeat=None) -> str:
            received.append(text)
            return "FR " + text

        def unload(self):
            pass

    names = ("PreciseEngine", "FastEngine", "OpusMtEngine", "MadladEngine")
    originals = {name: getattr(translate, name) for name in names}
    for name in names:
        setattr(translate, name, RecordingEngine)
    workdir = Path(tempfile.mkdtemp(prefix="translax_page_markers_"))
    try:
        book = workdir / "book.txt"
        book.write_text(user_document(separator="\n\n"), encoding="utf-8")
        statuses: list[str] = []
        extracted = pipeline.run_job(
            pipeline.Job(input_path=book, output_path=workdir / "extrait.md", extract_only=True,
                         strategy="blocks", split_paragraphs=False),
            on_status=statuses.append,
        )
        written = extracted.output_path.read_text(encoding="utf-8")
        check("extraction : plus aucun « N of 103 »", "of 103" not in written, f"({written!r})")
        check("extraction : la phrase coupée est recollée", USER_EXPECTED in flat(written))
        check("journal : retrait annoncé (« Texte source — Numéros de page retirés… »)",
              any(s.startswith("Texte source — Numéros de page retirés") for s in statuses), f"({statuses})")

        translated = pipeline.run_job(pipeline.Job(
            input_path=book, output_path=workdir / "traduit.md", strategy="blocks", translate_title=False,
        ))
        check("traduction : le moteur ne reçoit jamais « 17 of 103 »", not any("of 103" in t for t in received),
              f"({[t for t in received if 'of 103' in t]})")
        check("traduction : il reçoit la phrase entière", any(USER_EXPECTED in t for t in received))
        check("traduction terminée normalement", not translated.cancelled)

        received.clear()
        pipeline.run_job(pipeline.Job(
            input_path=book, output_path=workdir / "brut.md", strategy="blocks", translate_title=False,
            advanced_cleanup=False,
        ))
        check("nettoyage avancé décoché : laissé tel quel", any("of 103" in t for t in received))

        print("\n7. Fenêtre à cases (19/11/2026) : un groupe décoché est gardé pour de bon")
        paged = workdir / "paged.txt"
        paged.write_text("\f".join(
            f"RUNNING TITLE\n\n{FILLER} Body of page {n}.\n\n{n} of 6" for n in range(1, 7)
        ), encoding="utf-8")
        seen: dict = {}

        def keep_pagination(report):
            seen["labels"] = [g.label() for g in report.groups]
            keep = {i for i, g in enumerate(report.groups) if g.is_pagination}
            return page_cleanup.CleanupChoice(decision="clean", keep=frozenset(keep))

        kept = pipeline.run_job(
            pipeline.Job(input_path=paged, output_path=workdir / "paged.md", extract_only=True,
                         strategy="blocks", split_paragraphs=False),
            on_page_cleanup=keep_pagination,
        )
        kept_text = kept.output_path.read_text(encoding="utf-8")
        check("groupes proposés : pagination « N of M » et titre courant",
              "Numéros de page « N of M »" in seen.get("labels", []) and "« RUNNING TITLE »" in seen.get("labels", []),
              f"({seen})")
        check("« N of M » décoché : les 6 numéros restent (la liste noire ne passe pas outre)",
              len(re.findall(r"\b\d of 6\b", kept_text)) == 6, f"({kept_text!r})")
        check("le groupe resté coché est retiré", "RUNNING TITLE" not in kept_text)

        no_advanced = pipeline.run_job(
            pipeline.Job(input_path=paged, output_path=workdir / "paged_sans_avance.md", extract_only=True,
                         strategy="blocks", split_paragraphs=False),
            on_page_cleanup=lambda report: page_cleanup.CleanupChoice(decision="clean", advanced=False),
        )
        check("case « Nettoyage avancé » décochée : ni avant ni après pour ce document",
              no_advanced.source_cleanup_report is None and no_advanced.md_cleanup_report is None)
        check("ancienne réponse en simple texte (« clean ») toujours comprise",
              page_cleanup.CleanupChoice.of("clean") == page_cleanup.CleanupChoice(decision="clean")
              and page_cleanup.CleanupChoice.of(None).decision == "cancel"
              and page_cleanup.CleanupChoice.of({"decision": "clean", "keep": [2], "advanced": False})
              == page_cleanup.CleanupChoice(decision="clean", keep=frozenset({2}), advanced=False))
    finally:
        for name, engine in originals.items():
            setattr(translate, name, engine)
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    if failures:
        print(f"{len(failures)} test(s) en échec : " + ", ".join(failures))
        return 1
    print("Tous les tests de la liste noire des numéros de page passent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
