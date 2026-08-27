# TRANSLAX — fiche projet

Application de bureau **locale** qui traduit un document (PDF / TXT / MD) en
Markdown, avec le modèle NLLB-200 tournant sur la machine. Elle reprend
exactement le moteur déjà validé du pipeline `txtTraduction/pyTraduction`,
mais avec une vraie interface : import de fichier, barre de progression en %,
détails en direct, écriture automatique du `.md` de sortie.

- **Nom du logiciel** : TRANSLAX
- **Emplacement** : `C:\DEV\TRANSLAX\PROJECTS\TRANSLAX\`
- **Binaire final** : `PROJECTS\TRANSLAX\dist\TRANSLAX.exe`

> Le dossier du projet s'appelle `TRANSLAX` et non `translax.exe` : sous
> Windows, un dossier portant l'extension `.exe` déclenche des faux positifs
> antivirus, se confond au double-clic avec un exécutable, et entrerait en
> collision avec le vrai `TRANSLAX.exe` produit par PyInstaller.

---

## 0. État des lieux (vérifié le 21/08/2026)

**Déjà fait et validé**

| Élément | État |
|---|---|
| Moteur de traduction NLLB-200-600M | Validé — a produit 2 livres complets |
| `Les lois de l'univers et de la vie.md` | Terminé (626 segments) |
| `Bloodlines of the Illuminati Volume 1.md` | Terminé (726/726 segments, 100 %) |
| Modèle NLLB-200-distilled-600M | **Déjà en cache** (`~/.cache/huggingface`) — pas de téléchargement de 2,4 Go au premier lancement |
| Modules `core/` de TRANSLAX | Écrits et testés — 18 vérifications automatiques passent (`tests/test_pipeline.py`) |
| Segmentation | Revalidée : 726/726 segments **identiques** à la sortie de référence |
| Chaîne complète | Testée de bout en bout via `cli.py` : extraction → segmentation → traduction → nettoyage |
| Interface PySide6 | Écrite et testée — 31 vérifications automatiques passent (`tests/test_ui.py`), sélecteur de modèle inclus |

**Environnement machine**

- Python **3.13.2** (global : `C:\Users\amilc\AppData\Local\Programs\Python\Python313`)
- Déjà installé globalement : `torch 2.6.0+cpu`, `transformers 5.15.1`,
  `ctranslate2 4.8.1`, `sentencepiece 0.2.1`, `huggingface_hub 1.28.0`
- CPU uniquement : pas de GPU CUDA sur cette machine (le Radeon intégré ne
  fait pas de CUDA). Ne jamais forcer `device="cuda"`.
- Environnement du projet : `PROJECTS\TRANSLAX\.venv`, créé avec
  `--system-site-packages` pour réutiliser les 2 Go de torch déjà présents
  au lieu de les retélécharger.

**Le pipeline d'origine et sa correspondance dans TRANSLAX**

| Script d'origine (`txtTraduction/pyTraduction`) | Devient |
|---|---|
| `prepare_source.py` (PDF structuré → JSONL) | `core/segment.py`, stratégie « blocs » |
| `prepare_book_source.py` (txt continu → JSONL) | `core/segment.py`, stratégie « flux » |
| `translate_document.py` (JSONL → .md) | `core/translate.py` |
| `cleanup_headings.py` (faux titres, traits d'union) | `core/postprocess.py` |
| `traducteur_nllb.py` | **Obsolète** — ancienne classe avec `login()` HF, à ne pas reprendre |

---

## 1. Objectif

- App de bureau **locale**, pas un site web, pas de service en ligne.
- Traduction **EN → FR** par défaut. NLLB-200 gère ~200 langues, donc les
  sélecteurs de langue existent dès le départ, même si seul EN→FR est testé.
- Entrée : **PDF, TXT ou MD**.
- Sortie : **toujours un fichier .md**, créé automatiquement, avec le
  **même nom de base** que l'entrée (`IlluVol1.txt` → `IlluVol1.md`,
  `rapport.pdf` → `rapport.md`).
- Tout tourne en local ; l'appli est l'habillage visuel d'un moteur déjà
  éprouvé.

## 2. Cahier des charges

