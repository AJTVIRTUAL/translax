"""
Tests de `core/extract.py` -- en particulier le support EPUB (ajouté le
25/08/2026), vérifié avec un vrai fichier EPUB minimal mais valide (une
vraie archive ZIP avec le manifeste OPF/spine attendu), pas un simulacre.

    python tests/test_extract.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import extract  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK   {label}")
    else:
        print(f"  ECHEC {label} {detail}")
        failures.append(label)


CONTAINER_XML = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""

CONTENT_OPF = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Test EPUB Book</dc:title>
    <dc:language>en</dc:language>
    <dc:identifier id="BookId">test-epub-001</dc:identifier>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="chap1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
    <item id="chap2" href="chapter2.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="chap1"/>
    <itemref idref="chap2"/>
  </spine>
</package>"""

TOC_NCX = """<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="test-epub-001"/></head>
  <docTitle><text>Test EPUB Book</text></docTitle>
  <navMap>
    <navPoint id="np1" playOrder="1"><navLabel><text>Chapter 1</text></navLabel><content src="chapter1.xhtml"/></navPoint>
    <navPoint id="np2" playOrder="2"><navLabel><text>Chapter 2</text></navLabel><content src="chapter2.xhtml"/></navPoint>
  </navMap>
</ncx>"""


def _chapter_xhtml(title: str, paragraphs: list[str]) -> str:
    body = "\n".join(f"<p>{p}</p>" for p in paragraphs)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>{t}</title></head>'
        "<body><h1>{t}</h1>{b}</body></html>"
    ).format(t=title, b=body)


def make_epub(path: Path, chapters: list[tuple[str, list[str]]]) -> None:
    """Un vrai EPUB minimal (archive ZIP + manifeste OPF/spine réels), pas
    un fichier renommé -- pour vérifier que PyMuPDF l'ouvre pour de vrai."""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", CONTAINER_XML)
        z.writestr("OEBPS/content.opf", CONTENT_OPF)
        z.writestr("OEBPS/toc.ncx", TOC_NCX)
        for i, (title, paragraphs) in enumerate(chapters, start=1):
            z.writestr(f"OEBPS/chapter{i}.xhtml", _chapter_xhtml(title, paragraphs))


def main() -> int:
    print("\n1. .epub reconnu comme format pris en charge")
    check(".epub accepté", extract.is_supported(Path("livre.epub")))
    check(".EPUB (majuscules) accepté aussi", extract.is_supported(Path("livre.EPUB")))
    check(".mobi toujours refusé (pas dans le périmètre)", not extract.is_supported(Path("livre.mobi")))

    workdir = Path(tempfile.mkdtemp(prefix="translax_extract_"))
    try:
        epub_path = workdir / "book.epub"
        make_epub(epub_path, [
            ("Chapter One", [
                "This is the first real paragraph of chapter one, long enough to be a proper test.",
                "This is the second real paragraph of chapter one, also long enough to count.",
            ]),
            ("Chapter Two", [
                "This is the first real paragraph of chapter two, long enough to be a proper test.",
            ]),
        ])

        print("\n2. Extraction réelle (vraie archive ZIP, vrai manifeste OPF)")
        progress_calls: list[tuple[int, int]] = []
        text = extract.extract_text(epub_path, on_progress=lambda p, t: progress_calls.append((p, t)))
        check("le texte du chapitre 1 est présent", "first real paragraph of chapter one" in text)
        check("le texte du chapitre 2 est présent", "first real paragraph of chapter two" in text)
        check("les chapitres sont séparés par un saut de page (\\f), comme un PDF",
              "\f" in text, f"(repr partiel : {text[:80]!r})")
        check("progression rapportée pour les 2 chapitres", progress_calls == [(1, 2), (2, 2)],
              f"({progress_calls})")

        print("\n3. Bout en bout avec le pipeline (faux moteur, comme test_pipeline.py)")
        from core import pipeline, translate

        class FakeEngine:
            def __init__(self, *a, **k):
                self.calls: list[str] = []

            def load(self, on_status=None):
                pass

            def translate(self, text: str, *, heartbeat=None) -> str:
                self.calls.append(text)
                return "FR " + text

            def unload(self):
                pass

        original_engine = translate.PreciseEngine
        translate.PreciseEngine = FakeEngine  # type: ignore[assignment]
        try:
            out_path = workdir / "book.md"
            result = pipeline.run_job(
                pipeline.Job(input_path=epub_path, output_path=out_path, translate_title=False),
            )
            check("job non annulé", not result.cancelled)
            check("fichier .md produit à partir d'un .epub", out_path.exists())
            content = out_path.read_text(encoding="utf-8")
            check("contenu traduit présent (préfixe « FR »)",
                  "FR" in content and "first real paragraph of chapter one" in content)
        finally:
            translate.PreciseEngine = original_engine  # type: ignore[assignment]

        print("\n4. Format toujours refusé proprement (.docx par exemple)")
        raised = False
        try:
            extract.extract_text(workdir / "nope.docx")
        except extract.UnsupportedFormat:
            raised = True
        check("UnsupportedFormat levée pour un format hors périmètre", raised)

    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    if failures:
        print(f"{len(failures)} test(s) en échec : " + ", ".join(failures))
        return 1
    print("Tous les tests d'extraction passent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
