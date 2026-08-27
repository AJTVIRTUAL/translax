"""
Tests de `core/segment.py` -- en particulier le repérage des blocs
« restricted » (voir `RESTRICTED_MARKER_PREFIX`), qui ne doivent JAMAIS
être traduits (une page bloquée par le filtre de contenu de l'API vision,
voir `core/vision_ocr.py` et SPEC.md).

    python tests/test_segment.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import segment  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK   {label}")
    else:
        print(f"  ECHEC {label} {detail}")
        failures.append(label)


def main() -> int:
    print("\n1. Stratégie « blocs » : un bloc restreint reste un seul segment")
    marker = segment.RESTRICTED_MARKER_PREFIX
    raw = (
        "Un vrai paragraphe avant, assez long pour ne surtout pas ressembler à un titre "
        "structurel isolé sur sa propre ligne, ce qui le ferait mal classer.\n\n"
        f"{marker} — DÉBUT PAGE NON VÉRIFIÉE (page 12) : texte original conservé.\n"
        "Ceci est le texte original de la page bloquée, jamais traduit.\n"
        f"{marker} — FIN PAGE NON VÉRIFIÉE (page 12)\n\n"
        "Un vrai paragraphe après, lui aussi assez long pour ne pas ressembler à un titre "
        "structurel isolé sur sa propre ligne, exactement comme celui d'avant."
    )
    segments = segment.segment_text(raw, strategy="blocks")
    types = [s["type"] for s in segments]
    check("3 segments : avant / restreint / après", types == ["paragraph", "restricted", "paragraph"],
          f"({types})")
    restricted = segments[1]
    check("le marqueur de début est conservé", "DÉBUT PAGE NON VÉRIFIÉE (page 12)" in restricted["text"])
    check("le marqueur de fin est conservé", "FIN PAGE NON VÉRIFIÉE (page 12)" in restricted["text"])
    check("le contenu original est conservé", "jamais traduit" in restricted["text"])
    check("les paragraphes voisins sont des segments normaux, pas restreints",
          segments[0]["type"] == "paragraph" and segments[2]["type"] == "paragraph")

    print("\n2. Un bloc restreint très court n'est pas pris pour un titre")
    short_raw = f"{marker} — DÉBUT (page 1)\nX\n{marker} — FIN (page 1)"
    short_segments = segment.segment_text(short_raw, strategy="blocks")
    check("un seul segment, de type restricted (pas heading malgré sa brièveté)",
          len(short_segments) == 1 and short_segments[0]["type"] == "restricted",
          f"({[s['type'] for s in short_segments]})")

    print("\n3. Stratégie « flux » : repérage best-effort, jamais perdu")
    flow_raw = (
        "Une phrase normale avant. " + f"{marker} DÉBUT page 5. " +
        "Contenu original de la page bloquée, une phrase complète ici. " +
        f"{marker} FIN page 5. Une phrase normale après."
    )
    flow_segments = segment.segment_text(flow_raw, strategy="flow", target_words=90)
    restricted_flow = [s for s in flow_segments if s["type"] == "restricted"]
    check("au moins un segment marqué restricted en stratégie flux",
          len(restricted_flow) >= 1, f"({[s['type'] for s in flow_segments]})")
    check("le marqueur de début et de fin sont bien dans un segment restricted",
          any("DÉBUT page 5" in s["text"] and "FIN page 5" in s["text"] for s in restricted_flow))

    print("\n4. Aucun marqueur : comportement inchangé")
    normal_raw = "Premier paragraphe assez long pour ne pas être un titre.\n\nDeuxième paragraphe, pareil."
    normal_segments = segment.segment_text(normal_raw, strategy="blocks")
    check("aucun segment restricted sans marqueur dans le texte",
          all(s["type"] != "restricted" for s in normal_segments))

    print()
    if failures:
        print(f"{len(failures)} test(s) en échec : " + ", ".join(failures))
        return 1
    print("Tous les tests de segmentation passent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