| # | Fonctionnalité | Détail |
|---|---|---|
| 1 | Import de fichier | Bouton « Parcourir » + glisser-déposer (PDF / TXT / MD) |
| 2 | Détection du type | PDF → extraction PyMuPDF ; TXT/MD → lecture directe |
| 3 | Sélecteur de langues | Source / cible, pré-remplis Anglais → Français |
| 4 | Bouton « Traduire » | Lance le pipeline dans un thread (ne fige pas l'UI) |
| 5 | Barre de progression | % + « segment X / Y » + s/segment + temps restant |
| 6 | Détails en direct | Zone de log défilante |
| 7 | Écriture incrémentale | Le `.md` se remplit au fur et à mesure |
| 8 | Sortie automatique | `<dossier choisi>/<nom_source>.md` |
| 9 | Reprise | Relancer le même fichier reprend là où ça s'est arrêté |
| 10 | Annulation | Bouton « Stop » qui interrompt proprement |
| 11 | Nettoyage final | Faux titres rétrogradés + traits d'union recollés |
| 12 | Ouvrir le résultat | « Ouvrir le fichier » / « Ouvrir le dossier » (sélectionne le fichier dans l'explorateur) |
| 13 | Sélecteur de modèle | 600M / 1.3B distillé / 3.3B, avec avertissement avant tout téléchargement |

## 3. Stack technique

Tout en Python de bout en bout : pas de pont entre deux langages, pas de
serveur local, le moteur NLLB s'appelle directement depuis le code de
l'interface.

```
Langage        : Python 3.13
Interface      : PySide6            (Qt officiel, licence LGPL)
Traduction     : transformers 5.15.1 + facebook/nllb-200-distilled-600M
Extraction PDF : PyMuPDF (fitz)     — pas de Poppler à installer
Threading      : QThread + Signals Qt
Packaging      : PyInstaller        -> TRANSLAX.exe
```

**Pourquoi PySide6** : bindings officiels de Qt, licence LGPL (utilisable
librement, contrairement à PyQt6 en GPL/payant), rendu réellement « pro »
(feuilles de style QSS façon CSS), et surtout un modèle `QThread` + `Signal`
fait exactement pour ce cas — travail lourd en fond, UI qui reste réactive.
Sans ça, la fenêtre se fige pendant les heures de traduction.

Alternatives écartées : **CustomTkinter** (plus simple mais moins flexible,
threading plus manuel), **Flet** (beau mais écosystème jeune), **Electron +
FastAPI** (bundle Chromium + pont HTTP = complexité inutile ici).

**Versions épinglées** dans `requirements.txt` : `transformers` est en
version majeure 5, une montée de branche casserait l'appli packagée.

## 3 bis. Lancer l'application

```
cd C:\DEV\TRANSLAX\PROJECTS\TRANSLAX
.venv\Scripts\python.exe main.py          # l'application
.venv\Scripts\python.exe cli.py <fichier> # le même moteur en ligne de commande
.venv\Scripts\python.exe tests/test_pipeline.py
.venv\Scripts\python.exe tests/test_ui.py
```

Les deux fichiers de test utilisent un faux traducteur : ils s'exécutent en
une seconde et ne chargent jamais NLLB.

## 3 ter. Sélecteur de modèle (ajouté le 22/08/2026)

Trois profils exposés dans l'UI (`core/translate.py::MODEL_INFO`), classés du
plus rapide au plus puissant — détail du classement et des sources dans la
discussion du 22/08 :

| Clé | Modèle HuggingFace | Taille | Usage |
|---|---|---|---|
| `600M` (défaut) | `facebook/nllb-200-distilled-600M` | 2,5 Go — **mesuré** | Rapide. Gros volumes (livres entiers). |
| `1.3B` | `facebook/nllb-200-distilled-1.3B` | ~5,3 Go — estimé | Équilibré. Meilleure qualité que 600M pour un temps encore raisonnable. |
| `3.3B` | `facebook/nllb-200-3.3B` | ~13,5 Go — estimé | Qualité maximale. Documents courts seulement : bien plus lent. |

Le MoE 54,5B n'est pas proposé : ~55 Go rien qu'en RAM même quantifié,
irréaliste pour une appli de bureau CPU.

**Détection du cache local** (`translate.is_model_cached`) : interroge
`huggingface_hub.scan_cache_dir()` pour savoir si un modèle est déjà
téléchargé, avec un seuil de 50 Mo pour ignorer les stubs de cache vides
(rencontré en pratique : le 3.3B avait un dossier de cache sans le moindre
poids réel, resté d'un téléchargement jamais abouti).

**Avertissement avant téléchargement** (`MainWindow._confirm_model_download`) :
si le modèle choisi n'est pas en cache, une boîte de dialogue affiche la
taille estimée et demande confirmation avant de lancer quoi que ce soit —
jamais de téléchargement de plusieurs Go déclenché silencieusement en
arrière-plan. Le libellé d'info sous le sélecteur (`_update_model_info`)
affiche en permanence taille + vitesse + présence en cache, avant même de
cliquer sur Traduire.

Les tailles des modèles `1.3B` et `3.3B` sont des **estimations non
vérifiées** (extrapolées depuis le seul point mesuré, le 600M) : aucune
fiche HuggingFace consultée ne publie la taille exacte en Go, seule la
licence (CC-BY-NC-4.0 pour les trois) a pu être confirmée.

## 4. Architecture

```
PROJECTS/TRANSLAX/
├── main.py                  # point d'entrée de l'application
├── cli.py                   # même moteur en ligne de commande (validation sans UI)
├── requirements.txt
├── SPEC.md                  # ce document
├── scripts/
│   └── stamp_build_date.py  # régénère la date de build avant chaque packaging (voir §5 ter)
├── tests/
│   ├── test_pipeline.py     # reprise, arrêt, écriture incrémentale, nettoyage,
│   │                        #   nettoyage de pages, renommage du titre traduit
│   ├── test_page_cleanup.py # en-têtes/pieds de page répétés, numéros de page
│   └── test_ui.py           # câblage Qt : thread, signaux, états des boutons,
│                            #   dialogue de nettoyage cross-thread
│                            #   (moteur factice : aucun modèle chargé)
├── core/
│   ├── languages.py         # codes FLORES-200 (eng_Latn, fra_Latn, …)
│   ├── extract.py           # PDF/TXT/MD -> texte brut (PyMuPDF)
│   ├── page_cleanup.py      # en-têtes/pieds de page répétés, numéros de page (voir §5 bis)
│   ├── segment.py           # texte brut -> segments (blocs ou flux, auto)
│   ├── translate.py         # segments -> .md, écriture incrémentale
│   ├── postprocess.py       # nettoyage des faux titres + traits d'union
│   ├── state.py             # état de reprise (.translax/*.progress.json)
│   ├── pipeline.py          # orchestration : le seul module appelé par l'UI
│   └── version.py           # numéro de version + date de build (voir §5 ter)
└── ui/
    ├── main_window.py       # fenêtre PySide6, sans cadre natif (voir §6 bis)
    ├── titlebar.py          # barre de titre personnalisée (icône, boutons dessinés)
    ├── worker.py            # QThread + Signals autour de pipeline.run_job
    ├── icon.ico             # logo de l'appli (barre des tâches, barre de titre, raccourci)
    └── styles.qss
```

L'interface n'appelle qu'une seule fonction : `pipeline.run_job(job,
on_status=…, on_progress=…, should_stop=…, on_page_cleanup=…)`. Tout le
reste est du détail interne au moteur.

## 5. Pipeline de traitement

1. L'utilisateur choisit un fichier (PDF / TXT / MD).
2. `extract.py` sort le texte brut :
   - PDF → PyMuPDF page par page (`sort=True`, ordre de lecture), sauts de
     page conservés en `\f` comme le faisait `pdftotext -layout` ;
   - TXT/MD → lecture directe, UTF-8 avec repli cp1252 puis latin-1.
3. `segment.py` choisit **automatiquement** sa stratégie :
   - **blocs** — le texte a de vraies lignes vides (≥ 20 lignes vides et
     ≥ 5 % des lignes) : on respecte les paragraphes et on reconnaît
     titres / puces / paragraphes ;
   - **flux** — pavé continu sans ligne vide (cas `IlluVol1.txt` : 0 ligne
     vide sur 6274) : les paragraphes sont reconstruits par regroupement de
     phrases entières (~90 mots).
2 bis. `page_cleanup.py` retire les en-têtes/pieds de page répétés et les
   numéros de page (voir §5 bis) — avant la segmentation, sur le texte brut
   encore découpé par page (`\f`).
4. Les segments sont mis en cache dans `.translax/<nom>.segments.jsonl`
   (permet la reprise sans ré-extraire, et le nettoyage final).
5. `translate.py` traduit **un segment à la fois**, écrit le Markdown et
   `flush()` + `fsync()` immédiatement, puis appelle le callback de
   progression avec `(done, total, s/segment, temps restant, texte)`.
6. `postprocess.py` passe le nettoyage final une fois la traduction complète.
6 bis. Le titre du fichier est traduit et le `.md` renommé en conséquence
   (voir §5 bis) — dernière étape, une fois tout le reste terminé.
7. L'UI active « Ouvrir le fichier » / « Ouvrir le dossier ».

## 5 bis. Nettoyage des pages et traduction du titre (ajouté le 23/08/2026)

Deux fonctionnalités distinctes, ajoutées ensemble à la demande de
l'utilisateur, en s'inspirant (sans le reprendre tel quel) d'un mécanisme
similaire d'un autre de ses projets (`deepAnalyze` dans `substans.app`) —
seule la partie « en-têtes/pieds de page répétés » a été jugée pertinente
pour TRANSLAX ; la détection de phrases dupliquées par chevauchement de
page (pertinente pour du texte issu d'OCR) n'a **pas** été portée, faute
d'un cas concret à calibrer dessus — à ajouter plus tard si besoin.

### En-têtes/pieds de page répétés et numéros de page — `core/page_cleanup.py`

Sans ce nettoyage, un pied de page comme « 1 | P a g e » ou un en-tête
« xiv Foreword » entre dans le texte à traduire au même titre que le vrai
contenu, et peut couper une phrase à cheval sur deux pages.

**Principe** : contrairement à un balayage de tout le document à la
recherche de lignes répétées, seules les 3 premières et 3 dernières lignes
non vides de chaque page sont examinées — c'est là, physiquement, que
vivent les en-têtes et pieds de page. Un filtre de forme (calibré comme
celui de `deepAnalyze` : ≤ 80 caractères, ≤ 6 mots) élimine d'abord la
quasi-totalité du bruit — sans lui, une vraie phrase de corps de texte qui
déborde sur le bord d'une page (très fréquent) fait exploser le nombre de
candidats à comparer entre eux (constaté en pratique : plusieurs minutes
sur un livre de 231 pages, ramené à 2 secondes avec ce filtre).

Deux catégories, détectées séparément :
- **Numéro de page isolé** (« 42 », « xiv », « XIV ») : la forme seule
  suffit, retiré sans condition de répétition.
- **En-tête/pied de page répété** : un numéro de page éventuel en tête ou
  en fin de ligne est d'abord retiré pour ne garder que le « cœur » du
  texte (regroupe « 1 | P a g e » et « 2 | P a g e », ou « xiv Foreword »
  et « Foreword xix »), puis les cœurs sont regroupés par ressemblance
  (`difflib.SequenceMatcher`, seuil 0.82) plutôt que par égalité stricte —
  l'OCR introduit du bruit d'une occurrence à l'autre (rencontré en
  pratique : « THBVODOU » sur une page, « THEVODOU » sur une autre, pour
  le même en-tête). Seuil de répétition **absolu** (3 occurrences
  minimum), pas un pourcentage du livre entier — un pourcentage aurait
  raté les sections courtes (« Foreword »/« Preface », ~10 pages chacune
  dans un livre de 386 pages).

**Calibré et vérifié sur deux vrais PDF fournis par l'utilisateur** (pas
seulement testé sur des cas synthétiques) :
- `The Code to the Matrix.pdf` (231 pages) : pied de page « N | P a g e »
  détecté sur 231/231 pages.
- `The-vodou-quantum-leap-...pdf` (386 pages) : 9 en-têtes de
  chapitre/section détectés (« The Vodou Wonderland », « Introduction »,
  « Foreword », « Index », « Bibliography »... ), plus 22 numéros de page
  isolés et une publicité de fin de livre répétée sur 4 pages — moins de
  2 % du texte total retiré dans les deux cas.
- `A new era of thought.pdf` (302 pages, vérifié le 23/08/2026) : ce livre
  utilise la disposition classique verso/recto — le titre du livre en haut
  des pages paires, le titre du chapitre en cours en haut des pages
  impaires. Comme le regroupement se fait par CONTENU (pas par périodicité
  attendue), les deux motifs qui alternent sont détectés comme deux
  groupes séparés sans logique dédiée à écrire : « A NEW ERA OF THOUGHT. »
  (69 occurrences, verso), « Introduction » / « CHAPTER VI. » / etc.
  (recto, un groupe par chapitre traversé). 23 groupes au total détectés
  sur ce livre.

  **Bug trouvé et corrigé en le vérifiant** : une occurrence de
  « Introduction. » avait son numéro de page mal extrait par l'OCR (un
  symbole isolé, « •/ », à la place du chiffre) — ni chiffre ni romain, `_strip_number_token`
  ne le retirait pas, et le très grand espace laissé entre le titre et ce
  résidu faisait chuter le ratio de ressemblance à 0.80 (sous le seuil de
  0.82), la ligne restait donc non détectée sur cette seule page. Corrigé
  par `TRAILING_JUNK_RE` (retire un résidu court d'1 à 3 caractères non
  alphanumériques en fin de ligne) et par la normalisation des espaces
  internes dans `_strip_number_token` — les deux appliqués uniquement à la
  signature utilisée pour le REGROUPEMENT, jamais à la ligne réellement
  supprimée. Test de non-régression synthétique dans
  `tests/test_page_cleanup.py` (section 3, alternance + numéro illisible).

**Le rapport est montré avant de continuer** (demande explicite de
l'utilisateur) : si `page_cleanup` détecte quelque chose, `pipeline.run_job`
appelle `on_page_cleanup(report)` et attend une décision — « clean »
(utiliser le texte nettoyé), « original » (l'ignorer) ou « cancel »
(abandonner, rien n'est encore écrit à ce stade). Dans l'UI, ça se traduit
par une vraie boîte de dialogue (`MainWindow._on_cleanup_review_needed`) ;
en CLI, le rapport est affiché et « clean » est appliqué automatiquement
(sauf `--keep-page-headers`).

**Pont technique** (`ui/worker.py`) : la décision doit venir d'une boîte de
dialogue, donc du thread principal, alors que `pipeline.run_job` tourne
dans le thread de travail et a besoin d'attendre la réponse avant de
continuer — un signal Qt seul ne suffit pas (émettre ne bloque pas). Le
pont est un `threading.Event` : le thread de travail émet le signal puis
attend dessus ; le thread principal, une fois la boîte de dialogue fermée,
enregistre la décision et débloque l'attente.

> **Piège rencontré en écrivant le test correspondant** : connecter ce
> signal à une fonction Python ordinaire (pas une méthode liée d'un
> QObject) laisse Qt incapable de déterminer le thread du récepteur, et
> bascule la connexion en direct — le gestionnaire s'exécute alors de
> façon synchrone SUR LE THREAD DE TRAVAIL au lieu d'être mis en file
> d'attente vers le thread principal, ce qui a fini par bloquer toute la
> suite (`finished`, `thread.quit()` jamais délivrés). La connexion réelle
> de l'appli, vers une vraie méthode de `MainWindow`, n'a pas ce problème —
> mais c'était une leçon à retenir pour la suite des tests.

### Traduction du titre — `pipeline._translate_title`

« i am.pdf » → « je suis.md ». Le nom du fichier (pas son contenu) est
traduit via le même moteur déjà chargé pour le document, juste après son
chargement (pas de rechargement séparé). Activé par défaut
(`Job.translate_title = True`).

**Le fichier de travail reste sous son nom d'origine jusqu'à la toute fin**
— reprise, état (`.translax/`), tout continue de fonctionner exactement
comme avant, sans aucune modification. Le renommage n'intervient qu'une
fois la traduction *entièrement* terminée (pas annulée), en tout dernier :
`out_path.rename(...)` vers le nom traduit (assaini des caractères
invalides sur Windows/Mac/Linux, `core/pipeline.py::_sanitize_filename`).
`_unique_path` évite d'écraser un fichier déjà présent sous ce nom.

**Limite connue et assumée** : si l'utilisateur relance une traduction sur
le MÊME fichier source après ce renommage, le fichier au nom d'origine
n'existe plus (il a été renommé, pas copié) — la détection de reprise/
« déjà traduit » de l'UI ne le retrouve donc pas, et une traduction
complète repart de zéro plutôt que de proposer d'ouvrir le résultat déjà
là. Pas de perte de données (le fichier déjà traduit reste intact,
`_unique_path` évite l'écrasement — le nouveau résultat prendrait un nom
du type « Je suis (2).md ») mais c'est un temps de travail gaspillé dans
ce cas précis. Corrigible plus tard (faire porter la détection de reprise
sur l'empreinte du fichier source plutôt que sur le nom de sortie) si ça
s'avère gênant à l'usage — pas fait maintenant pour ne pas complexifier
une mécanique de reprise déjà éprouvée.

## 5 ter. Numéro de version et date de build (ajouté le 23/08/2026)

Affiché tout en bas de la fenêtre, discret (petit, gris atténué) : «
TRANSLAX v1.0.0 · 23rd August 2026 ». Deux fichiers :

- **`core/version.py`** — contient `VERSION` et `BUILD_DATE` (AAAA-MM-JJ),
  plus le formatage en ordinal anglais (`_ordinal`) : 1st, 2nd, 3rd, 4th…
  avec l'exception standard 11e/12e/13e (jamais « -st »/« -nd »/« -rd »,
  toujours « -th »). Vérifié sur les cas limites (1, 2, 3, 4, 11, 12, 13,
  21, 22, 23) avant intégration.
- **`scripts/stamp_build_date.py`** — régénère `BUILD_DATE` avec la date
  réelle du jour (`datetime.date.today()`), à lancer avant chaque
  empaquetage (voir §8 et `MACOS_BUILD.md`) pour que la date affichée
  reflète quand l'exécutable a vraiment été construit, pas juste quand
  quelqu'un l'utilise. `--bump patch|minor|major` incrémente aussi
  `VERSION` (sinon seule la date change).

`ui/main_window.py::version_label` est ajouté **hors de la zone de
défilement** (`outer.addWidget`, pas `root.addWidget`) : toujours visible
en bas de la fenêtre, jamais emporté par le défilement du contenu.

## 5 quater. Animation de la progression (ajoutée le 23/08/2026)

La barre de progression glisse vers chaque nouvelle valeur au lieu d'y
sauter directement (`QPropertyAnimation` sur la propriété `value`,
`QEasingCurve.OutCubic`, 220 ms — `PROGRESS_ANIMATION_MS` dans
`ui/main_window.py`). Délibérément discrète : ni rebond ni effet voyant,
juste un glissement bref à chaque segment traduit. Le tout premier
`_on_progress` (qui fixe l'intervalle de la barre) saute directement à sa
valeur — animer depuis une valeur de départ arbitraire n'aurait pas de
sens. Vérifié en échantillonnant `progress_bar.value()` pendant la
transition : la valeur progresse par paliers (ex. 10 → 18 → 25 → 30 → 34…
→ 40), pas d'un bond.

## 5 quinquies. Anti-veille pendant la traduction (ajouté le 23/08/2026)

Une traduction peut durer des heures. Si le système se met en VEILLE
pendant ce temps (pas juste l'écran qui s'éteint), Windows/Mac suspendent
tous les processus en cours — y compris celui de TRANSLAX : la traduction
ne plante pas, elle se fige purement et simplement jusqu'au réveil de la
machine.

**Choix retenu, parmi trois envisagés avec l'utilisateur** : actif
UNIQUEMENT pendant qu'une traduction tourne réellement — pas un minuteur à
régler (oblige à deviner une durée, jamais fiable pour un document dont on
ne connaît pas le temps de traitement à l'avance), pas « tant que le
logiciel est ouvert » (bloquerait la veille même sans rien faire tourner).
Câblé au seul endroit qui bascule déjà l'UI entre inactif/en cours
(`MainWindow._set_running`) — aucune logique dupliquée ailleurs.

**`core/keep_awake.py`** — deux mécanismes, un par OS, tous deux déjà
fournis par le système (aucune dépendance supplémentaire) :
- **Windows** : `SetThreadExecutionState` (API Win32 officielle,
  `ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED` au démarrage,
  `ES_CONTINUOUS` seul à l'arrêt pour rendre la main à Windows) — vérifié
  avec le vrai appel système, pas un simulacre (le retour de l'API,
  non nul, confirme le succès).
- **macOS** : `caffeinate -d -i`, lancé en sous-processus pour la durée du
  job puis terminé — l'utilitaire système standard pour ça, aucune
  bibliothèque tierce nécessaire. **Non testé sur un vrai Mac** faute
  d'accès à un (même limite que le reste du portage macOS, voir §8 bis).
- Aucun mécanisme sur les autres OS (Linux…) : `start()` ne fait rien,
  sans planter — la traduction fonctionne, juste sans cette protection.

**Indicateur visible et rappel de chargeur** (demande explicite de
l'utilisateur) : `MainWindow.keep_awake_label`, dans la carte Progression,
visible UNIQUEMENT pendant qu'une traduction tourne (pas une notification
ponctuelle qu'on pourrait manquer) : « Mise en veille du système
désactivée pendant la traduction — pensez à brancher votre chargeur. »
Teinte bleutée discrète, pour se distinguer d'un message de statut normal
sans être alarmante.

**Nettoyage à la fermeture forcée** : si l'utilisateur ferme l'appli
pendant une traduction (boîte de dialogue « Quitter »), `closeEvent`
arrête aussi l'anti-veille explicitement — sur Mac, un `caffeinate` lancé
en sous-processus ne mourrait pas tout seul avec la fenêtre, il resterait
à bloquer la veille pour rien après coup.

## 5 sexies. Reboost — pouls de génération (ajouté le 23/08/2026)

`translate.Progress` (voir §5) n'avance qu'une fois un segment ENTIER
traduit. Pour un gros segment ou un modèle plus lourd (1.3B, 3.3B), ça peut
laisser l'utilisateur sans le moindre signal pendant plusieurs dizaines de
secondes — impossible de distinguer « ce segment est juste long » de
« le logiciel s'est figé silencieusement ». Demande explicite de
l'utilisateur : un bouton **Reboost**, disponible en permanence pendant une
traduction, qui « interroge l'ordi » sans jamais rien arrêter ni relancer —
purement un indicateur — et le même contrôle déclenché automatiquement
après 15 minutes pile sans activité.

**`core/heartbeat.py`** : `Heartbeat`, un simple horodatage (+compteur de
pas, informatif) mis à jour à chaque pas de décodage NLLB, lu depuis le
thread d'interface. Écriture/lecture d'un float et d'un int : atomique
sous le GIL, aucun verrou nécessaire (même raisonnement que
`core/keep_awake.py`).

**Pourquoi un `StoppingCriteria` et pas un `streamer`** : un `streamer`
serait l'outil habituel pour ce genre de pouls, mais la version installée
de `transformers` lève explicitement `NotImplementedError` dès que
`num_beams > 1` (« `streamer` cannot be used with beam search (yet!) ») —
or ce moteur traduit toujours avec `num_beams=4`. Un `StoppingCriteria`,
lui, est appelé à CHAQUE pas de décodage quelle que soit la stratégie, beam
search comprise (vérifié en lisant `_beam_search` dans
`transformers/generation/utils.py`). `translate._HeartbeatCriteria` ne
renvoie donc jamais « stop » — il ne fait qu'appeler `heartbeat.beat()` à
chaque pas.

**Vérifié, pas supposé** : traduction d'un même segment avec et sans le
pouls branché, sortie identique mot pour mot dans les deux cas — d'abord
en appelant `model.generate()` directement (27 pas observés sur une
phrase), puis via le vrai chemin de production `PreciseEngine.translate()`
(38 pas sur une autre phrase). Le pouls ne modifie donc ni le texte produit
ni les réglages documentés en §5 (num_beams, no_repeat_ngram_size...).

**Câblage** : `Job` n'a pas besoin d'un nouveau champ — `run_job(...,
heartbeat=...)` transmet directement l'objet à `translate.translate_
segments`, lui-même à `engine.translate()`. `TranslationWorker` crée
l'instance (`self.heartbeat`) à la construction et la lit directement
depuis `MainWindow` (pas de signal Qt ici : une simple lecture d'attribut
depuis le thread principal pendant que le worker écrit dessus depuis le
sien, sûr sans verrou — voir le docstring de `ui/worker.py`).

**Interface** : bouton « Reboost » à côté de Stop, actif UNIQUEMENT
pendant une traduction (même bascule que Stop dans `_set_running`).
Cliquer, ou le déclenchement automatique, écrit un verdict dans le
journal — jamais de boîte de dialogue, cohérent avec le parti pris
« discret » déjà pris pour l'animation de la progression (§5 quater) :
- moins d'1 min : « actif — dernier mot généré il y a N s. »
- 1 min à 15 min : « actif — segment plus long que d'habitude… »
- ≥ 15 min (`HEARTBEAT_AUTO_THRESHOLD_S`) : verdict d'alerte, avec
  suggestion de fermer/relancer (la reprise repart de là où c'est resté).

Une `QTimer` (`_heartbeat_timer`, 10 s d'intervalle) tourne pendant toute
traduction et déclenche automatiquement ce même verdict une seule fois par
blocage dès que 15 minutes se sont écoulées sans nouveau mot — `_auto_
reboost_done` évite de spammer le journal toutes les 10 s tant que ça reste
bloqué, et se réarme dès qu'un mot est de nouveau produit.

## 5 septies. Dossier de sortie mémorisé (ajouté le 23/08/2026)

Demande explicite de l'utilisateur : que le dossier de sortie choisi soit
mémorisé d'un lancement à l'autre, pas seulement pour la session en cours.

**`core/settings.py`** : petit fichier JSON dans le dossier de
configuration standard de l'OS (jamais à côté du code, qui peut être en
lecture seule une fois empaqueté) — `%APPDATA%\TRANSLAX\settings.json` sur
Windows, `~/Library/Application Support/TRANSLAX/settings.json` sur Mac.
Écriture atomique (fichier temporaire + `replace`, même technique que
`core/state.py`). Un fichier absent, corrompu ou illisible ne bloque
jamais le démarrage — retombe silencieusement sur « aucun réglage ».
`get_default_output_dir()` revient aussi à `None` si le dossier mémorisé a
été supprimé/déplacé depuis (mieux vaut redemander que d'écrire dans un
dossier qui n'existe plus), sans effacer le réglage pour autant — il
refonctionne si le dossier réapparaît au même endroit.

**Interface** : `MainWindow.__init__` charge le réglage au démarrage ;
`_pick_output_dir` l'enregistre à chaque changement ; un bouton
« Réinitialiser », à côté de « Choisir… », efface le réglage et revient au
comportement par défaut (sortie dans le même dossier que le fichier
source).

## 5 octies. Titre traduit dès le début, pas seulement à la fin (ajouté le 23/08/2026)

Jusqu'ici, le fichier de sortie était écrit sous le nom du fichier source
(`i am.md`) du premier au dernier segment, et seulement renommé selon le
titre traduit (`je suis.md`) une fois la traduction *entièrement* terminée.
Demande explicite de l'utilisateur : que le fichier porte déjà son titre
traduit dès le premier segment écrit, avant même que le corps du livre ne
commence à être traduit.

**Le calcul du titre traduit se faisait déjà tôt** (juste après le
chargement du modèle, avant `translate_segments`) — seul le RENOMMAGE du
fichier attendait la toute fin. Le correctif avance donc surtout ce
renommage, pas la traduction du titre elle-même.

**La difficulté n'est pas de renommer tôt, mais de ne pas casser la
reprise.** `run_job` recalcule TOUJOURS le nom de départ
(`job.resolved_output()`) à partir du seul fichier source, sans modèle —
c'est ce qui rend la détection de reprise instantanée aujourd'hui. Si le
fichier réel avait été renommé au lancement précédent, ce recalcul ne
retrouverait plus rien (il ne connaît que l'ancien nom) : la reprise
échouerait silencieusement et redémarrerait de zéro — exactement la limite
déjà documentée pour le renommage de fin, mais bien plus grave une fois le
renommage déplacé au tout début (ça toucherait alors CHAQUE reprise, pas
seulement la reprise d'un job déjà entièrement fini).

**Solution : un fichier « pointeur »** (`core/state.py` —
`pointer_path`/`save_output_pointer`/`resolve_output_path`), à côté de
l'état habituel dans `.translax/` : associe le nom dérivé du fichier
source à l'endroit où le travail est réellement écrit, avec l'empreinte du
fichier source comme garde-fou (même principe que `can_resume`). Lu en
tout début de `run_job`, avant même l'extraction — une simple lecture
JSON, jamais besoin de charger le modèle pour savoir où chercher.

**Câblage dans `run_job`** :
1. `out_path = resolve_output_path(job.resolved_output(), input_path)` —
   redirige immédiatement si un pointeur valide existe.
2. Extraction, nettoyage de pages, segmentation, détection de reprise :
   inchangés, sur ce `out_path` (éventuellement redirigé).
3. Titre traduit **seulement si nécessaire** : job qui démarre vraiment de
   zéro, OU job repris dont le nom n'a encore jamais été redirigé (job
   commencé avant cette fonctionnalité) — une reprise déjà pointée ne
   retraduit jamais le titre en vain (économise un appel modèle à chaque
   reprise, la traduction étant déterministe).
4. Si le job démarre de zéro et que le titre traduit diffère du nom
   courant : le fichier .md n'existe pas encore (rien à renommer) — on
   déplace juste les fichiers de travail (état, cache de segments) vers le
   nouveau nom, on écrit le pointeur, et `translate_segments` crée le .md
   directement sous le bon nom dès le premier segment.
5. Le bloc de renommage en fin de fonction reste un filet de sécurité pour
   un job repris sans être jamais passé par ce chemin (comparaison sur le
   stem de `out_path`, pas celui du fichier source, pour ne jamais
   renommer une deuxième fois un fichier déjà correctement nommé).

**Vérifié** (`tests/test_pipeline.py`, sections 8 et 9) : le fichier porte
son nom traduit dès le premier segment (jamais vu sous l'ancien nom, même
un instant) ; un job interrompu puis relancé — simulant une vraie
fermeture/réouverture de l'appli, avec les mêmes chemins que
recalculerait l'interface — retrouve sa progression via le pointeur et ne
retraduit que les segments manquants, sans redémarrer de zéro et sans
retraduire le titre déjà connu.

## 5 nonies. Reprise automatique proposée au démarrage (ajouté le 23/08/2026)

Demande explicite de l'utilisateur, en réaction directe à la reprise déjà
en place (§4/§5 octies) : plutôt que de devoir ressélectionner
manuellement le fichier après une fermeture prématurée de l'appli ou un
plantage, une boîte de dialogue propose de reprendre automatiquement au
lancement suivant.

**`core/settings.py`** — deux fonctions de plus, même fichier JSON que le
dossier de sortie mémorisé (§5 septies) : `get_last_job`/`set_last_job`.
Le « dernier job » est enregistré dans `MainWindow._start_impl` À CHAQUE
lancement de traduction (avant même que le premier segment ne soit
traduit — si l'appli plante juste après, le repère doit déjà être sur le
disque), et effacé dans `_on_finished` UNIQUEMENT si le job s'est terminé
avec succès (pas annulé). Une interruption ou une erreur le laisse
volontairement en place : c'est justement le cas que ce réglage sert à
retrouver.

**Ce repère ne dit jamais, à lui seul, qu'il faut proposer une reprise.**
Il indique seulement OÙ regarder. La décision revient entièrement à
`state.can_resume` (après redirection éventuelle via `state.
resolve_output_path`, §5 octies) — exactement la même vérification déjà
utilisée pour la reprise manuelle. Si le fichier source a disparu, si le
job est en réalité déjà terminé, ou si l'état est corrompu, rien ne
s'affiche et le repère est simplement effacé sans déranger l'utilisateur.

**Déclenchement** : `QTimer.singleShot(0, self._offer_resume_last_job)`
dans `MainWindow.__init__`, PAS un appel direct — le délai de 0 diffère
l'exécution jusqu'à ce que la boucle d'évènements Qt tourne réellement
(donc après le premier affichage de la fenêtre), pour ne jamais faire
apparaître une boîte de dialogue modale avant qu'il y ait une fenêtre
visible derrière elle.

**Deux issues à la boîte de dialogue** :
- **Reprendre** : `_apply_job_snapshot` repeuple tous les champs de
  l'interface (fichier source, dossier de sortie, langues, modèle,
  case de nettoyage) depuis le repère mémorisé, puis
  `_start_impl(force_resume=True)` relance directement le job — ce
  paramètre saute la boîte de dialogue « fichier déjà existant » habituelle
  (`_resolve_conflict`), qui serait redondante juste après avoir répondu
  « Reprendre » à la proposition automatique.
- **Ignorer** : ne touche à rien — ni au repère mémorisé, ni au fichier.
  La proposition revient au prochain lancement tant que le job reste
  réellement inachevé (choix délibéré : mieux vaut redemander qu'oublier
  silencieusement une traduction en cours).

**Vérifié** (`tests/test_ui.py`, section 8) : aucune proposition quand
rien n'est mémorisé ; « Ignorer » ne démarre rien et laisse le repère
intact ; « Reprendre » repeuple bien la configuration d'origine et termine
la traduction interrompue jusqu'au bout ; le repère est effacé une fois
le job réellement fini. Piège rencontré en écrivant ce test, pas dans le
code de production : plusieurs `MainWindow()` coexistant dans le même
processus de test (jamais le cas dans l'appli réelle, qui n'en crée
qu'une) peuvent chacune laisser un `QTimer.singleShot(0, ...)` en attente
-- le test le déclenche explicitement via `app.processEvents()` juste
après la construction de chaque fenêtre plutôt que d'appeler la méthode
directement, pour ne jamais laisser un timer en suspens se déclencher plus
tard pendant qu'un autre scénario est en cours.

## 5 decies. Traduire X — extraction par vision IA (ajouté le 24/08/2026)

Demande explicite de l'utilisateur, à partir d'un cas concret : certains
PDF anciens contiennent déjà un calque de texte OCR de mauvaise qualité
(scans d'époque), avec du bruit de caractères (« acc()rding », « tlieir »,
« wbo ») voire des phrases entières absentes. Aucun nettoyage typographique
ne peut deviner ce qui manque — il faut *voir* la page, comme l'utilisateur
l'a démontré lui-même avec une IA multimodale externe (ChatGPT) avant de
formuler cette demande.

**Deux boutons, jamais un seul** : Traduire reste inchangé, 100 % local et
gratuit. Traduire X fait exactement la même chose, avec une étape
supplémentaire AVANT la segmentation — c'est la seule différence, comme
demandé (« rien à réinventer »). Traduire X nécessite une connexion
internet et une clé API Anthropic personnelle de l'utilisateur, facturée à
son usage — jamais fournie par TRANSLAX, jamais codée en dur.

**Pourquoi Claude et pas un modèle local** : une IA de vision générative de
pointe est nécessaire pour ce niveau de correction contextuelle ; aucun
modèle local raisonnable sur une machine sans GPU (le NLLB-200 de ce
projet ne fait que traduire, il ne « voit » rien) n'atteint cette qualité.
Modèle choisi par l'utilisateur après comparaison coût/qualité :
**Claude Sonnet 5** (`vision_ocr.DEFAULT_MODEL`), nettement moins cher que
Opus pour un usage à fort volume (des livres entiers, page par page).

### `core/vision_ocr.py`

- Chaque page est rendue en image (`page.get_pixmap(dpi=150)`) puis envoyée
  à Claude avec une consigne stricte : transcrire FIDÈLEMENT ce qui est
  imprimé, corriger les artefacts de scan/OCR évidents en s'appuyant sur le
  contexte, mais **ne jamais substituer un mot par un autre**, même plus
  logique, que celui réellement imprimé. Un passage sur lequel le modèle
  n'est pas sûr est marqué (`<uncertain>…</uncertain>`), jamais deviné en
  silence — le texte le plus probable est gardé, mais l'incertitude est
  remontée dans le rapport, jamais cachée.
- Le résultat (pages jointes par `\f`) est un remplacement direct de
  `extract.extract_text()` : tout le reste du pipeline (nettoyage des
  en-têtes/pieds de page, segmentation, traduction, titre traduit,
  renommage) tourne ensuite SANS AUCUNE modification.
- **Reprise** : chaque page transcrite est ajoutée immédiatement à un
  cache JSON Lines à côté de la sortie (`<stem>.vision_cache.jsonl`) --
  interrompre en cours de route (Stop, fermeture, plantage) ne fait jamais
  repayer une page déjà traitée : relancer Traduire X sur le même fichier
  reprend exactement où ça s'est arrêté, page par page.
- Erreurs API (clé invalide, débit dépassé, réseau indisponible) traduites
  en messages clairs (`VisionOcrError`), jamais une trace Python brute.

### Validé avant, pendant, et après l'écriture du code

- **Preuve visuelle directe, pas seulement l'API** : avant d'écrire une
  ligne de `vision_ocr.py`, la page exacte de l'exemple donné par
  l'utilisateur (« Hindu Magical Occultism Test.pdf ») a été rendue en
  image et regardée directement (pas juste envoyée à l'API en aveugle),
  pour confirmer à l'œil ce qui est réellement imprimé avant de juger la
  qualité de la transcription automatique.
- **Résultat** : le calque de texte existant contenait à la fois du bruit
  de caractères ET une phrase ENTIÈRE absente (« Nevertheless the kingdom
  of peace and righteousness shall not cover the earth over until this is
  understood by all men and women. ») — vérifiée présente sur l'image,
  invisible dans `extract.extract_text()`. La vision a restitué les deux
  correctement, dans le bon ordre de lecture (le calque existant avait
  aussi des fragments de phrases mélangés entre eux sur cette page).
- **Coût réel mesuré** : ~0,02 $/page avec Sonnet 5 (21 pages ≈ 0,42 $).
- **Bout en bout, avec le vrai pipeline** (pas seulement un script
  autonome) : `pipeline.run_job` avec `use_vision_ocr=True`, la vraie clé
  API, et le vrai moteur NLLB-200 600M -- extraction vision -> nettoyage
  des pages -> segmentation -> traduction réelle -> fichier final,
  intégralement, sans aucun raccourci.

### Interface

- Bouton **Traduire X**, à côté de Traduire — refuse un fichier non-PDF
  (rien à corriger visuellement sur un .txt/.md déjà en texte brut) et
  exige une clé API renseignée avant de démarrer.
- Champ **Clé API Anthropic** dans Réglages (`core/settings.py`,
  `get_anthropic_api_key`/`set_anthropic_api_key`) — stockée en clair dans
  le même fichier JSON local que le dossier de sortie mémorisé, jamais
  envoyée ailleurs qu'à l'API Anthropic elle-même.
- **Récapitulatif scrollable avant la traduction** (demande explicite :
  « je dois tout voir d'un coup ») — `VisionReviewDialog`, dans une
  `QScrollArea` : uniquement les pages réellement corrigées ou signalées
  incertaines (les pages identiques avant/après n'apportent rien à
  vérifier et noieraient les vraies corrections dans un livre de
  centaines de pages — leur nombre est affiché, jamais caché), avant/après
  côte à côte par page, coût estimé, boutons Continuer/Annuler. Bloque le
  thread de travail via le même pont `threading.Event` que le nettoyage
  des pages (`ui/worker.py`, `vision_review_needed`/`set_vision_decision`).
- La barre de progression et le pouls Reboost s'étendent naturellement à
  cette étape : une page transcrite compte comme un battement (voir
  `TranslationWorker._on_vision_progress`), sinon Reboost signalerait à
  tort un blocage après 15 minutes sur un gros livre dont l'extraction
  vision (des appels réseau page par page, pas le rythme de NLLB) prend
  plus de temps que ça.

### Filtre de contenu Anthropic sur une page précise (rencontré le 25/08/2026)

Cas réel, pas hypothétique : sur « Hindu Magical Occultism BOOK 2.pdf »
(293 pages), la page 12 (Chapitre XII, une polémique religieuse du 19e
siècle critiquant vivement le clergé et la papauté — « self-styled seat of
Christianity », « bigoted agents », clergé qualifié de « debauched ») a
fait échouer TOUT le job avec :
```
VisionOcrError : Erreur de l'API Anthropic : Error code: 400 -
{'type': 'invalid_request_error', 'message': 'Output blocked by content filtering policy'}
```
Vérifié en lisant la page moi-même (rendue en image) : rien qui justifie
un blocage dans l'absolu — un texte de critique religieuse historique,
polémique mais banal pour son genre et son époque. Un classifieur
automatique appliqué page par page, sans le contexte du reste du livre,
peut réagir à ce type de rhétorique même légitime — un faux positif
plausible, pas une preuve que le contenu du livre pose un vrai problème.

**Le vrai bug n'était pas le blocage lui-même** (hors du contrôle de
TRANSLAX -- c'est une décision d'Anthropic) **mais la réaction du
pipeline : une seule page bloquée faisait échouer les 292 autres.** Pour
un genre de livre (religion, occultisme, polémique historique) que
l'utilisateur traite couramment, ce risque était appelé à se reproduire.

**Corrigé** : `_is_content_filter_block` détecte spécifiquement ce cas
(inspecte `exc.body`, pas juste « c'est une erreur 400 » -- une AUTRE
erreur 400, comme un nom de modèle invalide, doit continuer à faire
échouer tout le job bruyamment, pas être masquée en silence). Sur ce cas
précis : la page concernée garde son texte ORIGINAL (non corrigé, tel que
`extract.extract_text()` l'aurait donné) au lieu d'être perdue, marquée
`PageResult.vision_failed=True`, et le reste du livre continue
normalement. Le récapitulatif (`VisionReviewDialog`) l'affiche
distinctement (🚫, pas le ⚠ d'une simple incertitude), avec un bandeau
d'avertissement en haut de la fenêtre si au moins une page est concernée
-- l'utilisateur voit exactement quelle(s) page(s) n'ont pas pu être
corrigées, plutôt que de perdre tout le travail déjà fait sur les autres.

### Marqueur début/fin dans le document final (ajouté le 25/08/2026)

Demande explicite de l'utilisateur, en suite directe du point ci-dessus :
que le récapitulatif ne suffit pas -- il veut retrouver, dans le fichier
`.md` final lui-même, une mention claire de début et de fin autour du
texte original d'une page bloquée, avec le numéro de page exact, pour
pouvoir aller la revoir et la traduire ailleurs à la main.

**`core/segment.py`** : `RESTRICTED_MARKER_PREFIX` (`« ⛔ TRANSLAX »`).
`core/vision_ocr.py` (`_wrap_restricted_page`) entoure le texte original
d'une page bloquée de ce marqueur avant de l'ajouter au flux de pages --
aplati en un seul bloc SANS ligne vide interne, pour garantir qu'il ne
forme qu'UN SEUL segment après la segmentation, jamais coupé en plusieurs
morceaux dont certains échapperaient au marqueur. `_classify` (stratégie
« blocs ») le détecte par préfixe et lui donne le type `"restricted"` --
vérifié AVANT la détection de titre, pour qu'un marqueur court ne soit
jamais pris pour un `##`. Repérage best-effort en stratégie « flux »
(`_segment_flow`) : cette stratégie aplatit tous les sauts de ligne avant
de regrouper par phrases, donc l'isolation n'est pas garantie aussi
proprement -- au pire, un peu de texte voisin reste non traduit avec lui,
jamais une perte de contenu.

**`translate.translate_segments`** n'envoie JAMAIS un segment de type
`"restricted"` au moteur -- son texte original est écrit tel quel.
`render_markdown` le rend en citation Markdown (`> `) pour rester
visuellement reconnaissable dans le document final, où qu'il tombe.
`postprocess.cleanup_file` n'y touche pas (seul le type `"heading"` est
concerné par la rétrogradation des faux titres).

**Résultat concret dans le `.md`** :
```
> ⛔ TRANSLAX — DÉBUT PAGE NON VÉRIFIÉE (page 12) : bloquée par le filtre
> de contenu de l'API, texte original (non corrigé) conservé ci-dessous.
> [texte original de la page, non traduit]
> ⛔ TRANSLAX — FIN PAGE NON VÉRIFIÉE (page 12)
```
Le marqueur reste volontairement en français (comme le reste des messages
de l'application), indépendamment de la langue cible de la traduction --
cohérent avec le principe déjà en place ailleurs dans TRANSLAX : la voix
de l'outil est en français, seul le contenu du livre change de langue.

### Tests

`tests/test_vision_ocr.py` : mécanique complète sans jamais appeler la
vraie API (payante) -- un faux client Anthropic reproduit ses VRAIES
classes d'exception (`anthropic.AuthenticationError` etc., construites
avec de vrais objets `httpx2.Request`/`Response`) pour vérifier le bon
message d'erreur pour le bon type d'échec ; reprise depuis le cache (aucun
second appel pour une page déjà traitée) ; arrêt en cours de route (cache
partiel conservé) ; un filtre de contenu sur UNE page au milieu d'un job
de plusieurs pages ne fait plus échouer les autres, texte original
conservé pour cette page, jamais retentée après coup (section 7).
`tests/test_ui.py` section 9 : refus non-PDF, refus sans clé, annulation
au récapitulatif (rien écrit), et le chemin complet jusqu'à un fichier
traduit avec le texte CORRIGÉ (pas le texte brut).

`tests/test_segment.py` (nouveau) : un bloc marqué reste un seul segment
de type `"restricted"` en stratégie « blocs », jamais pris pour un titre
même très court ; repérage best-effort en stratégie « flux » ; comportement
inchangé sans marqueur dans le texte. `tests/test_pipeline.py` section 11 :
`translate_segments` n'envoie jamais un segment restreint au moteur (les
vrais paragraphes autour, si) et le rend en citation Markdown, marqueurs
conservés.

