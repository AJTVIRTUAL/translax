"""
Tests de `core/md_cleanup.py` -- nettoyage avancé d'un .md importé
(demande explicite de l'utilisateur, 19/11/2026).

    python tests/test_md_cleanup.py
"""
from __future__ import annotations

import re
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import md_cleanup  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK   {label}")
    else:
        print(f"  ECHEC {label} {detail}")
        failures.append(label)


BOOK = "LE COTÉ CACHÉ DES CHOSES"
CHAPTER = "PAR NOS HABITUDES"

FILLER = (
    "Le chercheur attentif remarque bien des choses que les autres ignorent, et il en tire "
    "des leçons utiles pour sa vie de tous les jours. "
)


def fake_book() -> str:
    """Un faux livre qui reproduit les pièges d'un vrai : en-têtes pairs
    (numéro + titre du livre) et impairs (titre du chapitre, numéro avant OU
    après), en pleine phrase ; mot coupé collé au numéro ; numéro de cahier ;
    table des matières ; coquille d'OCR ; début de chapitre et sections
    collés au texte ; texte manquant dans la source."""
    blocks = [
        "## Le côté caché des Choses",
        f"TABLE DES MATIÈRES CHAPITRE XIV. — {CHAPTER} 7 Nourriture. — Le tabac. "
        "CHAPITRE XV. — PAR NOTRE ENTOURAGE 35 Les habitations.",
        f"{BOOK} CHAPITRE XIV {CHAPTER} NOURRITURE Une sentence attribuée au Christ est souvent citée. "
        + FILLER * 3,
        # L'exemple exact de l'utilisateur
        f"Tout ceci sans préjudice de la détérioration apportée 10 {BOOK} au corps mental et astral "
        "par l’habitude de ces liqueurs qui enivrent. Celui qui cherche à justifier ses ignobles appétits "
        "affirme souvent que les aliments et la boisson appartenant comme ils le font, purement au monde "
        "physique, ne peuvent avoir que de faibles effets sur le développement intérieur de l'homme.",
        FILLER + f"Il en est surtout ainsi pour 8 {BOOK} la pâtisserie et le pain. " + FILLER,
        FILLER + f"Le premier pas est que l'homme apprenne complète11 {CHAPTER} ment à contrôler ses corps. "
        "Il doit le faire complètement, et sans attendre. " + FILLER,
        FILLER + f"Il se nourrit d'ali28 {BOOK} ments très épicés, comme tant d'autres aliments. " + FILLER,
        FILLER + f"Pendant 2 18 {BOOK} une longue période, l'homme demeure comme paralysé. " + FILLER,
        FILLER + f"Il apprend 12 {BOOK} la patience, jour après jour. " + FILLER,
        FILLER + f"car ses aînés s'y sont {CHAPTER} 17 habitués depuis longtemps. " + FILLER,
        FILLER + f"Il a fait son devoir. {CHAPTER} 19 LA PROPRETÉ Les médecins sont d'accord sur ce point. " + FILLER,
        FILLER + f"Une parole 13 {CHAPTER} prononcée au bon moment suffit. " + FILLER,
        # Texte manquant dans la source (vu sur un vrai livre) : « parfaite »
        # et « ce » sont deux vrais mots du livre -- jamais soudés.
        FILLER + "Sa santé reste parfaite malgré les années. " + FILLER,
        FILLER + f"Il voulait faire son devoir aussi parfaite14 {BOOK} ce point de vue aussi. " + FILLER,
        FILLER + f"Une coquille d'OCR traîne 16 LF COTÉ CACHÉ DES CHOSES ici aussi. " + FILLER,
        FILLER + "Fin de la première partie. MÉTHODE ET SINCÉRITÉ L'étudiant occultiste agit avec soin. " + FILLER,
        "C. W. LEADBEATER Le côté caché des Choses, édition de 1928.",
    ]
    return "\n\n".join(blocks) + "\n"


def lowercase_words(text: str) -> Counter:
    return Counter(re.findall(r"(?<![\w'’])[a-zà-öø-ÿœæ][\w'’-]*", text))


