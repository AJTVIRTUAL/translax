# OCR local pour TRANSLAX — comparatif et statut

## 1. PaddleOCR — prototype testé et fonctionnel

Installé et testé en local sur cette machine (CPU, pas de GPU) le 25/08/2026.

```
pip install paddlepaddle paddleocr
```

**Résultat du test** (`ocr_prototype.py`, sur `captures/4_termine.png`) :
- 26/26 lignes de texte détectées correctement, y compris les accents
  français (vérifié : aucun caractère de remplacement dans les données
  réelles — les `�` visibles dans le terminal étaient un problème
  d'affichage de la console Windows, pas une erreur d'OCR, comme déjà vu
  avec les traductions de livres).
- Confiance de 0.95 à 1.00 sur toutes les lignes.
- ~43 s pour cette image sur CPU (premier appel ; les modèles restent
  chargés en mémoire pour les appels suivants, donc bien plus rapide au
  2ème document dans un vrai pipeline).

**Piège rencontré et corrigé** : `paddlepaddle==3.3.1` (le nouveau moteur
"PIR") plante à l'inférence sur CPU avec l'accélération oneDNN activée par
défaut (`NotImplementedError: ConvertPirAttribute2RuntimeAttribute...`).
Fix : passer `enable_mkldnn=False` au constructeur `PaddleOCR(...)`. Sans
ce réglage, l'OCR ne fonctionne pas du tout sur cette configuration.

**Fichier prototype** : [ocr_prototype.py](ocr_prototype.py) —
`python ocr_prototype.py <image.png>`.

**Licence** : Apache 2.0 — gratuit, open source, utilisation commerciale
sans restriction.

## 2. VLM (vision-langage) — non testés ici, pour les cas complexes

Plus lourds que PaddleOCR, mais comprennent le *contexte* visuel (mise en
page complexe, écriture manuscrite, tableaux) plutôt que juste détecter des
caractères. Nécessitent un GPU pour être utilisables en pratique — à tester
plutôt sur la machine de ton ami (RTX) que sur ce HP Pavilion.

| Modèle | Licence | Commercialisable ? | Remarque |
|---|---|---|---|
| **GOT-OCR2.0** | Apache 2.0 | ✅ Oui, sans restriction | Le plus simple des quatre côté licence. |
| **Qwen2.5-VL 7B / 72B** | Apache 2.0 | ✅ Oui, sans restriction | La variante **3B** est sous licence "Qwen RESEARCH" (recherche uniquement, **pas commercialisable**) — vérifier la taille exacte avant de choisir. |
| **InternVL** | Variable | ⚠️ À vérifier au cas par cas | La licence dépend du modèle de langage utilisé en base (InternLM2, Qwen, ou Llama 3 selon la variante) — chaque base a ses propres conditions. |
| **MiniCPM-V** | Licence OpenBMB | ⚠️ Généralement oui, avec condition | Usage commercial autorisé mais nécessite souvent un enregistrement/formulaire auprès d'OpenBMB au-delà d'un certain usage — à vérifier sur la page du modèle. |

**Important** : ces informations reflètent l'état des licences au moment de
la rédaction — les éditeurs changent parfois les termes d'une version à
l'autre. **Toujours vérifier le fichier `LICENSE` exact sur la page
Hugging Face du modèle précis (taille + version) avant tout usage
commercial** — ce tableau donne une orientation, pas un avis juridique.

## 3. Cloud à distance — Infomaniak

Oui, c'est possible : Infomaniak propose un **Public Cloud** (infrastructure
type OpenStack, accessible à distance en SSH/API comme n'importe quel VPS),
hébergé en Suisse — bon argument confidentialité/RGPD si c'est un critère
pour toi. Deux points à vérifier avant de te lancer, que je ne peux pas
garantir avec certitude à la date de rédaction :
- **Disponibilité d'instances GPU** dans leur offre Public Cloud actuelle —
  historiquement leur catalogue est plutôt orienté calcul CPU/stockage ;
  à confirmer sur leur page Public Cloud avant de compter dessus pour un
  VLM.
- **Tarifs actuels** — à vérifier directement, je n'ai pas de chiffres
  fiables et à jour à te donner ici.

Si leur offre GPU s'avère limitée ou absente, les alternatives classiques
pour louer du GPU à l'heure : RunPod, Vast.ai, Lambda Labs, ou les offres
GPU de OVHcloud (autre fournisseur européen, français celui-là).

## 4. Recommandation pour TRANSLAX

- **PaddleOCR en local, par défaut** — déjà validé, gratuit, tourne sur ce
  PC, licence commerciale sans restriction. Bon choix pour la majorité des
  documents scannés/PDF standards.
- **VLM local en option**, réservé aux documents difficiles, activé
  seulement si un GPU est détecté sur la machine — Qwen2.5-VL-7B ou
  GOT-OCR2.0 en priorité (licence Apache 2.0 claire, pas d'ambiguïté).
- Objectif : supprimer la dépendance à l'API Anthropic payante pour
  « Traduire X », pour que TRANSLAX soit réellement 100% local de bout en
  bout, cohérent avec le positionnement actuel du logiciel.