## 5 undecies. Extraction seulement — Extraire / Extraire X (ajouté le 24/08/2026)

Demande explicite de l'utilisateur : bénéficier du même nettoyage que
Traduire/Traduire X (en-têtes/pieds de page répétés, vision IA pour les
scans difficiles, structuration en titres/puces/paragraphes) SANS vouloir
traduire — pour simplement récupérer un texte propre dans sa langue
d'origine. Pas une fonction séparée à construire : une case à cocher qui
modifie le comportement des DEUX boutons existants, exactement comme
demandé (« tu n'as rien à réinventer »).

**`Job.extract_only`** court-circuite `pipeline.run_job` juste après la
segmentation : ni modèle NLLB chargé, ni titre traduit, ni renommage --
`translate.write_segments_plain` écrit chaque segment tel quel (langue
source) dans le même format Markdown qu'une traduction
(`render_markdown`), et `postprocess.cleanup_file` s'applique quand même
(la rétrogradation des faux titres fonctionne identiquement sur du texte
source ; la correction des traits d'union, spécifique à un artefact de
détokenisation NLLB, ne trouve simplement rien à corriger sur du texte
jamais passé par NLLB — inoffensif, pas besoin de le désactiver).

**Interface** : case « Extraction seulement » à côté du nettoyage
typographique. La cocher :
- relibelle Traduire → **Extraire** et Traduire X → **Extraire X** (les
  mêmes boutons, mêmes gestionnaires de clic -- jamais un bouton étiqueté
  « Traduire » qui ne traduit pas) ;
- désactive langue cible ET modèle NLLB (aucun des deux n'intervient sans
  traduction) -- et surtout, **saute la confirmation de téléchargement du
  modèle** : demander de télécharger plusieurs Go pour un mode qui ne
  charge jamais de modèle aurait été absurde ;
- ajuste le texte d'aperçu de sortie (pas de mention de titre traduit) et
  le bandeau final (« segments extraits », pas « traduits »).

`_set_running` réactive `tgt_combo`/`model_combo` sans condition à la fin
d'un job -- un piège rencontré en écrivant cette fonctionnalité : sans
correctif, finir un job en mode extraction réactivait ces deux champs même
si la case était restée cochée. Corrigé en ré-appliquant l'état « case
cochée » par-dessus le déverrouillage général, plutôt que de dupliquer
cette logique.

**Vérifié** (`tests/test_pipeline.py` section 10, `tests/test_ui.py`
section 10) : aucun moteur NLLB créé, aucune confirmation de
téléchargement même avec un modèle non caché sélectionné, texte de sortie
dans la langue source (aucun préfixe de traduction), nettoyage des
en-têtes/pieds de page et rétrogradation des faux titres toujours actifs,
libellés de boutons et champs désactivés qui basculent correctement dans
les deux sens.

## 5 duodecies. Import EPUB (ajouté le 25/08/2026)

Demande explicite de l'utilisateur : pouvoir importer un livre numérique
`.epub`, pas seulement PDF/TXT/MD.

**Aucun nouveau code d'extraction, aucune nouvelle dépendance** -- vérifié
avant d'écrire quoi que ce soit (un vrai fichier EPUB minimal mais valide,
une vraie archive ZIP avec son manifeste OPF/spine) : PyMuPDF, déjà utilisé
pour les PDF, ouvre un `.epub` directement et le traite en interne comme
un document paginé -- une « page » par fichier XHTML du spine, dans
l'ordre de lecture du livre. `core/extract.py` renomme juste `_extract_pdf`
en `_extract_paged` et l'utilise pour les deux formats, sans dupliquer la
moindre ligne. Les chapitres sont joints par `\f`, exactement comme les
pages d'un PDF -- `page_cleanup.py` s'applique donc aussi, sans rien coder
de plus, si un livre numérique répète un même bandeau à chaque chapitre.

**Différence structurelle à connaître, pas un bug** : un EPUB est du texte
reflowable, sans mise en page fixe -- ses paragraphes HTML (`<p>`) ne sont
pas toujours séparés par une ligne vide dans le texte que PyMuPDF en
extrait, contrairement à un PDF bien extrait. `segment.detect_strategy`
bascule alors naturellement en stratégie « flux » (reconstruction des
paragraphes par regroupement de phrases, ~90 mots) -- exactement le
mécanisme déjà prévu pour les exports ebook en `.txt` (voir §5, la logique
existait déjà pour ce cas, aucune adaptation nécessaire).

**Traduire X (vision) reste refusé sur un EPUB**, sans changement de code :
la restriction déjà en place (« PDF uniquement ») s'applique automatiquement,
et c'est cohérent -- un EPUB n'a pas de scan/image à corriger, son texte
est déjà la référence.

**Vérifié** (`tests/test_extract.py`, nouveau) : reconnaissance du format
(insensible à la casse), extraction réelle sur une vraie archive EPUB
(pas un simulacre), séparation par chapitre conservée, et un job complet
bout en bout à travers `pipeline.run_job` jusqu'à un fichier `.md` traduit.

## 5 terdecies. Moteur Turbo — CTranslate2 (ajouté le 25/08/2026)

Demande explicite de l'utilisateur, dans la foulée d'une question sur la
vitesse de traduction et la pertinence de louer du calcul distant
(Infomaniak) : ajouter un quatrième profil au sélecteur de modèle, plus
rapide sur CETTE machine (CPU seul, sans GPU), sans passer par le cloud.

**Ce que c'est** : les mêmes poids NLLB-200-600M que le profil « 600M —
Rapide », mais exécutés par CTranslate2 (moteur d'inférence C++ dédié,
quantification int8) au lieu de transformers/PyTorch en mode eager.
Nouvelle classe `translate.FastEngine`, même interface publique que
`PreciseEngine` (`load`/`translate`/`unload`) -- `pipeline.run_job` choisit
la classe à instancier selon `translate.MODEL_INFO[job.model_key].engine`
("precise" ou "fast"), le reste de la fonction ne sait pas lequel tourne
réellement.

**Mesuré sur cette machine (Ryzen 7 4700U, 8 cœurs, CPU seul, pas de GPU),
pas supposé** :
- **13,2× plus rapide** que PreciseEngine sur une phrase de référence
  (17,96 s -> 1,36 s) ; **6,5 à 7,0× plus rapide** sur un lot de 4 phrases
  variées (titre court, phrase d'un mot, phrase avec chiffres/nom propre,
  phrase longue) -- deux mesures répétées, légère variance entre les deux
  runs, dans tous les cas très en dessous du optimum théorique mais un gain
  net et largement suffisant pour changer l'expérience sur un document de
  plusieurs centaines de segments.
- **Sortie quasi identique** : 3 phrases sur 4 rigoureusement identiques,
  caractère pour caractère, à celle de PreciseEngine sur ce même lot. La 4e
  ne diffère que par un mot (singulier/pluriel d'un synonyme -- « des
  évasions confortables » vs « une évasion confortable »), sens inchangé.
  Écart attendu de la quantification int8 (précision numérique réduite
  pouvant faire pencher une décision de beam search déjà proche), documenté
  ici plutôt que caché -- pas un bug de `FastEngine`.
