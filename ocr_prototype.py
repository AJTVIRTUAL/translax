"""
Prototype PaddleOCR -- test rapide sur une image locale (une capture de
l'appli), pour valider que PaddleOCR tourne correctement en local sur CPU
avant toute integration dans TRANSLAX.

Usage:
    python ocr_prototype.py <image.png>
"""
import sys
import time
from pathlib import Path

from paddleocr import PaddleOCR


def main():
    if len(sys.argv) != 2:
        print("Usage: python ocr_prototype.py <image.png>")
        sys.exit(1)

    img_path = Path(sys.argv[1])
    if not img_path.exists():
        print(f"Introuvable : {img_path}")
        sys.exit(1)

    print("Chargement de PaddleOCR (telechargement des poids au premier lancement)...")
    t0 = time.time()
    ocr = PaddleOCR(use_textline_orientation=True, lang="fr", enable_mkldnn=False)
    print(f"Charge en {time.time() - t0:.1f}s")

    print(f"\nOCR sur {img_path.name} ...")
    t0 = time.time()
    result = ocr.predict(str(img_path))
    elapsed = time.time() - t0
    print(f"Termine en {elapsed:.2f}s\n")

    for page in result:
        texts = page.get("rec_texts", [])
        scores = page.get("rec_scores", [])
        print(f"{len(texts)} lignes de texte detectees :\n")
        for text, score in zip(texts, scores):
            print(f"  [{score:.2f}] {text}")


if __name__ == "__main__":
    main()
