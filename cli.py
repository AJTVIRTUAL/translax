"""
TRANSLAX en ligne de commande — sert à valider le moteur sans interface.

C'est volontairement la même logique que celle qu'appellera la fenêtre
graphique : si un problème apparaît, on sait tout de suite s'il vient du
moteur ou de l'interface.

    python cli.py ../../DRAFTS/Books/IlluVol1.txt
    python cli.py livre.pdf -o C:/Traductions --limit 5
    python cli.py livre.pdf --restart          (ignore une reprise possible)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from core import pipeline, translate
from core.languages import DEFAULT_SOURCE, DEFAULT_TARGET


def format_duration(seconds: float) -> str:
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h{minutes:02d}"
    if minutes:
        return f"{minutes} min {secs:02d} s"
    return f"{secs} s"


def main() -> int:
    parser = argparse.ArgumentParser(description="TRANSLAX — traduction locale NLLB-200")
    parser.add_argument("input", help="fichier PDF, TXT ou MD à traduire")
    parser.add_argument("-o", "--output-dir", default=None, help="dossier de sortie (défaut : celui du fichier source)")
    parser.add_argument("--src", default=DEFAULT_SOURCE, help=f"langue source FLORES (défaut {DEFAULT_SOURCE})")
    parser.add_argument("--tgt", default=DEFAULT_TARGET, help=f"langue cible FLORES (défaut {DEFAULT_TARGET})")
    parser.add_argument("--model", default=translate.DEFAULT_MODEL_KEY, choices=list(translate.MODEL_MAP))
    parser.add_argument("--strategy", default="auto", choices=["auto", "blocks", "flow"])
    parser.add_argument("--limit", type=int, default=None, help="ne traiter que les N premiers segments (test)")
    parser.add_argument("--no-cleanup", action="store_true", help="ne pas passer le nettoyage final")
    parser.add_argument("--restart", action="store_true", help="repartir de zéro même si une reprise est possible")
    parser.add_argument("--threads", type=int, default=None)
    parser.add_argument(
        "--keep-page-headers",
        action="store_true",
        help="ne pas retirer les en-têtes/pieds de page répétés ni les numéros de page détectés (PDF)",
    )
    parser.add_argument(
        "--no-title-translation",
        action="store_true",
        help="ne pas traduire le nom du fichier de sortie (garde le nom du fichier source)",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = pipeline.default_output_path(input_path, args.output_dir)

    job = pipeline.Job(
        input_path=input_path,
        output_path=output_path,
        src_lang=args.src,
        tgt_lang=args.tgt,
        model_key=args.model,
        strategy=args.strategy,
        cleanup=not args.no_cleanup,
        translate_title=not args.no_title_translation,
        resume="restart" if args.restart else "auto",
        limit=args.limit,
        threads=args.threads,
    )

    print(f"Source : {input_path}")
    print(f"Sortie : {output_path}" + ("  (renommé selon le titre traduit une fois terminé)" if job.translate_title else ""))

    def on_progress(p: translate.Progress) -> None:
        print(
            f"[{p.done}/{p.total}] {p.percent:5.1f}%  {p.rate:5.1f} s/segment  "
            f"reste ~{format_duration(p.eta)}",
            flush=True,
        )

    def on_page_cleanup(report) -> str:
        # Pas d'utilisateur pour répondre à une question en CLI : on
        # affiche ce qui a été détecté et on applique automatiquement
        # (sauf --keep-page-headers, qui garde le texte original).
        print(f"\n{report.lines_removed} ligne(s) détectée(s) sur {report.total_pages} pages :")
        for line in report.summary_lines():
            print(f"  - {line}")
        decision = "original" if args.keep_page_headers else "clean"
        print(f"-> {decision}\n", flush=True)
        return decision

    try:
        result = pipeline.run_job(
            job,
            on_status=lambda m: print(m, flush=True),
            on_progress=on_progress,
            on_page_cleanup=on_page_cleanup,
        )
    except KeyboardInterrupt:
        print("\nInterrompu. Relancer la même commande reprendra où ça s'est arrêté.")
        return 130

    if result.cancelled:
        print("\n".join(result.notes))
        return 1

    print(f"\nTerminé : {result.translated_segments}/{result.total_segments} segments -> {result.output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