- **Conversion locale unique** : ~77 s pour le 600M sur cette machine (poids
  d'origine déjà en cache HuggingFace), ~0,6 Go supplémentaires sur le
  disque (`model.bin` mesuré : 622 596 105 octets). Se fait une seule fois,
  au premier lancement de ce profil (`FastEngine.load()` convertit si
  besoin) ; conservée ensuite comme les poids HuggingFace des autres
  profils. Rangée dans le dossier de données de l'appli
  (`%APPDATA%\TRANSLAX\ctranslate2-models\`, voir `core/settings.py`), pas
  dans le cache HuggingFace -- les deux caches sont indépendants.

**Limite connue et acceptée pour Reboost, pas contournable** : la
documentation réelle de CTranslate2 4.8.1 (celle installée, pas une
supposition) précise que le `callback` par jeton de `translate_batch` n'est
appelé que « for each generated token when beam_size is 1 ». Ce moteur
utilise `beam_size=4` comme PreciseEngine (même raison : le décodage
glouton boucle sur les segments courts, voir §5 en tête de fichier), donc
ce callback ne se déclenche jamais ici. Le battement Reboost de
`FastEngine` est donc par SEGMENT (avant et juste après chaque appel à
`translate_batch`), pas par jeton comme `_HeartbeatCriteria` pour
`PreciseEngine`. Conséquence concrète : un blocage en cours de génération
d'un très long segment serait détecté un peu plus tard qu'avec le moteur
« Précis » -- mais reste détecté, et de toute façon bien plus rare vu la
vitesse de ce moteur (un segment qui prenait 70 s en « Précis » en prend
moins de 10 en « Turbo »).

**Sélecteur et messages adaptés** : le 4e profil apparaît dans le même menu
déroulant que les trois autres (`600M — Turbo (CTranslate2)`), sans code
UI séparé. `_confirm_model_download`/`_update_model_info` distinguent
« à télécharger » (profils « precise », cache HuggingFace) de
« à convertir localement » (ce profil, dossier CTranslate2) -- deux
opérations réellement différentes, pas la même formulation recyclée à
tort. `translate.is_model_ready(model_key)` fait cet aiguillage une seule
fois, au même endroit, pour ne jamais avoir à dupliquer ce `if` ailleurs.

**Vérifié** (`tests/test_translate.py`, nouveau) : déclaration du profil
dans `MODEL_INFO`, aiguillage `is_model_ready` isolé dans un dossier
temporaire (jamais le vrai `%APPDATA%`), `pipeline.run_job` instancie bien
`FastEngine` pour `"600M-ct2"` et `PreciseEngine` pour les trois autres
(faux moteurs, comme `test_pipeline.py`), et un vrai test de bout en bout :
conversion réelle, vraies traductions, comparées à `PreciseEngine` sur les
mêmes phrases -- sauté proprement (pas en échec) si `ctranslate2` n'est pas
installé ou si le 600M n'est pas en cache.

**Non fait ici, volontairement** : pas de conversion des profils 1.3B/3.3B
(seul le 600M a un profil Turbo pour l'instant -- rien n'empêche d'ajouter
`"1.3B-ct2"`/`"3.3B-ct2"` plus tard sur le même principe, si le besoin s'en
fait sentir) ; pas de sélecteur local/distant (l'idée d'un modèle exécuté
sur une machine louée, proposée par l'utilisateur, reste distincte de ce
gain-ci qui est purement local).

## 5 quaterdecies. Moteur OPUS-MT — licence commerciale (ajouté le 25/08/2026)

Demande explicite de l'utilisateur, suite à un projet de commercialisation
de TRANSLAX (abonnement mensuel, façon DeepL). **Découverte critique avant
d'écrire la moindre ligne de code** : les quatre profils NLLB (Précis ×3 +
Turbo) sont sous licence **CC-BY-NC 4.0 -- usage commercial explicitement
interdit**. Vérifié aussi pour SeamlessM4T (autre modèle de traduction
Meta) : même licence, même interdiction -- Meta ne propose pas de licence
commerciale séparée pour ses modèles de traduction, contrairement à Llama.
**Décision de l'utilisateur** : garder NLLB et son moteur Turbo tels quels,
mais uniquement pour son usage personnel -- jamais dans la version vendue.
Aucun des quatre profils existants n'a été touché ni retiré.

**OPUS-MT (Helsinki-NLP, architecture MarianMT)** est le premier moteur
commercialement propre ajouté : licence **CC-BY 4.0** (attribution requise,
usage commercial permis). C'est la base technique de LibreTranslate,
l'alternative open-source à DeepL qui vend déjà un service dessus -- choix
éprouvé pour ce cas d'usage précis, pas un pari. MADLAD-400 (Google, Apache
2.0, architecture T5) reste prévu pour une prochaine étape, pour élargir la
couverture de langues au-delà de ce qu'OPUS-MT publie par paire.

**Différence structurelle avec NLLB, pas un détail d'implémentation** :
NLLB est UN modèle qui traduit entre 200 langues via un jeton de langue
cible forcé (`forced_bos_token_id`). OPUS-MT n'a pas cette notion -- chaque
modèle (`Helsinki-NLP/opus-mt-{src}-{tgt}`) ne sait traduire que dans UN
sens, pour UNE paire précise. Conséquences dans le code :
- `MODEL_INFO["opus-mt"].repo_id` est un **gabarit** (`"...-{src}-{tgt}"`),
  pas un identifiant utilisable tel quel -- résolu par
  `translate.opus_mt_repo_id(src_lang, tgt_lang)` à partir des langues
  réellement choisies dans le sélecteur.
- Nouvelle table `core/languages.py::FLORES_TO_ISO2` (18 langues, une
  correspondance manuelle vérifiée, jamais devinée par troncature du code
  FLORES -- `zho_Hans` -> `zh`, `arb_Arab` -> `ar`, `kor_Hang` -> `ko` :
  la première syllabe ne suffit pas).
- `is_model_ready`/`_confirm_model_download`/`_update_model_info` ont tous
  les trois dû apprendre à recevoir `src_lang`/`tgt_lang` en plus du
  `model_key` -- une extension de leur contrat, pas juste un nouveau cas
  dans un `if` existant. `src_combo`/`tgt_combo` sont maintenant aussi
  connectés à `_update_model_info` (jamais nécessaire avant : aucun autre
  moteur ne dépend de la paire de langues pour afficher son état).
- **Couverture non garantie** : Helsinki-NLP n'a pas publié un modèle pour
  toutes les combinaisons possibles. `OpusMtEngine.load()` capture l'`OSError`
  que lève `transformers` quand le dépôt n'existe pas sur le Hub, et la
  retraduit en `OpusMtUnavailable` avec un message clair (langues nommées,
  suggestion d'essayer un autre moteur) plutôt que de laisser remonter une
  trace HuggingFace brute jusqu'à la boîte d'erreur de l'interface.

**Vérifié réellement, pas supposé** (`tests/test_translate.py`, section 5) :
- Chargement direct via `AutoTokenizer`/`AutoModelForSeq2SeqLM` (résolus en
  `MarianTokenizer`/`MarianMTModel`) : aucun jeton à forcer, juste
  tokenizer -> generate -> decode. Même mécanisme de pouls que PreciseEngine
  (`_HeartbeatCriteria`, StoppingCriteria) : OPUS-MT reste un modèle
  transformers classique, pas de limite par segment comme FastEngine.
- `eng_Latn` -> `fra_Latn` : 604,6 Mo sur le disque (mesuré), traduction
  réelle sur 4 phrases de test.
- **Différence de phrasé avec NLLB constatée et analysée, pas ignorée** :
  1 phrase sur 4 identique mot pour mot, les 3 autres sont des paraphrases
  fidèles au sens -- attendu et normal entre deux modèles entraînés
  indépendamment (contrairement à FastEngine, qui est le MÊME 600M
  quantifié et dont la quasi-identité de sortie était, elle, l'attente
  correcte). Sur "Hello.", OPUS-MT produit même une meilleure traduction
  (« Bonjour. ») que NLLB (« Je vous en prie. », bizarrerie connue de NLLB
  sur les phrases isolées très courtes).
- Une paire réellement confirmée absente du Hub (`wol_Latn` -> `lin_Latn`,
  vérifié manuellement avant d'écrire le test) lève bien
  `OpusMtUnavailable`, pas une trace brute.
- `pipeline.run_job` instancie bien `OpusMtEngine` pour `model_key="opus-mt"`
  (faux moteurs, comme pour les quatre autres profils).

**Dépendance ajoutée** : `sacremoses` (0.2.0) -- "recommended" par le
tokenizer Marian ; testé sans lui (tout fonctionne, juste un avertissement
à chaque chargement) et avec lui (avertissement disparu, sortie strictement
identique) avant de l'ajouter à `requirements.txt`.

**Non fait ici, volontairement** : pas de vérification à l'avance de la
couverture OPUS-MT pour les 18×17 paires possibles du sélecteur (trop
coûteux à vérifier une par une, et Helsinki-NLP peut faire évoluer son
catalogue) -- le message d'erreur au chargement est le mécanisme de
découverte assumé, pas une liste blanche maintenue à la main ; pas encore
de variante Turbo (CTranslate2) pour OPUS-MT, bien que l'architecture
Marian soit confirmée prise en charge par le convertisseur installé
(`MarianMTLoader`, vérifié dans `ctranslate2.converters.transformers`).

## 5 quindecies. Moteur MADLAD-400 — licence commerciale (ajouté le 25/08/2026)

Deuxième moteur commercialement propre, ajouté dans la foulée du précédent
(voir §5 quaterdecies pour le contexte de licence complet) : **MADLAD-400**
(Google, architecture T5), licence **Apache 2.0**, usage commercial sans
restriction. Sert à élargir la couverture de langues au-delà de ce
qu'OPUS-MT publie par paire : UN seul modèle pour environ 419 langues
(comme NLLB), pas un modèle par paire.

**Troisième mécanisme différent, pas une redite** -- TRANSLAX a maintenant
trois façons distinctes de faire choisir la langue cible à un moteur :
| Moteur | Mécanisme |
|---|---|
| NLLB (Précis/Turbo) | `forced_bos_token_id`, un seul modèle, `src_lang` fixé dès la construction du tokenizer |
| OPUS-MT | un modèle DÉDIÉ par paire, aucun jeton de langue nécessaire |
| MADLAD-400 | un jeton `<2xx>` PRÉFIXÉ AU TEXTE SOURCE, un seul modèle, aucune langue source à préciser |

Vérifié réellement que `<2fr>` est une seule entrée de vocabulaire atomique
(id 46, jamais éclatée en `<`, `2`, `fr`, `>`) -- pas une astuce de prompt
fragile qui dépendrait de la tokenisation. Réutilise directement
`languages_mod.iso2` (même table que OpusMtEngine) pour construire ce
jeton : aucune nouvelle table de correspondance nécessaire.

**Poids réels volontairement PAS téléchargés en développement** : mesuré
via l'API Hub (métadonnées de fichiers, sans téléchargement complet) --
`google/madlad400-3b-mt` pèse **11,8 Go** rien que pour les poids
`safetensors` (le dépôt héberge aussi des variantes GGUF plus légères,
0,9 à 1,6 Go, mais `transformers`/`AutoModelForSeq2SeqLM` ne les utilise
pas -- seul le fichier `.safetensors` est réellement téléchargé). Un
téléchargement de cette taille n'a pas été déclenché juste pour valider ce
moteur : laissé à la confirmation normale de l'interface avant tout
téléchargement de plusieurs Go (déjà en place pour les autres profils,
aucun code neuf nécessaire pour ça). Le mécanisme de tokenisation/prompt,
lui, EST vérifié réellement (tokenizer seul, téléchargement léger, quelques
Mo) -- voir `tests/test_translate.py`, section 6.

**Attente honnête sur la vitesse** : 3 milliards de paramètres contre 600
millions pour le 600M NLLB -- sur un CPU sans GPU (cette machine), un
ralentissement très important est attendu, non mesuré en conditions
réelles ici. `MODEL_INFO["madlad-3b"].speed_note` le dit explicitement
plutôt que de laisser un silence se lire comme une promesse de vitesse.

**Aucun code d'interface neuf nécessaire** : contrairement à OPUS-MT (dont
le `repo_id` est un gabarit dépendant de la paire choisie), MADLAD-400 a un
`repo_id` FIXE -- `is_model_ready`/`_update_model_info`/
`_confirm_model_download` le traitent déjà correctement via leurs branches
génériques existantes, sans branche spécifique à ajouter.

**Vérifié réellement** (`tests/test_translate.py`, section 6) :
- déclaration du profil (`repo_id` fixe, moteur `"madlad"`) ;
- `pipeline.run_job` instancie bien `MadladEngine` pour `model_key="madlad-3b"` ;
- une langue cible réellement hors de la table de TRANSLAX (`heb_Hebr`,
  l'hébreu -- absent des 18 langues exposées aujourd'hui, pas un code
  inventé) lève `MadladUnavailable` sans la moindre requête réseau ;
- le jeton `<2fr>` est atomique dans le vocabulaire réel du tokenizer ;
- la traduction complète est sautée proprement (pas en échec) tant que les
  11,8 Go ne sont pas en cache sur la machine qui exécute le test.

**Non fait ici, volontairement** : pas de téléchargement réel des poids en
développement (voir plus haut) ; pas de variante 7B/10B (seul le profil 3B,
le plus petit des trois, a été ajouté) ; pas de variante Turbo (CTranslate2)
-- l'architecture T5 est confirmée prise en charge par le convertisseur
installé (`T5Loader`/`MT5Loader`, vérifié dans
`ctranslate2.converters.transformers`), mais pas branchée pour l'instant.

## 5 sexdecies. Reprise avec un autre moteur (ajouté le 25/08/2026)

Demande explicite de l'utilisateur : au démarrage, la boîte « Traduction
interrompue » (§5 nonies) forçait jusqu'ici le moteur d'ORIGINE si on
cliquait Reprendre, sans possibilité d'en changer.

**Rien à changer côté `core/pipeline.py`** : `run_job` lisait déjà
`job.model_key` (jamais `job_state.model`) pour décider quel moteur
charger au moment de reprendre -- vérifié en relisant le code avant
d'écrire quoi que ce soit. Seul un petit oubli corrigé : `job_state.model`
restait périmé (l'ancien nom) une fois la traduction reprise avec un autre
moteur ; mis à jour désormais, avec une ligne de journal explicite
(`"Reprise avec un moteur différent : « X » -> « Y »"`).

Côté interface, un troisième bouton **« Choisir un autre moteur… »**
dans la boîte de reprise : repeuple le fichier et les langues comme
Reprendre, mais **laisse le sélecteur de modèle intact**
(`_apply_job_snapshot(snapshot, apply_model=False)`) et ne démarre PAS tout
seul -- l'utilisateur choisit dans le sélecteur existant, puis clique
Traduire lui-même. Ce second clic retombe naturellement sur
`_resolve_conflict`, qui redétecte le même job interrompu et propose sa
propre reprise, cette fois avec le moteur choisi entre-temps. Aucune
UI neuve : entièrement recyclé.

**Piège rencontré en testant, pas en production** : `QMessageBox`
réordonne ses boutons selon leur rôle une fois un troisième bouton
`ActionRole` ajouté -- l'ordre d'AFFICHAGE n'est plus l'ordre d'ajout.
Les tests qui repéraient un bouton par position (`self.buttons()[0]`)
cassaient silencieusement dès qu'un test cliquait le MAUVAIS bouton sans
lever d'erreur. Corrigé en recherchant par texte plutôt que par position,
partout où ce risque existe désormais dans `tests/test_ui.py`.

**Vérifié** (`tests/test_pipeline.py` §12, `tests/test_ui.py` §8c) : un job
interrompu par le premier moteur, repris par un second (faux moteurs
distincts) -- les segments déjà écrits ne sont jamais retouchés, seuls les
segments restants passent par le nouveau moteur, l'état persisté reflète
le nouveau moteur ; côté interface, le bouton ne démarre aucun thread, ne
consomme pas le repère, et laisse un choix de modèle fait AVANT l'offre
intact plutôt que de l'écraser.

## 5 septendecies. Hall d'accueil, écran Outils, annulation du nettoyage (ajouté le 25/08/2026)

Demande explicite de l'utilisateur, dans la continuité des changements
précédents : plutôt qu'un écran unique qui montre tout d'un coup, TRANSLAX
s'ouvre maintenant sur un **hall d'accueil** qui fait choisir le service --
et tout ce qui n'est PAS de la traduction à proprement parler part sur un
écran séparé.

**Trois écrans empilés (`QStackedWidget`, `self.pages`), pas trois
fenêtres** : la fenêtre, sa taille, sa barre de titre personnalisée
restent les mêmes tout du long -- seul le contenu affiché change.
- `PAGE_HUB` (0) : trois boutons -- TRADUIRE DU VOLUME, EXTRAIRE AVEC
  ANALYSE, ANNULER NETTOYAGE. Premier écran vu au lancement.
- `PAGE_TRANSLATE` (1) : **contenu inchangé** de l'écran unique
  précédent (document, langues, modèle, traduction) -- juste déplacé
  dans une page de la pile, avec un bouton « ← Menu » ajouté en haut
  pour revenir au hall. "EXTRAIRE AVEC ANALYSE" mène au même écran, avec
  la case Extraction seulement pré-cochée -- pas un écran séparé à
  maintenir en double.
- `PAGE_TOOLS` (2) : nouvel écran, pour l'instant un seul outil.

**Annuler le nettoyage** (`core/postprocess.py`) : demande explicite de
l'utilisateur, qui voulait pouvoir revenir en arrière si le nettoyage des
titres/traits d'union avait corrigé quelque chose à tort.
- `cleanup_file` sauvegarde maintenant le contenu AVANT nettoyage, à côté
  du fichier (`<nom>.md.avant_nettoyage`) -- une seule fois : une deuxième
  passe de nettoyage (ex. reprise d'un job) n'écrase pas cette sauvegarde
  par une version déjà nettoyée, sinon "Annuler" retrouverait un état
  intermédiaire, pas l'original.
- `undo_cleanup(md_path)` restaure ce contenu et supprime la sauvegarde
  (pas de ré-annulation fantôme possible après coup).
- Pourquoi une sauvegarde et pas une inversion algorithmique : la
  rétrogradation de faux titres serait re-calculable (elle vient d'une
  règle appliquée au texte source), mais le recollement des traits
  d'union ne l'est PAS -- "mot-mot" après coup ne permet pas de savoir si
  c'était déjà collé ou pas avant. Une seule sauvegarde couvre les deux
  cas de façon fiable.
- Écran Outils : un champ de chemin + Parcourir, un message d'état (prêt à
  annuler / rien à annuler), un bouton qui n'est actif que si une
  sauvegarde existe pour CE fichier précis.
- **Case « Nettoyer les titres... » décochée par défaut** (elle l'était
  auparavant) : demande explicite de l'utilisateur, maintenant que
  l'annulation existe, plus la peine d'imposer ce nettoyage par défaut.

**Autres changements de la même série, demandes explicites de
l'utilisateur** :
- **Coins carrés partout** : chaque `border-radius` de `ui/styles.qss`
  ramené à `0px` (douze déclarations) -- aucun changement de code Python,
  purement une feuille de style.
- **Sélecteur de modèle réorganisé** : les moteurs à licence commerciale
  (OPUS-MT, MADLAD-400) affichés en premier, les profils à usage
  personnel uniquement (NLLB ×3 + Turbo, licence CC-BY-NC) rejetés en
  dernier ET colorés en orange (`PERSONAL_USE_COLOR`), via
  `QComboBox.setItemData(..., Qt.ForegroundRole)`.
- **Modal d'avertissement au clic** sur un profil à usage personnel
  (`_on_model_selected`) : rappelle la licence CC-BY-NC et l'interdiction
  d'usage commercial, suggère OPUS-MT/MADLAD-400 à la place. Ne s'affiche
  que sur un vrai clic utilisateur -- jamais lors d'une restauration
  programmatique (reprise d'un job mémorisé), grâce à
  `_suppress_model_notice`/`_set_model_combo`.
- **600M — Turbo présélectionné par défaut à l'écran**
  (`UI_DEFAULT_MODEL_KEY`), DÉLIBÉRÉMENT distinct de
  `translate.DEFAULT_MODEL_KEY` ("600M", inchangé) qui reste le repli de
  `pipeline.Job`/des tests/du CLI -- mélanger les deux aurait cassé la
  quasi-totalité des tests existants, construits autour du repli
  precise/FakeEngine.
- **Fondu simple entre écrans** (`_navigate_to`, 160 ms) : un seul effet
  d'opacité posé sur `self.pages`, animé à zéro puis remis à un pendant
  que la page change en dessous -- volontairement discret, pas un effet
  spectaculaire.

**Piège rencontré en corrigeant les tests après le changement de modèle
par défaut, distinct de celui ci-dessous** : `tests/test_ui.py` patchait
déjà `translate.PreciseEngine` en faux moteur depuis toujours, mais pas
`translate.FastEngine` -- tant que le sélecteur montrait "600M" par
défaut, ça n'avait jamais eu d'importance. Une fois Turbo présélectionné,
toute section qui appelait `_start()` sans choisir un modèle exprès
passait par le VRAI `FastEngine` (donc une vraie conversion CTranslate2
dans un dossier de réglages isolé, très lente) au lieu du faux moteur
instantané -- corrigé en patchant aussi `FastEngine`, ET en fixant le
modèle de la fenêtre principale du test sur "600M" juste après sa
construction (`_set_model_combo`, qui n'ouvre pas l'avertissement
ci-dessus).

**Piège rencontré en testant, à corriger dans le code lui-même, pas
seulement dans les tests** : les méthodes statiques
`QMessageBox.information()`/`.warning()` (utilisées pour la confirmation
de l'outil d'annulation) **ne passent PAS par le même `.exec()`** que la
convention déjà établie partout ailleurs dans ce fichier (une instance
explicite `QMessageBox(self)` puis `.exec()`) -- un mock Python de
`QMessageBox.exec` (utilisé par tous les tests de boîtes de dialogue de
`tests/test_ui.py`) ne les intercepte pas, ce qui a fait tourner un test
en boucle infinie en mode offscreen. Corrigé en remplaçant ces deux
raccourcis par une instance explicite, cohérent avec le reste du fichier
-- pas seulement un correctif de test, une vraie incohérence de style
révélée par le test.

## 5 duodevicies. Écran de démarrage — splash screen (ajouté le 25/08/2026)

Demande explicite de l'utilisateur : une petite zone rectangulaire, logo
et nom du logiciel centrés, visible pendant le chargement, avec le nom de
l'éditeur (« AJTWS — Amilcar Joao »).

`main.py::_build_splash_pixmap` dessine ce rectangle à la volée avec
`QPainter` (pas un fichier image séparé à maintenir) : logo (`ui/icon.ico`),
« TRANSLAX », « Chargement… », éditeur -- couleurs reprises telles quelles
de `ui/styles.qss` pour que ce tout premier écran ait déjà le thème de
l'appli, avant même que la feuille de style ne soit chargée. `QSplashScreen`
standard de Qt, affiché en tout premier dans `main()` (avant la feuille de
style et la construction de `MainWindow`), refermé via `splash.finish(window)`
une fois la vraie fenêtre affichée.

**Limite honnête, pas contournable par ce changement** : dans le `.exe`
empaqueté (`--onefile`), le plus gros du délai avant que quoi que ce soit
n'apparaisse vient de l'auto-extraction du bootloader PyInstaller --
constaté plusieurs fois cette session, 10 à 20+ secondes selon la charge
de la machine -- qui a lieu AVANT que le moindre code Python (donc ce
splash) ne s'exécute. Ce changement couvre le chargement côté Python
(import de `ui.main_window`, construction de `MainWindow`) qui reste
rapide même en `.exe` (quelques dizaines de ms, mesuré) -- pas
l'extraction elle-même. En lançant depuis les sources (`python main.py`),
où il n'y a pas de bootloader à extraire, le splash n'est visible que
très brièvement pour la même raison.

**Vérifié réellement** : pixmap rendu et sauvegardé en PNG pour inspection
visuelle directe (pas juste relu dans le code) ; lancement réel de
`python main.py`, processus sain (empreinte mémoire normale, pas de plantage).

## 5 undevicies. Boutons de même hauteur et modal des clés API (ajouté le 25/08/2026)

Deux demandes explicites de l'utilisateur, à partir d'une capture d'écran
montrant Traduire/Traduire X/Stop/Reboost visiblement désynchronisés en
hauteur.

**Hauteur des 4 boutons** : `setFixedHeight(self.translate_button.sizeHint().height())`
appliqué aux quatre après leur création, plutôt que d'harmoniser le padding
des trois styles QSS différents (`primary`, `danger`, par défaut) qui
produisaient des hauteurs naturelles différentes (38/31/40/31 px mesurés) --
garantit un résultat pixel identique, quel que soit le style de chacun.

**Modal des clés API** (`ApiKeysDialog`) : le champ « Clé API Anthropic »
de la page principale n'est plus modifiable en place -- `setReadOnly(True)`
+ un clic (`mousePressEvent` assigné directement, pas de signal Qt standard
pour ce genre de champ) ouvre une fenêtre avec **trois** champs : Anthropic
(seule utilisée aujourd'hui, par Traduire X), **xAI (Grok)** et **OpenAI
(ChatGPT)** -- ces deux dernières préparées pour de futures intégrations,
demande explicite de l'utilisateur, stockées (`core/settings.py::get/set_xai_api_key`,
`get/set_openai_api_key`, même mécanisme que la clé Anthropic) mais
branchées à AUCUNE fonctionnalité pour l'instant. Cliquer sur Traduire X
sans clé enregistrée ouvre directement cette fenêtre après l'avertissement,
plutôt que de simplement donner le focus à un champ qui ne peut de toute
façon plus être tapé directement.

**Piège rencontré en testant, propre à ce changement précis** : `_start_vision`
ouvre maintenant potentiellement DEUX boîtes de dialogue en cascade quand
aucune clé n'est enregistrée (la `QMessageBox.warning` existante, puis
`ApiKeysDialog`) -- un test qui ne mockait que la première restait bloqué
sur la seconde en mode offscreen. Corrigé en neutralisant les deux
séparément (même principe déjà établi pour `VisionReviewDialog`).

**Erreur commise et corrigée pendant la vérification manuelle, à ne pas
reproduire** : un script de test ad hoc n'isolant pas `settings._settings_dir`
(contrairement à tous les fichiers de `tests/`) a écrit une fausse clé de
test PAR-DESSUS la vraie clé Anthropic de l'utilisateur dans le vrai
`%APPDATA%\TRANSLAX\settings.json` de cette machine. Repéré immédiatement,
la vraie clé (déjà vue en clair plus tôt dans cette même session, jamais
autrement répétée) a été réécrite pour la restaurer exactement, et les
clés xAI/OpenAI de test effacées. Confirme la même leçon que le reste de
cette session : **toujours isoler `settings._settings_dir` avant tout
script touchant aux réglages, même un test jetable, jamais seulement dans
les fichiers de `tests/`**.

**Vérifié** (`tests/test_ui.py`, section 12) : les 4 boutons ont
rigoureusement la même hauteur ; le champ de clé API n'est plus
modifiable directement ; un clic (simulé) qui accepte le modal enregistre
bien les trois clés et met à jour le champ affiché ; un clic qui annule
ne change rien.

## 5 vicies. OCR local (PaddleOCR) — Traduire X sans dépendance payante (ajouté le 25/08/2026)

Demande explicite de l'utilisateur, dans la continuité d'une exploration
faite en parallèle (voir `OCR_VLM_COMPARATIF.md`, `ocr_prototype.py`) :
supprimer la dépendance à l'API Anthropic payante pour « Traduire X », afin
que TRANSLAX reste réellement 100 % local de bout en bout. PaddleOCR
(licence Apache 2.0) devient le moteur **par défaut**, Anthropic reste
disponible en option pour les cas difficiles.

**Découvert en reprenant cette tâche, pas supposé** : l'exploration
précédente avait installé et validé PaddleOCR en STANDALONE
(`ocr_prototype.py`, à la racine du projet) mais ne l'avait jamais branché
dans `core/vision_ocr.py` -- vérifié par une recherche dans `core/`, `ui/`
et `requirements.txt` avant de commencer, aucune occurrence. Cette section
documente le VRAI branchement.

**`core/vision_ocr.py::extract_text_paddleocr`** -- même signature de
retour que `extract_text_vision` (`PageResult`/`VisionOcrReport`), même
mécanisme de reprise par cache disque : le reste du pipeline (récapitulatif,
segmentation) ne sait pas lequel des deux a tourné.
- **Coût nul** : `input_tokens`/`output_tokens` toujours à 0.
- **`flagged`** : True si au moins une ligne détectée a une confiance sous
  `PADDLEOCR_CONFIDENCE_THRESHOLD` (0,75) -- même sémantique que les
  balises `<uncertain>` du chemin Anthropic (le texte reste inclus, juste
  signalé). Seuil choisi sur un cas réel : corps de texte lisible à
  0,98-1,00, fragment d'en-tête tourné/minuscule à 0,47-0,61 (voir plus
  bas) -- 0,75 sépare proprement les deux.
- **`vision_failed`** toujours False : PaddleOCR ne refuse jamais de
  transcrire une page (pas de filtre de contenu comme Anthropic).
- **Pas de reconstruction de paragraphes** ligne par ligne (indentation,
  espacement vertical) : `core/segment.py::detect_strategy` bascule déjà
  en stratégie « flux » pour un texte sans repère fiable de paragraphe --
  le même mécanisme déjà validé pour les .txt sans ligne vide et les EPUB,
  réutilisé tel quel plutôt que réinventé. Seul traitement propre à l'OCR :
  recoller un mot coupé par un trait d'union en fin de ligne
  (`_join_ocr_lines`) -- « Appro- » + « priate » -> « Appropriate ».
- **Table de langues** (`core/languages.py::FLORES_TO_PADDLEOCR`) : codes
  PaddleOCR vérifiés un par un dans le code source réellement installé
  (`paddleocr/_utils/langs.py`, `_pipelines/ocr.py`), pas devinés -- « ch »
  pas « zh » pour le chinois, « japan » pas « ja », « korean » pas « ko ».
  16 des 18 langues de TRANSLAX couvertes ; lingala et wolof absents
  (aucun modèle PaddleOCR connu), `VisionOcrError` explicite dans ce cas
  plutôt qu'une correspondance inventée.
- **Moteur mis en cache par langue** (`_paddleocr_engines`), chargé
  PARESSEUSEMENT au tout premier vrai besoin (pas avant la boucle sur les
  pages) : un job entièrement repris depuis le cache ne paie pas le
  chargement du modèle. **Bug réel trouvé par un test, pas par relecture** :
  la première version chargeait le moteur avant même de vérifier le cache
  -- corrigé avant que ce comportement n'atteigne un vrai usage.

**Piège hérité de l'exploration précédente, reconfirmé ici** :
`paddlepaddle==3.3.1` (moteur "PIR") plante à l'inférence CPU avec oneDNN
activé par défaut -- `enable_mkldnn=False` obligatoire au constructeur
`PaddleOCR(...)`.

**Validé sur un cas réel, pas juste sur un texte propre généré exprès**
(avant d'écrire `extract_text_paddleocr`) : sur une page de « Hindu
Magical Occultism Test.pdf » dont le calque de texte du PDF contenait du
bruit de caractères (« MAGIOAL », « titis », « tbat », « wiU »), PaddleOCR
a lu le corps du texte à 0,98-1,00 de confiance -- nettement plus fiable
que le calque d'origine. Un fragment d'en-tête tourné/minuscule a été
détecté avec une confiance basse (0,47-0,61), correctement signalé plutôt
que présenté comme fiable à tort.

**Interface** : case à cocher « Utiliser Claude (Anthropic) au lieu de
l'OCR local — payant, nécessite une clé API », décochée par défaut. Traduire
X ne bloque plus sur l'absence de clé API tant que cette case reste
décochée -- la clé n'est exigée QUE si elle est cochée (comportement
Anthropic inchangé dans ce cas précis). `pipeline.Job.vision_provider`
("paddleocr" par défaut, "anthropic" sinon) porte ce choix jusqu'à
`run_job`, qui dispatche vers la bonne fonction d'extraction.

**Vérifié** (`tests/test_vision_ocr.py` §8-9, `tests/test_ui.py` §9c) :
table de langues, recollement des traits d'union coupés, langue sans
correspondance -> erreur claire ; UN vrai appel PaddleOCR (gratuit, donc
sans le compromis coût/réseau d'Anthropic) sur un vrai document, avec
reprise depuis le cache confirmée sans rappel au moteur ; côté interface,
Traduire X démarre bien sans clé API quand la case Anthropic n'est pas
cochée, et appelle réellement `extract_text_paddleocr` (pas Anthropic).

## 5 vicies unus. Export PDF (ajouté le 25/08/2026)

Demande explicite de l'utilisateur : pouvoir choisir PDF comme format de
sortie -- texte noir sur blanc, une mise en forme réelle, jamais les codes
Markdown bruts ("#", "##", "-", ">") visibles dans le résultat.

**Le .md reste le format par défaut et le SEUL utilisé pour l'état/la
reprise** (voir `core/state.py`, `core/pipeline.py`) : choisir PDF ajoute
un export à la toute fin, il ne remplace jamais le .md. Choix délibéré,
pas une demi-mesure -- toute la mécanique de reprise déjà validée
(écriture incrémentale, cache, pointeur de nom traduit) continue de
fonctionner exactement pareil, sans le moindre risque d'y toucher pour
cette fonctionnalité.

**`core/pdf_export.py`** -- nouveau module, rendu via `pymupdf.Story`
(HTML -> mise en page -> PDF), déjà une dépendance du projet : aucune
bibliothèque supplémentaire nécessaire.
- `markdown_to_html_body` est l'**inverse exact** de
  `core/translate.py::render_markdown` -- pas un analyseur Markdown
  général : TRANSLAX ne produit jamais que quatre formes de ligne (titre,
  sous-titre, puce, citation) plus des paragraphes ordinaires, donc pas
  besoin d'un vrai analyseur pour les reconnaître fidèlement. Les
  caractères spéciaux HTML sont échappés (jamais interprétés comme du
  HTML si le texte traduit contient littéralement `<`/`>`/`&`).
- CSS explicite : noir sur blanc, jamais hérité d'un thème -- ce PDF est
  fait pour être lu/imprimé comme un document classique, indépendamment
  du thème sombre de l'application elle-même.
- Pagination automatique gérée par `pymupdf.Story` (boucle
  `begin_page`/`story.place`/`story.draw`/`end_page` jusqu'à ce que
  `place()` indique qu'il ne reste plus rien à placer) -- aucune limite de
  pages codée en dur, un livre entier tient sur autant de pages que
  nécessaire.
- Une erreur de rendu PDF ne fait JAMAIS échouer tout le job : le .md,
  déjà écrit et valide, reste le résultat utilisable, avec une note claire
  dans le journal plutôt qu'un plantage pour un format d'export secondaire.

**Interface** : nouveau sélecteur « Format de sortie » (Markdown / PDF),
Markdown par défaut. Le format choisi est mémorisé dans le repère de
reprise (voir §5 nonies) -- un job repris après interruption produit
toujours le format demandé à l'origine. Une fois le PDF généré, il devient
le fichier « principal » pour Ouvrir/Ouvrir le dossier (c'est le format
explicitement demandé) ; le .md reste accessible juste à côté.

**Vérifié réellement avant d'écrire le module** (pas supposé) : rendu d'un
document de test avec les quatre types de bloc, PDF ouvert et capturé en
image pour inspection visuelle directe -- titres en gras à la bonne
taille, paragraphes justifiés, puces indentées, citation visuellement
distincte (bordure + texte grisé), tout en noir sur fond blanc, aucun
symbole Markdown brut visible nulle part.

**Vérifié** (`tests/test_pdf_export.py`, `tests/test_pipeline.py` §13-14) :
conversion ligne par ligne pour les quatre types de bloc, échappement HTML,
un vrai document PDF produit et relu avec `pymupdf` pour confirmer que le
texte réellement traduit (pas juste le .md) y figure, et qu'AUCUNE ligne
du texte extrait du PDF ne commence par un symbole Markdown brut ; un job
sans `output_format="pdf"` ne produit toujours aucun fichier .pdf
(comportement par défaut inchangé).

## 5 vicies duo. Page Paramètres et diagnostic matériel (ajouté le 25/08/2026)

Demande explicite de l'utilisateur : un quatrième écran (`PAGE_SETTINGS`),
pensé pour accueillir plusieurs réglages au fil du temps -- pour l'instant,
un seul : savoir si TRANSLAX exploite réellement la puissance de la
machine (CPU, GPU, RTX Nvidia...), pas une estimation ni une promesse.

**Navigation** : un lien discret « ⚙ Paramètres » sous les trois boutons
du hall d'accueil -- volontairement PAS un quatrième "service" au même
niveau que Traduire/Extraire/Annuler nettoyage (ce n'est pas une action à
lancer, juste un endroit à consulter).

**`core/system_info.py::detect()`** -- nouveau module, appelé à la demande
(jamais mis en cache, jamais calculé au démarrage) :
- Nom du CPU : sur Windows, via PowerShell/WMI
  (`Get-CimInstance Win32_Processor`) pour un nom lisible -- vérifié
  réellement sur la machine de référence de ce projet : donne bien
  « AMD Ryzen 7 4700U with Radeon Graphics », là où `platform.processor()`
  seul n'aurait donné qu'une chaîne technique peu lisible. Repli sur
  `platform.processor()` si PowerShell est injoignable, jamais bloquant.
- GPU : `torch.cuda.is_available()` -- pas "un GPU existe-t-il", mais "PyTorch
  peut-il vraiment s'en servir", ce qui est la question qui compte
  réellement pour cette appli.
- **Nuance réelle, pas glissée sous le tapis** : les quatre moteurs de
  TRANSLAX ne se comportent PAS tous pareil vis-à-vis d'un GPU disponible
  (relu dans `core/translate.py` avant d'écrire ce module, pas supposé) :
  Précis/OPUS-MT/MADLAD-400 (tous basés sur `transformers`) utilisent un
  GPU CUDA automatiquement s'il est détecté ; **Turbo (CTranslate2) tourne
  TOUJOURS en CPU**, même si un GPU est disponible -- ce moteur a été
  conçu et validé pour l'accélération CPU par quantification (voir §5
  terdecies), pas pour CUDA. La page l'affiche explicitement plutôt que de
  laisser croire qu'un GPU accélérerait tous les profils.
- Aucune exception ne remonte jamais jusqu'à l'interface : toute détection
  impossible (PyTorch absent, PowerShell injoignable...) est notée dans
  `detection_notes` et affichée telle quelle, sans jamais faire planter la
  page Paramètres.

**Vérifié réellement sur la machine de référence de ce projet** (AMD
Ryzen 7 4700U, 8 cœurs, pas de GPU dédié, déjà confirmée par ailleurs) :
`detect()` renvoie bien `gpu_available=False`, `cpu_name` contient
« Ryzen 7 4700U », le device retenu pour Précis/OPUS-MT/MADLAD-400 est
bien "cpu" -- pas une donnée inventée, la vraie sortie de la vraie fonction
sur la vraie machine.

**Vérifié** (`tests/test_system_info.py`, `tests/test_ui.py` §13) :
détection réelle sur cette machine (cohérente avec le matériel déjà
documenté ailleurs dans SPEC.md) ; absence de PyTorch simulée par un vrai
blocage d'import (pas juste un mock de façade) sans faire planter
`detect()` ; côté interface, un vrai clic sur le lien Paramètres du hall
d'accueil mène bien à l'écran, dont le texte affiché reprend exactement ce
que `detect()` a réellement renvoyé.

**Non fait ici, volontairement** : la page ne détecte pour l'instant que
CPU/GPU (ce qui a été explicitement demandé) -- RAM, espace disque du
cache des modèles, etc. pourraient s'ajouter plus tard sur le même
principe (une nouvelle carte sur cette même page), sans restructuration.

## 5 vicies tres. Inversion rapide langue source / langue cible (ajouté le 25/08/2026)

Demande explicite de l'utilisateur : « entre les langues met une double
flèche qui me permet d'inverser la langue source de la langue de sortie ;
un switch qui s'effectue correctement (si on veut aller vite) ». La flèche
simple (`→`), jusque-là purement décorative entre les deux menus
déroulants, est remplacée par un bouton cliquable affichant une flèche
double (`⇄`), objectName `swapLangButton`.

**`_swap_languages()`** (`ui/main_window.py`) : lit `currentData()` des
deux menus (les codes FLORES-200, pas leur position), puis replace chacun
via `findData()` sur le code de l'autre. Raisonner en codes plutôt qu'en
index a un but précis : les deux menus sont aujourd'hui peuplés à
l'identique (même boucle, même ordre -- voir `_build_ui`), donc les index
coïncideraient déjà, mais un swap par code reste correct même si ça
changeait, et ne plante jamais si un code venait à manquer côté cible
(`findData` renvoie alors -1, silencieusement ignoré plutôt que de
sélectionner le mauvais index par accident).

Comme les deux menus ont déjà leur `currentIndexChanged` branché sur
`_update_model_info` (nécessaire pour OPUS-MT, dont le repo_id dépend de
la paire de langues -- voir §5 quaterdecies), un swap déclenche
naturellement la mise à jour de l'info modèle affichée, sans câblage
supplémentaire.

**Mode extraction seule** : la langue cible n'a aucun sens dans ce mode
(voir `_on_extract_only_toggled`, §5 septendecies) -- `swap_lang_button`
suit désormais exactement le même sort que `tgt_combo`/`tgt_label`
(désactivé/réactivé au même endroit), pour ne jamais laisser un bouton
actif échanger vers une langue cible qui n'a plus d'utilité.

**Vérifié** (`tests/test_ui.py` §14, réel -- pas de mock) : un clic échange
bien les deux langues (pas juste visuellement -- `currentData()` des deux
combos vérifié après coup) ; un second clic restaure exactement l'état de
départ (aller-retour) ; le bouton se désactive/réactive en même temps que
la langue cible en mode extraction seule.

## 5 vicies quater. Correctif build : fichiers de configuration PaddleOCR manquants dans l'exe gelé (corrigé le 25/08/2026)

Bug réel signalé par l'utilisateur (capture d'écran d'une vraie tentative
d'extraction, pas un cas de test) : lancer « Extraire X » depuis l'exe
construit plantait systématiquement avec `Exception : The pipeline (OCR)
does not exist! Please use a pipeline name or a config file path!` --
alors que ce même chemin de code (`core/vision_ocr.py::extract_text_paddleocr`)
est validé réellement par `tests/test_vision_ocr.py` depuis l'intégration
de PaddleOCR (§5 vicies) et n'avait jamais échoué en test.

**Cause racine, retrouvée dans le code source réellement installé de
`paddlex`** (`paddlex/inference/pipelines/__init__.py::get_pipeline_path`) :
PaddleOCR ne charge pas ses pipelines depuis du code Python, mais depuis
des fichiers `.yaml` livrés comme simples fichiers à l'intérieur du
paquet (`paddlex/configs/pipelines/OCR.yaml`, et d'autres fichiers
`.yaml` référencés en cascade, ex. `doc_preprocessor.yaml` pour le
sous-pipeline de prétraitement). `PyInstaller.Analysis` ne suit que le
graphe d'imports Python -- un fichier `.yaml` qui n'est jamais importé
n'est jamais copié dans l'exe gelé, sauf déclaration explicite dans
`datas=[...]`. `TRANSLAX.spec` n'en déclarait aucun pour `paddlex`/
`paddleocr`. Résultat : l'exe gelé démarre normalement, importe `paddle`/
`paddlex` sans erreur (ce sont les `.pyd`/`.dll` qui sont bien suivis
automatiquement), mais plante uniquement au moment précis où PaddleOCR
cherche son fichier de config `OCR.yaml` sur disque -- absent de l'arborescence
extraite (`_MEIxxxxxx/paddlex/configs/pipelines/`), d'où l'exception.
Ce bug ne pouvait pas apparaître dans `tests/test_vision_ocr.py`, qui
tourne sur l'interpréteur réel (fichiers `.yaml` intacts sur disque), et
n'avait donc aucune chance d'être détecté avant un usage réel de l'exe.

**Correctif** (`TRANSLAX.spec`) : ajout de
`collect_data_files('paddlex')` et `collect_data_files('paddleocr')`
(utilitaire standard de `PyInstaller.utils.hooks`) à `datas=[...]` --
copie tous les fichiers non-Python du paquet (335 `.yaml` de config, plus
quelques `.jinja`/`.html`/`.json` annexes) en conservant leur chemin
relatif interne au paquet, exactement ce que `get_pipeline_path` attend
pour les retrouver une fois gelé.

**Vérifié réellement, pas supposé** : après reconstruction, lancement de
l'exe et inspection directe du dossier d'extraction onefile
(`%TEMP%\_MEIxxxxxx\paddlex\configs\pipelines\`) -- présence confirmée de
`OCR.yaml`, `doc_preprocessor.yaml`, et des 38 fichiers `.yaml` de
pipelines au total (contre 0 avant ce correctif).

**Non retesté par ce correctif** : la logique d'OCR elle-même (extraction
réelle de texte, seuils de confiance, dé-hyphénation) est inchangée --
déjà validée par `tests/test_vision_ocr.py` §9 sur interpréteur réel ; ce
correctif ne touche que l'empaquetage (`TRANSLAX.spec`), aucun fichier
`core/`.

## 5 vicies quinquies. Page Paramètres : à propos, clés API, gestion du cache OCR (ajouté le 26/08/2026)

Demande explicite de l'utilisateur, en trois volets, après avoir remarqué
que le pied de page affichait encore « v1.14.0 » alors que le correctif
précédent (§5 vicies quater) avait déjà porté le numéro à 1.14.1 :

**Affichage de version périmé** : pas un bug de code -- `core/version.py`
contenait bien `VERSION = "1.14.1"`. La reconstruction qui a suivi le
correctif PaddleOCR avait été lancée AVANT le passage 1.14.0 → 1.14.1
(erreur d'ordre des opérations de ma part), donc l'exe distribué à ce
moment-là avait figé l'ancien numéro dans son pied de page
(`version.version_string()`, affiché une fois à la construction de la
fenêtre). Pas de correctif de code nécessaire ici -- juste refaire le
build une fois le numéro correct en place, cette fois dans le bon ordre
(bump avant build, plus jamais après).

**Carte « À propos de TRANSLAX »** : `version.version_string()` (numéro +
date de build réels), nom de l'éditeur (AJTWS — Amilcar Joao), et un
court résumé du « pourquoi » de l'application -- traduire des documents
volumineux localement, sans dépendre d'un service payant, plusieurs
moteurs adaptés à l'usage (commercial ou strictement personnel).

**Carte « Clés API »** : un bouton « Gérer les clés API… » qui ouvre
`ApiKeysDialog` -- exactement le même modal que celui déjà relié au champ
(en lecture seule) de la page Traduire (voir §5 undevicies), pas une copie
divergente. Logique : gérer les clés est un réglage, ça a naturellement
sa place dans Paramètres en plus de son accès existant.

**Carte « Fichiers temporaires et cache OCR »**, réponse à « permet moi de
gérer les paths en lien avec le ocr json » : le cache OCR de Traduire X
n'est pas un fichier JSON isolé et déplaçable au sens strict -- c'est un
dossier caché `.translax` (voir `core/state.py::work_dir`, `core/pipeline.py::vision_cache`)
créé À CÔTÉ de chaque fichier de sortie, contenant l'état de reprise ET
le cache OCR (`*.vision_cache.jsonl`). Le relocaliser globalement
casserait la logique de reprise (qui cherche spécifiquement `.translax`
à côté de CHAQUE fichier de sortie individuellement) -- « gérer ces
chemins » a donc été interprété comme : les rendre visibles (jusque-là
un dossier caché dont l'existence même n'était pas montrée dans
l'interface), savoir combien de place ils prennent, et pouvoir les vider
une fois les traductions terminées. Nouveau module `core/cache_maintenance.py` :
- `find_cache_dirs(root)` : parcourt récursivement `root` à la recherche
  de dossiers `.translax`, calcule leur taille réelle sur disque.
- `clear_cache_dirs(dirs)` : supprime chaque dossier, ne s'arrête jamais
  au premier échec (fichier verrouillé par un job en cours...) -- renvoie
  le compte réellement supprimé ET la liste des erreurs, jamais un simple
  succès/échec global qui cacherait un cas partiel.

Interface : « Analyser le dossier de sortie par défaut » (ou « Choisir un
autre dossier… »), puis « Vider les caches trouvés » (bouton rouge,
`objectName="danger"`) avec confirmation explicite avant suppression --
la confirmation repère le bouton cliqué par IDENTITÉ (`clickedButton() is
confirm_btn`), pas par position, pour ne jamais retomber dans l'incident
de réordonnancement des boutons `QMessageBox` déjà rencontré et documenté
plus haut dans ce document.

**Vérifié réellement** (`tests/test_cache_maintenance.py`, vrais dossiers/
fichiers sur disque -- imbriqués, absents, ou déjà supprimés entre-temps ;
`tests/test_ui.py` §15, deux vrais dossiers `.translax` préparés, analysés
puis effectivement supprimés du disque via les vrais boutons de
l'interface).

## 5 vicies sexies. Deuxième correctif build PaddleOCR : métadonnées de dépendances manquantes (corrigé le 26/08/2026)

Deuxième bug réel signalé par l'utilisateur sur l'exe reconstruit après le
correctif précédent (§5 vicies quater, qui réglait bien un problème
différent -- les fichiers `.yaml` de pipeline) : « Extraire X » plantait
maintenant avec `RuntimeError : A dependency error occurred during
pipeline creation. Please refer to the installation documentation...` --
un message générique qui masque la vraie cause (`paddleocr/_pipelines/base.py`
attrape la vraie exception `DependencyError` et la remplace par ce texte
fixe, sans jamais dire QUELLE dépendance pose problème).

**Cause racine, retrouvée en lisant `paddlex/utils/deps.py`** : `paddlex`
ne vérifie pas ses propres dépendances en les import ant, mais en
interrogeant leurs métadonnées d'installation via `importlib.metadata`
(`is_dep_available` → `importlib.metadata.version(nom_du_paquet)`). Un
paquet dont le CODE est bien présent mais dont le dossier `.dist-info`
n'est pas gelé dans l'exe est donc vu comme « absent » par cette
vérification, même s'il fonctionne parfaitement. `PyInstaller.Analysis`
ne peut pas deviner ce genre de vérification par chaîne de caractères --
il ne suit que les imports Python réels -- donc ces dossiers `.dist-info`
ne sont jamais copiés automatiquement. Le pipeline OCR de PaddleX exige
l'extra `"ocr"` (avec repli sur `"ocr-core"` si l'extra complète n'est
pas disponible, voir `@pipeline_requires_extra("ocr", alt="ocr-core")`) ;
sur cette machine, `"ocr"` n'est de toute façon jamais complètement
disponible même en développement normal (scipy/scikit-learn/lxml...
utiles uniquement à des pipelines que TRANSLAX n'utilise jamais, comme
PP-StructureV3), et c'est le repli `"ocr-core"` qui satisfait réellement
l'exigence -- interrogé directement sur `paddlex.utils.deps.EXTRAS`
plutôt que deviné : `imagesize`, `opencv-contrib-python`, `pyclipper`,
`pypdfium2`, `python-bidi`, `shapely`.

**Correctif** (`TRANSLAX.spec`) : ajout de `copy_metadata(nom)` (utilitaire
standard de `PyInstaller.utils.hooks`) pour `paddlex` et ces 6 paquets
précis -- copie leurs dossiers `.dist-info` dans l'exe gelé, exactement
ce qu'`importlib.metadata` a besoin de retrouver.

**Vérifié réellement AVANT de reconstruire** (pour éviter un troisième
cycle de build de 10 minutes sur une hypothèse non testée) : simulation
fidèle de la visibilité des métadonnées telle que l'exe gelé la verrait --
`importlib.metadata.version` intercepté pour ne « voir » que les 7 noms
du correctif, caches `@lru_cache` de `paddlex.utils.deps` vidés, puis
appel réel à `create_pipeline(pipeline="OCR")` (le point d'échec EXACT
du message d'erreur de l'utilisateur) : pipeline créé avec succès dans
ces conditions contraintes, alors qu'il échouait avant le correctif dans
les mêmes conditions. Puis reconstruction réelle de l'exe et nouveau
smoke test de lancement.

**Deux correctifs de packaging PaddleOCR coup sur coup (§5 vicies quater
et celui-ci)** : pas le signe d'un problème plus profond nécessitant de
« tout réorganiser » -- ce sont les deux pièges classiques et bien connus
de PyInstaller face à des paquets qui font de la découverte dynamique
(fichiers de config non importés, dépendances vérifiées par métadonnées
plutôt que par import). Les deux catégories les plus courantes sont
maintenant couvertes ; un troisième cas isolé resterait possible vu la
taille de l'arbre de dépendances de `paddlex` (plus de 70 dépendances
optionnelles au total, TRANSLAX n'en utilisant qu'une poignée), mais se
traiterait de la même façon, au cas par cas, avec la même rigueur.

## 5 vicies septies. Troisième correctif build PaddleOCR : DLL natives de Paddle absentes (corrigé le 26/08/2026)

Troisième bug réel signalé par l'utilisateur, un de plus dans la même
veine que les deux précédents mais d'une nature différente : `RuntimeError :
(PreconditionNotMet) The third-party dynamic library (mklml.dll) that
Paddle depends on is not configured correctly. (error code is 126)`
(code 126 Windows = « module introuvable »).

**Cause racine, retrouvée dans `paddle/__init__.py`** : Paddle ne lie pas
`mklml.dll`/`mkldnn.dll`/`libiomp5md.dll` (son moteur MKL/oneDNN pour
l'inférence CPU) au niveau du code Python importé -- il les charge lui-même
dynamiquement au démarrage via `ctypes.WinDLL`/`LoadLibraryExW`, avec un
chemin calculé à la main : `th_dll_path = os.path.dirname(__file__) + "/libs"`,
puis un `glob.glob(...*.dll)` dans ce dossier précis. `PyInstaller.Analysis`
suit les tables d'imports PE des extensions natives pour détecter
automatiquement les DLL dont elles dépendent -- mais ici, rien ne référence
`mklml.dll` de cette façon (chargement 100% dynamique, jamais un lien
statique), donc PyInstaller ne les a JAMAIS détectées ni incluses, dans
aucun des deux builds précédents (vérifié : absentes des journaux de
construction). Vérifié aussi ce que ce troisième message d'erreur ne
voulait PAS dire : que le fichier serait totalement absent -- en réalité
c'est le message générique de Windows pour « une DLL demandée par le
chargeur natif de Paddle est introuvable au chemin qu'il calcule »,
confirmé en lisant le code source de Paddle plutôt que deviné depuis le
message seul.

**Correctif** (`TRANSLAX.spec`) : `collect_dynamic_libs('paddle')` (le
même utilitaire que `collect_data_files`/`copy_metadata`, mais pour des
bibliothèques natives) passé à `binaries=[]` -- copie les 12 `.dll` de
`paddle/libs/` (~188 Mo au total, `mklml.dll` et `mkldnn.dll` en étant
l'essentiel, taille réelle et incompressible du moteur d'inférence CPU de
Paddle) en conservant EXACTEMENT le sous-chemin relatif `paddle/libs/`
que le code de Paddle calcule lui-même -- contrairement à la détection
binaire automatique de PyInstaller, qui aurait pu les aplatir à la racine
du paquet si elle les avait détectées (ce qu'elle ne fait de toute façon
pas ici, le chargement étant entièrement dynamique).

**Trois correctifs de packaging PaddleOCR/Paddle coup sur coup (§5 vicies
quater, §5 vicies sexies et celui-ci)**, chacun d'une NATURE DIFFÉRENTE (fichiers de config non importés, dépendances vérifiées
par métadonnées, DLL natives chargées dynamiquement) : les trois pièges
les plus classiques et documentés de PyInstaller face à un paquet natif
complexe qui fait de la découverte dynamique à l'exécution plutôt que des
imports Python statiques. Les trois catégories connues sont maintenant
couvertes ; un quatrième cas resterait possible en théorie mais
improbable -- il n'existe pas de quatrième catégorie « classique »
au-delà de ces trois-là dans l'écosystème PyInstaller.

## 5 vicies octies. Pause/Stop séparés, liste complète des reprises, Paramètres non intrusif (ajouté le 26/08/2026)

Trois demandes explicites de l'utilisateur en une seule fois.

**Pause (vert) et Stop (rouge), plus un seul bouton** : l'ancien bouton
Stop unique interrompait déjà après le segment en cours en laissant tout
ce qu'il faut pour reprendre (voir `core/state.py`) -- exactement ce que
**Pause** (`⏸ Pause`, objectName `pauseButton`, palette verte) fait
maintenant, sans aucun changement de comportement. **Stop**
(`⏹ Stop`, toujours `objectName="danger"`) est un comportement NOUVEAU :
interrompt ET abandonne DÉFINITIVEMENT ce job -- confirmation demandée
avant (bouton reconnu par identité, `clickedButton() is confirm_btn`, pas
par position -- même précaution que partout ailleurs dans ce document
face au réordonnancement des boutons `QMessageBox`). Les deux passent par
le même `worker.request_stop()` sous-jacent ; seul un drapeau
(`MainWindow._abandon_requested`) distingue, dans `_on_finished`, s'il
faut en plus effacer l'état de reprise. Largeur : cinq boutons se
partagent maintenant la même ligne (Traduire/Traduire X gardent un poids
double, Pause/Stop/Reboost un poids simple) -- réduit légèrement
Traduire/Traduire X, explicitement accepté par l'utilisateur.

**`core/state.py::abandon(out_path, source_path=None)`** : efface l'état
de reprise d'UN job précis (progression, segments, cache OCR) -- jamais
le fichier de sortie. **Piège réel rencontré en écrivant les tests, pas
une précaution théorique** : `.translax/` est un dossier PARTAGÉ par tous
les jobs d'un même dossier de sortie (voir `work_dir`, basé sur le
DOSSIER, pas le fichier) -- une première version faisait
`shutil.rmtree(work_dir(out_path))`, ce qui effaçait purement et
simplement l'état de reprise de TOUS les autres jobs voisins du même
dossier, pas seulement celui qu'on voulait abandonner. Corrigé : les
fichiers sont effacés un par un, identifiés par le préfixe de leur nom
(`{stem}.`), le dossier partagé n'étant retiré que s'il devient vide.
Vérifié réellement par un test dédié (`tests/test_pipeline.py` §5 ter) :
deux jobs voisins dans le même dossier, l'un abandonné, l'autre reste
intact et reprenable.

**Liste complète des traductions en attente, pas seulement la dernière**
(`core/settings.py::get_pending_jobs`/`add_pending_job`/`remove_pending_job`,
remplace `get_last_job`/`set_last_job`) : indexée par `output_path`, pas
un singleton. `ui/main_window.py::ResumeJobsDialog` liste chaque job
encore réellement reprenable (revalidé via `state.can_resume`, comme
avant), avec par ligne : « Reprendre » (moteur d'origine), « Autre
moteur… » (reprend la fonctionnalité de §5 sexdecies, préservée), et
« Abandonner » -- qui agit IMMÉDIATEMENT (pas seulement à la fermeture du
dialogue : ces jobs ne tournent pas encore, effacer leur état tout de
suite est sans risque, contrairement au Stop rouge de la page Traduire
qui doit attendre l'arrêt réel du thread). « Plus tard » (ou fermer la
fenêtre) laisse tout en l'état, reproposé au prochain démarrage --
exactement le comportement demandé : cet écran revient à chaque
lancement tant qu'il reste au moins un job non traité.

**Diagnostic matériel non automatique** (`_go_to_settings`/`_refresh_system_info`,
voir §5 vicies duo) : ouvrir la page Paramètres n'analyse plus rien tout
seul -- affiche « Détection non lancée -- cliquez sur « Analyser » »
jusqu'au clic sur le bouton dédié (renommé Actualiser → Analyser, plus
juste pour un premier déclenchement).

**Vérifié réellement** (`tests/test_ui.py` §3/§3bis remplace l'ancienne
§3 unique, §8/§8a-d remplace l'ancienne §8 à job unique, §13 mise à jour ;
`tests/test_settings.py` §8 ; `tests/test_pipeline.py` §5 bis/§5 ter) :
Pause laisse une reprise possible, Stop avec confirmation « Annuler » ne
touche à rien, Stop avec confirmation « Abandonner » supprime la reprise
sans toucher au texte déjà traduit ; la liste de reprise gère
Reprendre/Autre moteur/Abandonner/Plus tard sur plusieurs jobs
simultanés, sans qu'abandonner l'un affecte les autres ; la page
Paramètres n'affiche rien tant que le bouton n'a pas été cliqué.

## 5 vicies novies. Icônes SVG, sélecteur de modèle OCR, sélecteurs sans molette, Paramètres vraiment responsive (ajouté le 26/08/2026)

Quatre demandes explicites de l'utilisateur en une fois.

**Icônes SVG réelles pour Pause/Stop** (pas d'emoji) : `ui/icons/pause.svg`
(deux barres vertes) et `ui/icons/stop.svg` (carré rouge), chargées via
`_svg_icon()` (`ui/main_window.py`). Repli explicitement demandé si Qt ne
peut pas charger le SVG (`icon.isNull()`) : texte seul, sans emoji ni
icône cassée. Vérifié réellement que ce repli ne serait probablement
jamais nécessaire : `qsvgicon.dll`/`qsvg.dll` sont déjà présents dans
l'exe gelé (inspection directe d'un dossier d'extraction onefile réel),
bundlés automatiquement par le hook PyInstaller/PySide6 sans configuration
supplémentaire.

**`NoScrollComboBox`** (`ui/main_window.py`) : remplace `QComboBox` sur
TOUS les sélecteurs de l'appli (langues, modèle, format de sortie, modèle
OCR) -- `wheelEvent` surchargé pour `event.ignore()` : la molette ne
change plus jamais la valeur sélectionnée par accident, mais continue de
faire défiler la page autour du sélecteur (l'évènement remonte au
parent). Seul un clic pour ouvrir la liste puis une sélection dedans
change la valeur, comme demandé.

**Sélecteur de modèle OCR (Traduire X)** remplace la case à cocher
« Utiliser Claude » (voir §5 undevicies) : les 4 fournisseurs déjà cités
à l'utilisateur dans la liste -- PaddleOCR et Claude (Anthropic) en haut,
RÉELLEMENT branchés à `core/vision_ocr.py` ; Grok (xAI) et ChatGPT
(OpenAI) en bas, en orange et désactivés dans le modèle du menu
(`item.setEnabled(False)`) -- listés comme demandé, mais pas
sélectionnables : leurs clés API sont préparées (voir §5 undevicies),
aucun appel réel n'existe encore. PaddleOCR reste le choix par défaut.
`output_format_combo` et `vision_model_combo` rejoignent la liste des
champs verrouillés pendant qu'une traduction tourne (`_set_running`) --
les changer en cours de route n'aurait aucun effet sur le job déjà lancé.

**Page Paramètres réellement responsive** -- bug réel trouvé et corrigé,
pas seulement une case cochée : un `QLabel` avec `setWordWrap(True)` mais
sans `QSizePolicy.Ignored` en horizontal calcule son `sizeHint()` sur son
texte en UNE SEULE ligne, et ce sizeHint remonte à travers
`QVBoxLayout`/`QScrollArea` jusqu'à forcer TOUTE la page à s'élargir en
conséquence -- une des descriptions de carte faisait ainsi passer la page
entière à plus de 3400 px de large, bien au-delà de n'importe quelle
fenêtre réelle, y compris en grand. Corrigé via `_wrapped_label()`
(`setWordWrap(True)` + `setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)`),
appliqué à tous les labels descriptifs de la page. Un essai de
`FlowLayout` personnalisé pour réorganiser dynamiquement les 3 boutons du
bloc cache OCR selon la largeur disponible a été tenté puis abandonné
(le protocole `heightForWidth` de Qt s'est révélé peu fiable à travers
plusieurs niveaux de `QScrollArea`/`QVBoxLayout` imbriqués) -- remplacé
par un simple empilement vertical (`QVBoxLayout`), moins élégant mais
garanti sans débordement, à n'importe quelle largeur de fenêtre.

**Vérifié réellement** (`tests/test_ui.py` §16/§17) : molette ignorée sur
un sélecteur réel (évènement `QWheelEvent` construit et envoyé, valeur
inchangée, évènement bien "non accepté" -- pas juste avalé) ; les 4
fournisseurs OCR listés avec le bon statut activé/désactivé ; verrouillage
réel des deux nouveaux sélecteurs pendant l'exécution ; ET, pour la page
Paramètres, un test géométrique réel (pas visuel) à la largeur MINIMALE
réelle de la fenêtre (`setMinimumSize`) confirmant qu'aucun bouton ne
déborde horizontalement -- le test qui a permis de découvrir le bug du
label ci-dessus, pas ajouté après coup pour l'habiller.

## 5 tricies. Installeur Windows (Inno Setup) -- premier pas vers la distribution (ajouté le 26/08/2026)

Demande explicite de l'utilisateur : « un installateur multi-étape simple
mais nécessaire à ce que chacun puisse avoir ça sur leur machine » --
avant d'envisager le reste de la distribution/déploiement, préparer ce
premier maillon concret : quelqu'un d'autre qui reçoit TRANSLAX doit
pouvoir l'installer proprement, sans connaître Python ni PyInstaller.

**Choix de l'outil** : Inno Setup 6, pas NSIS (l'autre option courante) --
gratuit, open source, le plus utilisé pour ce genre de logiciel, script
`.iss` lisible. N'était pas installé sur cette machine : téléchargé
depuis le dépôt GitHub officiel (`jrsoftware/issrc`, version 6.7.3, le
lien direct de téléchargement du site officiel étant en réalité une page
HTML de choix de version, pas le binaire) et installé silencieusement
(`/VERYSILENT /SUPPRESSMSGBOXES /NORESTART`).

**`installer/translax.iss`** : le comportement standard de l'assistant
Inno Setup EST déjà le "multi-étape" demandé (Bienvenue → dossier de
destination → icône Bureau (case à cocher) → prêt à installer →
installation → fin, avec case "lancer maintenant") -- pas besoin de pages
personnalisées pour cette première version. Décisions notables :
- **Installation par UTILISATEUR, pas par machine**
  (`PrivilegesRequired=lowest`, `{localappdata}\Programs\TRANSLAX`) :
  aucun droit administrateur requis, pour que "chacun puisse l'avoir sur
  sa machine" sans avoir à demander à un administrateur.
- **AppId fixe** (GUID généré une fois, jamais recopié à la main
  ailleurs) : permet à une future mise à jour de reconnaître
  l'installation existante plutôt que d'en poser une deuxième à côté.
- Raccourci menu Démarrer + icône Bureau optionnelle, vraie entrée de
  désinstallation Windows (« Paramètres → Applications »).
- Pas de page de licence (aucun fichier `LICENSE` n'existe encore dans ce
  projet -- rien à afficher, pas une omission).

**`scripts/build_installer.py`** : lit `core/version.py::VERSION` (une
seule source de vérité, jamais recopiée dans le `.iss`), vérifie que
`dist/TRANSLAX.exe` existe et est à jour AVANT d'appeler `ISCC.exe` --
ne construit jamais un installeur autour d'un exe absent ou périmé en
silence. Produit `dist_installer/TRANSLAX-Setup-<version>.exe`.

**`INSTALLER_BUILD.md`** (nouveau, même esprit que `MACOS_BUILD.md`) :
documente le processus étape par étape pour l'utilisateur lui-même,
y compris un avertissement honnête sur Windows SmartScreen -- un exe non
signé numériquement (le cas ici, signer coûte de l'argent, pas encore en
place) déclenche presque toujours cet avertissement sur la machine de
quelqu'un d'autre ; contournable (« Informations complémentaires →
Exécuter quand même »), mais mérite d'être su à l'avance plutôt que
découvert en marquant à tort le logiciel comme dangereux.

**Vérifié réellement** : installeur construit avec la vraie version de
l'exe (v1.17.0), lancé et son assistant suivi jusqu'au bout, TRANSLAX
installé puis relancé depuis son propre raccourci, puis désinstallé --
dossier d'installation confirmé disparu après désinstallation.

**Non fait ici, volontairement** : signature de code (payante, hors
scope pour l'instant) ; version macOS de l'installeur (l'utilisateur a
explicitement demandé de s'y consacrer plus tard : « pour le moment
concentre-toi plus sur Windows, et Mac on verra à la fin ») ; toute
automatisation de mise à jour (vérifier une nouvelle version, proposer de
la télécharger) -- explicitement hors du périmètre "simple" demandé pour
ce premier jet.

## 5 tricies unus. Dépôt Git, mise à jour intégrée (comme VS Code), installeur personnalisé (ajouté le 27/08/2026)

Trois demandes explicites de l'utilisateur en une fois : « un système de
mise à jour bien rodé dès le départ afin que mes futures changements se
fassent confortablement », un dépôt Git réel
(`https://github.com/AJTVIRTUAL/translax.git` = ce dossier), une fonction
« Chercher une mise à jour » dans les Paramètres câblée à ce dépôt,
« comme sur VS Code » (un clic, tout le reste se lance), et la liberté de
styliser l'installeur « au maximum » pour une sensation immersive.

**Dépôt Git réel, pas une supposition** : `git init` + `.gitignore`
(exclut `.venv/`, `dist/`, `build/`, `dist_installer/`) + remote `origin`
vers le dépôt réel, confirmé accessible (`gh repo view`, compte
`amilcarjoao` membre de l'organisation `AJTVIRTUAL`) avant tout push.
**Incident réel évité de justesse** : `doc/claud_api_key`, un fichier de
108 octets (taille compatible avec une vraie clé API) trouvé lors du
premier `git add -A` -- retiré immédiatement de l'index et ajouté au
`.gitignore` par son nom exact avant le tout premier commit, jamais
poussé sur GitHub. Premier commit + push vers `main`, puis tag `v1.17.0`
et première GitHub Release avec l'installeur joint (voir plus bas) --
socle réel sur lequel la mise à jour intégrée s'appuie, pas une adresse
théorique.

**`core/updater.py`** -- vérifie/télécharge via les Releases GitHub du
dépôt public (`GET /repos/AJTVIRTUAL/translax/releases/latest`, aucune
authentification nécessaire, 60 requêtes/heure largement suffisantes pour
un clic occasionnel) :
- `check_latest_release()` : dernière version publiée + URL de
  l'installeur joint + notes de version. Erreurs réseau/dépôt sans
  publication -> `UpdateCheckError` avec un message déjà lisible, jamais
  une exception brute remontée jusqu'à l'interface.
- `is_newer(distant, local)` : comparaison segment par segment (`1.9.0`
  correctement PAS plus récent que `1.17.0` malgré `"9" > "1"` en
  comparaison de chaînes -- piège explicitement testé).
- `download_installer(...)` : par blocs de 1 Mo, avec progression et
  annulation (`should_stop`) ; écrit d'abord un fichier `.part`, renommé
  seulement une fois complet -- jamais un `.exe` à moitié écrit qui
  pourrait être lancé par erreur.
- **`urllib.request` (bibliothèque standard), PAS `requests`** : sur
  Windows, `ssl.create_default_context()` utilise le magasin de
  certificats de l'OS lui-même, pas un fichier `certifi` embarqué à part
  -- une dépendance de moins à empaqueter correctement dans l'exe gelé
  (voir les trois correctifs de packaging PaddleOCR de ce même projet,
  tous causés par des fichiers que PyInstaller ne devine pas qu'il faut
  embarquer -- éviter d'en ajouter un quatrième volontairement).

**Interface (page Paramètres, carte « Mises à jour »)** : jamais de
vérification automatique au démarrage -- uniquement au clic sur
« Chercher une mise à jour » (`UpdateCheckWorker`, thread séparé, un appel
réseau ne doit jamais geler l'interface). Une version plus récente trouvée
fait apparaître « Mettre à jour » ; confirmation demandée, puis
téléchargement avec barre de progression (`UpdateDownloadWorker`) et,
une fois terminé, `updater.launch_installer_and_quit()` puis fermeture de
TRANSLAX -- « comme sur VS Code » : un clic, tout s'enchaîne.

**Mécanique de remplacement de l'exe en cours d'exécution** -- le vrai
problème technique derrière « tout le reste se lance » : le fichier
`TRANSLAX.exe` en cours d'exécution reste verrouillé par Windows tant que
le processus tourne, l'installeur ne peut donc le remplacer qu'une fois
TRANSLAX réellement terminé. Résolu en deux temps, pas un seul :
1. TRANSLAX lance l'installeur (`/SILENT /CLOSEAPPLICATIONS /NOCANCEL`)
   puis se ferme lui-même (`QApplication.quit()`, différé de 1,2 s pour
   laisser le message final s'afficher) -- le mécanisme PRINCIPAL.
2. `installer/translax.iss` : `CloseApplications=yes` /
   `RestartApplications=yes` (Gestionnaire de redémarrage de Windows) --
   une sécurité SUPPLÉMENTAIRE, utile si une AUTRE instance de TRANSLAX
   tournait en parallèle, pas le mécanisme principal.
3. L'entrée `[Run]` de relance automatique existante (voir §5 tricies)
   perd son flag `skipifsilent` : en mode `/SILENT` (mise à jour), elle
   s'exécute désormais automatiquement sans personne pour cocher une
   case -- la MÊME entrée sert l'installation manuelle (case à cocher,
   comportement inchangé) et la mise à jour automatique, sans les
   dupliquer.

**Installeur personnalisé** (« se sentir immergé dans le logiciel ») :
`scripts/build_installer_images.py` dessine, avec Qt (déjà une
dépendance -- pas besoin de Pillow), une bannière et un petit logo
reprenant le vrai logo et la vraie palette de TRANSLAX (fond sombre,
accent bleu -- voir `ui/styles.qss`) plutôt que les images grises par
défaut d'Inno Setup, exportés en `.bmp` (format exigé par Inno Setup) et
référencés via `WizardImageFile`/`WizardSmallImageFile`. Régénérées à
chaque build (`scripts/build_installer.py` les appelle avant `ISCC.exe`)
-- jamais périmées par rapport à `ui/icon.ico`.

**`scripts/release.py`** (le « bien rodé... confortablement » demandé) :
une seule commande enchaîne bump de version, build de l'exe, build de
l'installeur, commit + tag + push Git, et publication de la GitHub
Release avec l'installeur joint (CLI `gh`, déjà authentifié) -- s'arrête
au premier échec plutôt que de publier une release incohérente (ex. un
tag Git sans exe réel derrière). Vérifie d'abord que TRANSLAX n'est pas
en cours d'exécution (`tasklist`, jamais de dépendance externe comme
`psutil`) -- le fichier ne peut pas être reconstruit sinon.

**Vérifié réellement** : dépôt créé et poussé pour de vrai sur GitHub ;
`core/updater.py` testé contre le VRAI dépôt public (vraie réponse API,
vraie URL de téléchargement, vraie taille de fichier > 100 Mo) et contre
un vrai petit fichier réellement téléchargé (progression, absence de
fichier `.part` résiduel, annulation propre) -- pas un simulacre réseau ;
`tests/test_ui.py` §18 : câblage complet de la carte Paramètres (aucune
vérification automatique, nouvelle version trouvée/pas trouvée/erreur
réseau, confirmation, téléchargement, lancement de l'installeur mocké,
`QApplication.quit()` bien appelé).

## 5 tricies duo. Boutons ⓘ (info) : écran épuré, explications à la demande (ajouté le 27/08/2026)

Demande explicite de l'utilisateur : « dans le logiciel tout ce qui est
trop texte explicatif [...] je veux que ces explications soient
accessibles en cliquant sur un bouton i (info) qui ouvre un modal » --
plusieurs cartes de la page Paramètres (et un bloc de la page Traduire)
affichaient un paragraphe entier en permanence, alourdissant visuellement
un écran pensé pour rester épuré.

**`InfoDialog`** (`ui/main_window.py`) : modal minimaliste (texte +
bouton « Fermer »). **`_info_button(titre, texte)`** : petit bouton
« ⓘ » (`objectName="infoButton"`, carré comme le reste de l'interface --
voir §5 septendecies) qui ouvre `InfoDialog(titre, texte, button.window())`
au clic -- `button.window()` résolu au moment du CLIC, pas de la
construction : au clic, le bouton fait forcément déjà partie de la vraie
fenêtre, donc le modal se centre correctement sans avoir à faire remonter
`self` (MainWindow) à travers chaque appel de `Card(...)`.

**`Card`** accepte maintenant `info`/`info_title` optionnels : si fourni,
ajoute automatiquement ce bouton à côté du titre de la carte -- un seul
endroit à changer pour que toute nouvelle carte future en bénéficie sans
y repenser.

**Paragraphes déplacés dans un modal** (le texte reste identique, seul
l'endroit où le lire change) :
- Page Paramètres : cartes « Matériel réellement détecté et utilisé »,
  « Mises à jour », « Clés API », « Fichiers temporaires et cache OCR ».
- Carte « À propos de TRANSLAX » : renommée en interne (`info_title`)
  « Pourquoi TRANSLAX » -- seuls le numéro de version et le nom de
  l'éditeur restent visibles en permanence (des faits courts), le
  paragraphe qui explique le POURQUOI de l'application part dans le
  modal.
- Page Traduire : le paragraphe fixe sous le sélecteur de modèle OCR
  (Traduire X) devient un bouton ⓘ juste à côté du sélecteur, à l'endroit
  où l'explication est la plus pertinente.

**Non déplacé, volontairement** : les textes des DIALOGUES déjà
ponctuels (ApiKeysDialog, VisionReviewDialog, ResumeJobsDialog...) --
ceux-là ne contribuent pas à l'encombrement visuel des écrans
PERMANENTS, puisqu'ils ne s'affichent que lorsqu'on les ouvre
explicitement ; les indicateurs courts et fonctionnels (résultat d'une
analyse, avertissement anti-veille pendant la traduction...) restent
affichés tels quels -- ce ne sont pas des textes explicatifs mais de
l'état réel, pas du texte à cacher.

**Vérifié réellement** (`tests/test_ui.py` §19) : exactement 5 boutons ⓘ
sur la page Paramètres et 1 sur la page Traduire, chacun ouvrant bien un
modal distinct et correctement titré ; `InfoDialog` testé directement
(texte réellement affiché, bouton « Fermer » qui accepte réellement le
modal) -- pas seulement que la connexion clic → modal existe.

## 5 tricies tres. Préparation macOS : spec multiplateforme, guide reconstruit à jour (ajouté le 27/08/2026)

L'utilisateur est prêt à s'attaquer réellement à la version Mac (MacBook
Pro Apple Silicon) -- travail préparé depuis Windows (impossible de
construire ou tester sur macOS depuis cette session, PyInstaller ne fait
pas de compilation croisée), en vue d'une session Claude Code distincte
tournant réellement sur ce Mac.

**`TRANSLAX.spec` rendu multiplateforme**, sans rien changer au
comportement Windows (vérifié réellement : reconstruction + lancement de
l'exe après modification, identique à avant) :
- Icône conditionnelle (`ui/icon.icns` sur macOS, `ui/icon.ico` sur
  Windows -- exigence propre à chaque OS, PyInstaller ne les accepte pas
  de façon interchangeable).
- Bloc `BUNDLE(...)` ajouté, actif UNIQUEMENT si `sys.platform == "darwin"`
  -- sans lui, `pyinstaller TRANSLAX.spec` sur Mac ne produirait qu'un
  exécutable Unix nu, pas un vrai bundle `.app` (contrairement au
  raccourci `pyinstaller --onefile --windowed` utilisé par l'ancienne
  version de ce guide, qui ajoute cet emballage tout seul -- un `.spec`
  écrit à la main doit le faire explicitement). Enveloppe l'EXE onefile
  existant sans changer de mode d'empaquetage.
- Les trois correctifs de packaging PaddleOCR (§5 vicies, vicies sexies,
  vicies septies) restent inchangés et s'appliquent tels quels : basés
  sur des utilitaires PyInstaller (`collect_data_files`/`copy_metadata`/
  `collect_dynamic_libs`) qui inspectent ce qui est RÉELLEMENT installé
  sur la machine de construction, pas figés sur des noms de fichiers
  Windows.
- **Non testé sur un vrai Mac** au moment d'écrire ce correctif (honnêteté
  explicite dans les commentaires du fichier) -- en particulier le bloc
  `BUNDLE`, seule vraie inconnue de cette modification.

**Vérification réelle de compatibilité Apple Silicon des dépendances
compilées**, avant d'écrire quoi que ce soit dans le guide (pas une
supposition) : liste des fichiers RÉELLEMENT publiés sur PyPI pour les
versions EXACTES pinées dans `requirements.txt`, une par une --
`paddlepaddle==3.3.1` (wheel arm64 Python 3.13 confirmé),
`paddleocr==3.7.0` (pur Python, aucune question de plateforme),
`ctranslate2==4.8.1` (wheel arm64 confirmé), `opencv-contrib-python==4.10.0.84`
et `pymupdf==1.28.2` (ABI stable, couvrent 3.13). Résultat : aucune de ces
dépendances ne devrait avoir besoin d'être compilée localement sur ce
Mac -- une inconnue réelle et documentée en moins avant même de démarrer.

**`MACOS_BUILD.md` entièrement reconstruit** (l'ancienne version datait
de la 1.6.0, un an de fonctionnalités en retard) : Git remplace la copie
manuelle du dossier (le projet a maintenant un vrai dépôt, voir §5
tricies unus) ; `TRANSLAX.spec` remplace la commande `pyinstaller`
brute ; liste à jour de tout ce qu'il y a de nouveau à construire et
tester (PaddleOCR, export PDF, hub/Paramètres/Outils, Pause/Stop, liste
de reprise, boutons ⓘ) ; tableau de compatibilité des dépendances
compilées ; et deux limites explicitement documentées comme NON corrigées
ici volontairement -- la mise à jour intégrée ne cherche aujourd'hui
qu'un installeur Windows (rien d'automatique côté Mac pour l'instant), et
le diagnostic matériel de la page Paramètres ne détecte que CUDA, jamais
MPS (l'accélération GPU propre à Apple Silicon) -- affichera donc « aucun
GPU détecté » même si la puce en a un utilisable par PyTorch autrement,
une vraie limite à corriger plus tard, pas une erreur de ce guide.

## 6. Interface

```
┌────────────────────────────────────────────────┐
│  TRANSLAX                                 [–][x]│
├────────────────────────────────────────────────┤
│  Fichier source :  [ IlluVol1.txt        ] [...] │
│  (glisser-déposer un PDF / TXT / MD ici aussi)   │
│                                                  │
│  Langue source :  [ Anglais  ▾]                 │
│  Langue cible  :  [ Français ▾]                 │
│  [x] Nettoyer les titres à la fin                │
│                                                  │
│  Fichier de sortie : IlluVol1.md  (auto)        │
│                                                  │
│              [   ▶ Traduire   ]  [ ■ Stop ]      │
│                                                  │
│  ████████████████░░░░░░░░░░  62%  (450/726)     │
│  ~14 s/segment · reste ≈ 1h04                    │
│                                                  │
│  ┌─ Détails ────────────────────────────────┐   │
│  │ [450/726] Segment traduit (92 mots)       │   │
│  │ [449/726] Segment traduit (78 mots)       │   │
│  └───────────────────────────────────────────┘  │
│                                                  │
│  ✓ Terminé → [Ouvrir le fichier] [Ouvrir dossier]│
└────────────────────────────────────────────────┘
```

Un seul écran suffit : pas d'onglets, pas de navigation.

## 6 bis. Barre de titre personnalisée (ajouté le 22/08/2026)

La barre native Windows (icône + titre + Réduire/Agrandir/Fermer) est
entièrement remplacée par une barre stylisée, cohérente avec le thème sombre
de l'appli plutôt que le style natif du système.

- **Fenêtre** créée avec `Qt.FramelessWindowHint` (`ui/main_window.py`) :
  Windows ne dessine plus rien en haut de la fenêtre.
- **`ui/titlebar.py`** recrée le nécessaire : icône de l'appli (18×18,
  `ui/icon.ico`) tout à gauche, titre « TRANSLAX », puis trois boutons
  Réduire / Agrandir-Restaurer / Fermer — **dessinés en `QPainter`** (traits
  fins façon Windows 11), pas des glyphes de police, pour un rendu identique
  quelle que soit la police installée.
- **Ce qui a fallu recréer à la main**, puisque le cadre natif ne le fournit
  plus :
  - déplacer la fenêtre en glissant la barre → `QWindow.startSystemMove()` ;
  - agrandir/restaurer au double-clic sur la barre, ou via le bouton ;
  - redimensionner depuis les bords → `QWindow.startSystemResize()`,
    déclenché depuis une fine marge de 6 px réservée tout autour de la
    fenêtre (`RESIZE_MARGIN` dans `ui/titlebar.py`), seul « bord » qui
    subsiste une fois le cadre natif supprimé.
  - glisser une fenêtre maximisée la restaure d'abord (comportement natif
    Windows attendu en tirant sur la barre).
  - l'icône Agrandir/Restaurer reste synchronisée avec l'état réel de la
    fenêtre (`MainWindow.changeEvent`, sur `QEvent.WindowStateChange`) —
    pas seulement au clic sur le bouton, aussi après un double-clic, un
    Win+Haut, ou un « snap » Windows.
- **Limitations assumées, pas des oublis** :
  - pas de « Snap Layouts » Windows 11 au survol du bouton Agrandir (lié au
    bouton natif lui-même via un hit-test DWM spécial, non reproductible
    sans crochets Win32 bas niveau) ;
  - pas d'ombre portée automatique autour de la fenêtre (perdue avec le
    cadre natif, pas restaurée) ;
  - coins de fenêtre carrés (pas l'arrondi automatique de Windows 11 sur les
    fenêtres normales).
- **Vérifié avec de vraies actions souris simulées** (`SendInput` +
  `startSystemMove`/`Resize`, pas juste des signaux Qt internes) : glisser
  déplace réellement la fenêtre, tirer un bord la redimensionne, Réduire /
  Agrandir / Restaurer / Fermer fonctionnent chacun individuellement,
  l'icône Agrandir⇄Restaurer bascule visuellement (confirmé par capture
  d'écran réelle). Un faux échec du bouton Fermer pendant les tests venait
  d'une autre fenêtre du bureau qui chevauchait la zone de clic testée — pas
  du code — résolu en déplaçant la fenêtre avant de recliquer.

### Présentation macOS (ajoutée le 23/08/2026)

`ui/titlebar.py` choisit sa présentation une seule fois à la construction,
via `sys.platform` — **le chemin Windows ci-dessus n'a pas été modifié**,
seul du code Mac a été ajouté en parallèle :

- **Pastilles rouge/jaune/verte** (`MacTrafficLightButton`) alignées à
  gauche, dans l'ordre natif Mac (fermer, réduire, agrandir), dessinées en
  cercles pleins ; le glyphe (×, −, +) n'apparaît qu'au survol de la
  pastille précise (`enterEvent`/`leaveEvent` → `self.update()`, testé via
  un journal de débogage qui confirme la séquence ENTER → peint
  `underMouse=True` → LEAVE → repeint `underMouse=False`).
- **Titre centré**, pas d'icône dans la barre (convention Mac : le Dock en
  tient déjà lieu) — `set_icon()` devient un no-op silencieux sur Mac
  plutôt que de planter.