def main() -> int:
    source = fake_book()
    cleaned, report = md_cleanup.clean_markdown(source, extract_headings=True, aerate=False)

    print("\n1. En-têtes appris dans le document lui-même")
    found = {h.text for h in report.headers}
    check("titre du livre (pages paires) reconnu", BOOK in found, f"({found})")
    check("titre du chapitre (pages impaires, numéro avant OU après) reconnu", CHAPTER in found, f"({found})")
    check("aucun faux en-tête", found <= {BOOK, CHAPTER}, f"({found})")
    variants = {v for h in report.headers for v in h.variants}
    check("coquille d'OCR rattachée à son en-tête (« LF COTÉ… »)", "LF COTÉ CACHÉ DES CHOSES" in variants)

    print("\n2. Retrait en pleine phrase -- l'exemple de l'utilisateur")
    expected = (
        "Tout ceci sans préjudice de la détérioration apportée au corps mental et astral par l’habitude "
        "de ces liqueurs qui enivrent. Celui qui cherche à justifier ses ignobles appétits affirme souvent "
        "que les aliments et la boisson appartenant comme ils le font, purement au monde physique, ne "
        "peuvent avoir que de faibles effets sur le développement intérieur de l'homme."
    )
    check("phrase rendue exactement comme demandé", expected in cleaned)
    check("plus aucune trace de l'en-tête du livre (ni de sa coquille)",
          "COTÉ CACHÉ DES CHOSES" not in cleaned, f"({cleaned.count('COTÉ CACHÉ')})")
    check("« pour 8 LE COTÉ… la pâtisserie » -> « pour la pâtisserie »", "pour la pâtisserie" in cleaned)
    check("numéro APRÈS l'en-tête aussi (« PAR NOS HABITUDES 17 »)", "sont habitués" in cleaned)

    print("\n3. Réparations au passage")
    check("mot coupé recollé (« complète11 … ment » -> « complètement »)", "apprenne complètement à" in cleaned)
    check("mot coupé recollé (« d'ali28 … ments » -> « d'aliments »)", "d'aliments très" in cleaned)
    check("numéro de cahier retiré (« Pendant 2 18 … » -> « Pendant une »)", "Pendant une longue" in cleaned)
    check("… et compté", report.signature_marks_removed == 1, f"({report.signature_marks_removed})")
    check("texte manquant dans la source : deux vrais mots jamais soudés (« parfaite ce »)",
          "parfaite ce point" in cleaned and "parfaitece" not in cleaned)
    check("table des matières intacte (« — PAR NOS HABITUDES 7 »)",
          f"CHAPITRE XIV. — {CHAPTER} 7 Nourriture" in cleaned)

    print("\n4. Titres remis à leur place")
    lines = cleaned.split("\n")
    check("début de chapitre dégagé", f"# CHAPITRE XIV — {CHAPTER}" in lines, f"({[l for l in lines if l.startswith('#')]})")
    check("titre de section qui suivait le chapitre", "## NOURRITURE" in lines)
    check("section collée après une fin de phrase", "## LA PROPRETÉ" in lines and "## MÉTHODE ET SINCÉRITÉ" in lines)
    check("le paragraphe reprend juste après le titre",
          any(l.startswith("Une sentence attribuée") for l in lines))
    check("une initiale (« C. W. LEADBEATER ») n'est jamais prise pour un titre",
          "## LEADBEATER" not in lines and "C. W. LEADBEATER Le côté" in cleaned)
    check("titre de livre déjà en Markdown conservé", "## Le côté caché des Choses" in lines)

    print("\n5. Aucun mot d'auteur perdu")
    before, after = lowercase_words(source), lowercase_words(cleaned)
    lost = before - after
    # Seuls peuvent manquer les morceaux de mots recollés : celui collé au
    # numéro de page (« complète11 ») et sa fin, reprise après l'en-tête (« ment »).
    unexpected = {w: n for w, n in lost.items() if not any(c.isdigit() for c in w) and w not in {"ment", "ments"}}
    check("seuls des morceaux de mots recollés manquent", not unexpected, f"({unexpected})")
    again, _ = md_cleanup.clean_markdown(cleaned, extract_headings=True, aerate=False)
    check("nettoyer deux fois ne change plus le texte (hors titres déjà dégagés)",
          lowercase_words(again) == lowercase_words(cleaned))

    print("\n6. Document sans en-têtes : rien n'est inventé")
    plain = "## Titre\n\nUne phrase normale. " + FILLER + "\n\nUn second paragraphe, en 2024, page 12.\n"
    plain_out, plain_report = md_cleanup.clean_markdown(plain, extract_headings=True, aerate=False)
    check("aucun en-tête détecté", not plain_report.headers)
    check("texte intact", lowercase_words(plain_out) == lowercase_words(plain))

    print("\n7. Fichier : un NOUVEAU fichier à côté, l'original jamais modifié")
    workdir = Path(tempfile.mkdtemp(prefix="translax_md_cleanup_"))
    try:
        original = workdir / "livre.md"
        original.write_text(source, encoding="utf-8")
        destination, pdf_path, file_report = md_cleanup.clean_file(original, make_pdf=True)
        check("original inchangé", original.read_text(encoding="utf-8") == source)
        check("résultat écrit sous « livre (nettoyé).md »", destination.name == "livre (nettoyé).md" and destination.exists())
        check("PDF généré à la demande", pdf_path is not None and pdf_path.exists())
        check("aération des paragraphes faite par défaut", file_report.paragraph_report is not None)
        second, _, _ = md_cleanup.clean_file(original)
        check("un second nettoyage n'écrase jamais le premier", second.name == "livre (nettoyé 2).md")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    if failures:
        print(f"{len(failures)} test(s) en échec : " + ", ".join(failures))
        return 1
    print("Tous les tests du nettoyage avancé passent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