- **Interface publique strictement identique** entre les deux présentations
  (signaux `minimize_clicked`/`maximize_clicked`/`close_clicked`, méthodes
  `set_icon`/`set_maximized`) : `ui/main_window.py` ne contient aucun code
  spécifique à l'OS, il utilise `TitleBar` telle quelle des deux côtés.
- Glisser/redimensionner/agrandir restent gérés par le même code partagé
  (`startSystemMove`/`startSystemResize`, `MainWindow.mousePressEvent`) —
  aucune duplication nécessaire, ces API Qt sont déjà multiplateformes.
- **Simplification assumée** : le vrai macOS révèle les trois glyphes
  ensemble dès qu'on survole le groupe des pastilles ; ici chaque pastille
  ne révèle que la sienne. Différence mineure, plus simple à maintenir de
  façon fiable.
- **Vérifié en simulant `IS_MAC = True` sur cette machine Windows**
  (capture d'écran réelle : pastilles bien positionnées, titre bien centré,
  clics fonctionnels sur les trois boutons) — mais **jamais sur un vrai
  Mac**, faute d'accès à un. Voir `MACOS_BUILD.md`.

## 7. Nommage de la sortie et reprise

- Nom **pendant le travail** : toujours `<nom du fichier source sans
  extension>.md` — c'est sous ce nom que la reprise et l'état fonctionnent
  du début à la fin (inchangé).
- Nom **une fois terminé** : renommé selon le titre traduit si
  `Job.translate_title` est actif (par défaut) — voir §5 bis pour le détail
  et sa limite connue.
- Dossier : celui du fichier source par défaut, modifiable dans l'UI.
- **Reprise** : un fichier compagnon `.translax/<nom>.progress.json` mémorise
  le nombre exact de segments écrits **et une empreinte SHA-256 du fichier
  source**. Au lancement :
  - même fichier source, traduction incomplète → **reprise automatique** ;
  - fichier source différent portant le même nom → l'UI demande quoi faire
    (écraser / renommer) ;
  - traduction déjà terminée → l'UI propose de refaire ou d'ouvrir.

> **Pourquoi pas le comptage de blocs du script d'origine** : il comptait les
> blocs séparés par une ligne vide dans le `.md`. Un paragraphe traduit
> contenant une ligne vide décale ce compte, et la reprise repart au mauvais
> endroit. L'empreinte du source règle en plus la question « est-ce bien la
> même traduction ? », que le comptage ne pouvait pas poser.

## 8. Packaging — fait le 22/08/2026

**Avant chaque packaging**, régénérer la date de build (voir §5 ter) :

```
.venv\Scripts\python.exe scripts\stamp_build_date.py
```

Puis :

```
.venv\Scripts\pyinstaller --onefile --windowed --name TRANSLAX --icon ui\icon.ico ^
    --add-data "ui\styles.qss;ui" --add-data "ui\icon.ico;ui" main.py
```

- `--windowed` : pas de console derrière la fenêtre.
- `--icon ui\icon.ico` : icône du fichier .exe lui-même (celle vue dans
  l'Explorateur, pas celle affichée par l'appli une fois lancée).
- **`--add-data`** pour `ui/styles.qss` et `ui/icon.ico` : sans ça, PyInstaller
  n'embarque QUE le code Python — ces deux fichiers ne sont pas du code, ils
  auraient été absents du paquet et l'appli aurait démarré sans thème ni
  icône dans sa propre fenêtre. `main.py::resource_path()` et
  `ui/main_window.py::_asset_path()` savent retrouver ces fichiers aussi
  bien lancés depuis les sources (chemin relatif) que depuis l'exe gelé
  (`sys._MEIPASS`, le dossier où PyInstaller déplie tout au lancement).
- Le modèle NLLB (~2,4 Go) n'est **pas** embarqué dans l'exécutable : il
  reste dans le cache `huggingface`, partagé avec le mode développement.
- Résultat : `dist\TRANSLAX.exe`, **307 Mo**, lançable au double-clic.
- **Piège rencontré** : relancer PyInstaller alors qu'une instance de
  TRANSLAX.exe est encore ouverte échoue avec `PermissionError` (le fichier
  .exe est verrouillé). Fermer toutes les fenêtres TRANSLAX avant de
  repackager.
- **Vérifié avec le vrai .exe fini**, pas seulement en relisant le code :
  - lancement, icône (barre des tâches + barre de titre), style QSS — tous
    corrects, confirmant que le `--add-data` fonctionne ;
  - le cache `huggingface` partagé est bien lu (600M affiché « déjà
    téléchargé (4,6 Go) ») ;
  - **téléchargement d'un modèle absent (1.3B distillé) réellement
    déclenché et confirmé progresser** (fichier `.incomplete` du cache
    monté à 1,37 Go pendant le test) — la crainte initiale que PyInstaller
    oublie d'embarquer les certificats SSL (`certifi`) pour ce genre d'appli
    ne s'est pas confirmée : `hook-certifi` s'exécute correctement, les
    téléchargements HTTPS fonctionnent identiquement en mode packagé.
  - Limite constatée en testant : cliquer **Stop** pendant le
    *téléchargement* du modèle ne l'interrompt pas — `should_stop` n'est
    vérifié que dans la boucle de traduction, pas pendant
    `from_pretrained()`. Le Stop reste pleinement fonctionnel pendant la
    traduction elle-même (déjà testé, voir §6 bis / tests/test_ui.py) ;
    seule l'interruption d'un téléchargement en cours n'est pas câblée.
- **Raccourci de bureau retargeté** : `C:\Users\amilc\Desktop\TRANSLAX.lnk`
  pointe maintenant directement sur `dist\TRANSLAX.exe` (choix de
  l'utilisateur : voir §9, plus besoin du venv ni de Python installé pour
  lancer l'appli). Conséquence assumée : toute modification future du code
  nécessite de refermer TRANSLAX puis de relancer la commande PyInstaller
  ci-dessus pour que le raccourci reflète le changement — voir §9.

## 8 bis. Packaging macOS

PyInstaller ne fait pas de compilation croisée : un `.app` macOS doit être
construit sur un vrai Mac, pas depuis cette machine Windows. Tout ce qu'il
faut est prêt côté projet :

- `ui/icon.icns` — généré le 23/08/2026 depuis le logo source
  (`DRAFTS/IMG/icon.png`, 1024×1024), via Pillow (`Image.save(...,
  format="ICNS")`) — une conversion pure, faisable depuis Windows sans
  aucun outil Apple, puisque macOS exige ce format pour l'icône d'un `.app`
  (le `.ico` ne convient pas à cet usage-là).
- Le reste du code (`core/`, `ui/`) est déjà multiplateforme : les seuls
  points sensibles identifiés (`open_in_explorer`, `reveal_in_explorer`
  dans `ui/main_window.py`) avaient déjà leurs branches macOS (`darwin`)
  écrites dès le départ.
- **Guide complet** : voir [`MACOS_BUILD.md`](MACOS_BUILD.md) — commande
  de build exacte (le séparateur `--add-data` est `:` sur Mac, `;` sur
  Windows, seule vraie différence), installation de Python 3.13 pas à pas
  (guide écrit pour un premier usage de Python/Terminal sur Mac).
- **Barre de titre adaptée à la convention Mac** (§6 bis « Présentation
  macOS », 23/08/2026) : pastilles rouge/jaune/verte à gauche, titre
  centré — plus l'ancienne limitation « boutons à droite façon Windows »
  décrite ici initialement, corrigée depuis.
- **Piège réel rencontré (23/08/2026), pas juste anticipé** : Python 3.14
  (déjà installé sur le Mac cible avant même de commencer) casse
  l'installation — **vérifié sur PyPI** que `torch==2.6.0` et
  `PySide6==6.11.2`, les versions figées dans `requirements.txt`, n'ont
  aucune roue compilée pour cp314 (les autres dépendances du projet
  n'auraient posé aucun souci). Solution : installer Python 3.13 à côté du
  3.14 (les deux cohabitent sans conflit) et créer le venv avec
  `python3.13 -m venv .venv` explicitement — détaillé dans
  `MACOS_BUILD.md` §3 et §5.
- **Non testé sur un vrai Mac** faute d'accès à un — le code et les
  bibliothèques utilisées (Qt, PyInstaller, torch) sont réputés
  multiplateformes, mais seul un test réel sur Mac le confirmera.

## 7 bis. Icône et raccourci de bureau

- **Icône** : `ui/icon.ico` — logo « TX » fourni par l'utilisateur
  (`DRAFTS/IMG/icon.ico`, copié tel quel dans le projet le 22/08/2026),
  dégradé bleu → turquoise sur fond bleu nuit, 6 résolutions embarquées
  (16 à 256 px). Câblée à trois endroits : `app.setWindowIcon(...)` dans
  `main.py` (icône de la barre des tâches), `window.setWindowIcon(...)`
  (idem), et `TitleBar.set_icon(...)` dans `ui/titlebar.py` (icône affichée
  en haut à gauche de la fenêtre elle-même, dans la barre de titre
  personnalisée — voir §6 bis).
  > Une première version générée par Pillow (monogramme « T » simple) a été
  > remplacée par ce logo le 22/08/2026, à la demande de l'utilisateur.
- **Raccourci de bureau** : `C:\Users\amilc\Desktop\TRANSLAX.lnk`, créé via
  PowerShell (`WScript.Shell`). Deux générations :
  1. D'abord pointé sur `.venv\Scripts\pythonw.exe "main.py"` (mode
     développement, mises à jour instantanées) le temps que l'appli soit
     encore en évolution active.
  2. **Retargeté le 22/08/2026 sur `dist\TRANSLAX.exe`** une fois le
     packaging fait et vérifié (choix explicite de l'utilisateur, voir §9)
     — dossier de travail `dist\`, icône reprise depuis l'exe lui-même.
  Le fichier `ui/icon.ico` a été remplacé en place (même chemin) à un
  moment donné : pas besoin de recréer le raccourci pour ça, juste le cache
  d'icônes de l'explorateur rafraîchi (redémarrage d'`explorer.exe`).
- **Point technique vérifié en testant (mode développement)** : lancer le
  raccourci pointé sur les sources fait apparaître 2 processus
  `pythonw.exe`, pas 1 — normal, pas un bug (le lanceur de venv Python 3.13
  reste actif en supervision à côté du vrai interpréteur). Un seul des deux
  porte réellement la fenêtre. Le même phénomène existe côté `.exe` packagé
  (bootloader PyInstaller + processus réel), tout aussi normal.

## 9. Plan de développement

1. ✅ **Moteur en modules réutilisables, testable en CLI** — `core/` écrit,
   segmentation revalidée à l'identique (726/726) contre la sortie de
   référence, `cli.py` pour l'éprouver sans UI, `tests/test_pipeline.py`
   pour la reprise et l'écriture incrémentale.
2. ✅ **Fenêtre PySide6** : `main.py` + `ui/`, traduction dans un `QThread`,
   barre de progression branchée sur les signaux, aucune UI figée.
3. ✅ **Détails visuels** : pourcentage, vitesse lissée, temps restant, journal
   défilant, glisser-déposer, sélecteurs de langue, bouton Stop, dialogues de
   reprise/écrasement, ouverture du fichier et de l'emplacement du dossier
   (fichier sélectionné dans l'explorateur, pas juste le dossier ouvert).
3 bis. ✅ **Sélecteur de modèle** : 600M / 1.3B distillé / 3.3B exposés dans
   l'UI (voir §3 ter), avec avertissement de taille/temps avant tout
   téléchargement d'un modèle absent du cache local — jamais de
   téléchargement silencieux de plusieurs Go.
4. ⬜ **Mode « Rapide » (CTranslate2)** en option explicite, avec
   avertissement sur la perte de qualité (voir §10).
5. ✅ **Packaging** `TRANSLAX.exe` (307 Mo, §8) — icône, style et téléchargement
   de modèle vérifiés dans l'exe fini, pas juste en dev. Raccourci de bureau
   retargeté dessus. Reste à faire à l'occasion : tester sur une seconde
   machine sans le venv de développement, pour confirmer l'autonomie totale.
6. ✅ **Nettoyage des pages + traduction du titre** (§5 bis, 23/08/2026) —
   en-têtes/pieds de page répétés et numéros de page (arabes/romains)
   détectés et retirés avant traduction, avec rapport de confirmation
   avant de continuer ; le `.md` de sortie est renommé selon le titre
   traduit. Calibré et vérifié sur deux vrais PDF fournis par
   l'utilisateur, avec le vrai modèle NLLB (pas seulement le moteur
   factice des tests).

## 10. Pièges déjà rencontrés (à ne pas reproduire)

- **Écriture non-incrémentale** : un refactor « pour la vitesse » avait
  déplacé l'écriture après la boucle entière → le fichier de sortie restait
  vide pendant des heures alors que le travail avançait. Toujours écrire +
  `flush()` après **chaque** segment.
- **Batching + padding avec `generate()`** : traduire plusieurs segments
  courts ensemble fait dérailler le modèle (boucles « nous nous sommes nous
  nous… »). Rester séquentiel.
- **`num_beams=1` (glouton)** : plus rapide, mais mêmes boucles de
  répétition. Garder `num_beams=4` + `no_repeat_ngram_size=3`.
- **CTranslate2** : 4 à 8× plus rapide et pas de bug de padding, mais on a
  mesuré de vraies substitutions de mots par rapport à `transformers`. À ne
  proposer qu'en mode explicitement choisi, jamais par défaut.
- **PDF avec tableaux en colonnes** : l'extraction mélange les colonnes sur
  une même ligne. Limitation connue, documentée, non contournée.
- **Fichiers texte sans aucune ligne vide** : détection automatique par
  comptage des lignes vides (seuils au §5), pas d'intervention manuelle.
- **Caractères bizarres dans le terminal** : problème d'affichage de la
  console Windows sur de l'UTF-8, **pas** une corruption des fichiers.
  Toujours vérifier avec `open(..., encoding="utf-8")` plutôt qu'avec
  l'affichage brut de la console.
- **La vitesse dépend de la longueur des segments** : ~10-17 s/segment sur le
  livre « lois de l'univers » (médiane 23 mots), mais 35-70 s/segment sur
  « Bloodlines » (médiane 99 mots). Le tout premier segment paie en plus la
  chauffe du modèle. L'ETA est donc lissée (moyenne exponentielle) et non
  calculée sur la moyenne cumulée, sinon elle reste fausse très longtemps.
- **Le `.venv` de `pyTraduction` est vide** (pip seul) : les dépendances du
  pipeline d'origine sont en réalité installées sur le Python global. Ne pas
  s'y fier pour packager.
- **Regroupement flou sans filtre de forme au préalable** : comparer des
  milliers de lignes entre elles par ressemblance (`SequenceMatcher`) sans
  d'abord éliminer les candidats évidemment trop longs pour être un
  en-tête/pied de page fait exploser le temps de calcul (plusieurs minutes
  sur un livre de 231 pages). Toujours filtrer par forme (longueur, nombre
  de mots) AVANT toute comparaison floue, jamais après.
- **Connecter un signal Qt cross-thread à une fonction Python ordinaire**
  (pas une méthode liée d'un QObject) : Qt ne peut pas déterminer le thread
  du récepteur et bascule en connexion directe — le gestionnaire s'exécute
  alors de façon synchrone sur le thread ÉMETTEUR au lieu d'être mis en
  file d'attente vers le thread récepteur voulu, ce qui peut bloquer toute
  la suite d'une chaîne de signaux qui en dépend. Toujours connecter vers
  une vraie méthode de QObject, y compris dans les tests.

## 11. Licences — usage strictement personnel/non-commercial

Vérifié le 22/08/2026 auprès des sources officielles (fiche du modèle sur
HuggingFace, page de licensing Artifex) :

| Dépendance | Licence | Verdict |
|---|---|---|
| **facebook/nllb-200-distilled-600M** | **CC-BY-NC-4.0** | **Bloque toute vente.** Le modèle est explicitement réservé à un usage non-commercial ; sa fiche officielle précise qu'il s'agit d'un modèle de recherche, « not released for production deployment ». |
| **PyMuPDF** | AGPL-3.0 (ou licence commerciale Artifex payante) | Sous AGPL, un logiciel fermé ne peut pas l'utiliser sans publier tout son code source sous AGPL. Vendre TRANSLAX fermé nécessiterait une licence Artifex payante — non prise. |
| PySide6 | LGPL-3.0 | OK en usage fermé |
| transformers | Apache 2.0 | OK |
| torch | BSD-3-Clause | OK |
| PyInstaller | GPLv2 + exception explicite pour exécutables commerciaux fermés | OK |

**Décision (22/08/2026)** : TRANSLAX reste un outil **personnel / interne**,
non vendu, non distribué contre paiement, pas mis en service payant (pas de
SaaS). C'est l'usage pour lequel toutes ces licences sont libres. Pas de
changement de code nécessaire.

> Si un jour la commercialisation redevient d'actualité, il faudra reprendre
> ces deux points-là en premier — remplacer NLLB-200 par un modèle sous
> licence permissive (ex. `facebook/m2m100_418M`, Apache 2.0, déjà présent
> dans le cache local) et PyMuPDF par une bibliothèque d'extraction PDF sous
> licence permissive (ex. `pypdf`, MIT) — avant toute mise en vente, pas
> après.

## 12. Évolutions possibles (plus tard)

- Ouvrir vraiment les ~200 langues NLLB dans les sélecteurs.
- File d'attente : plusieurs fichiers traduits à la suite.
- Glisser-déposer d'un dossier entier.
- Détection automatique de la langue source.
