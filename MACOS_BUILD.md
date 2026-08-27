# Construire TRANSLAX sur macOS

Ce document existe parce que PyInstaller **ne fait pas de compilation
croisée** : il empaquette l'interpréteur Python natif et les bibliothèques
compilées (torch, Qt, PaddlePaddle...) de la plateforme sur laquelle il
tourne. Le `.exe` Windows a été construit sur une machine Windows ; ce
`.app` macOS doit être construit **sur un vrai Mac**, en suivant ces
étapes.

> **Honnêteté avant tout** : ce guide combine du code déjà validé sur
> Windows, des bibliothèques connues pour être multiplateformes, et une
> vérification réelle (pas supposée) que les dépendances compilées les
> plus lourdes ont bien des paquets pour Apple Silicon (voir la section
> dédiée plus bas) -- mais **rien de tout ça n'a encore tourné sur un vrai
> Mac**. Tu seras le premier vrai test. Si une étape échoue, garde le
> message d'erreur complet : c'est ce qu'il faut pour corriger, ici dans
> le chat Windows ou directement avec Claude sur ce Mac.

**Ta machine** : MacBook Pro, puce Apple Silicon (arm64), 2026. Le build
produit sur ce Mac ne tournera que sur des Mac Apple Silicon -- pas un
problème pour toi : tu construis et tu utilises sur la même machine.

Ce guide part du principe que tu n'as **jamais utilisé Python ni le
Terminal sur Mac** -- chaque étape est écrite pour ça, rien n'est supposé
acquis.

---

## Ce qui a changé depuis le dernier essai Mac (v1.6.0 → v1.19.0)

Un an de fonctionnalités en plus, pour resituer ce que ce guide va
construire et tester :

- **OCR local (PaddleOCR)** pour Traduire X, gratuit et sans connexion --
  Anthropic (Claude, payant) reste disponible en option. Deux nouvelles
  dépendances lourdes et compilées (`paddleocr`, `paddlepaddle`) --
  compatibilité Apple Silicon **vérifiée réellement** avant d'écrire ce
  guide, voir la section suivante.
- **Export PDF** en plus du Markdown (bibliothèque `pymupdf`).
- **Trois écrans séparés** : un hall d'accueil au démarrage (Traduire /
  Extraire / Annuler nettoyage), une page Paramètres (diagnostic
  matériel, mises à jour, clés API, cache), une page Outils.
- **Pause (vert) et Stop (rouge) séparés** -- Pause interrompt sans rien
  perdre, Stop abandonne définitivement (confirmation demandée).
- **Liste complète des traductions interrompues** au démarrage (pas
  seulement la dernière), avec Reprendre / Autre moteur / Abandonner.
- **Boutons ⓘ (info)** à la place des longs paragraphes explicatifs --
  écran plus épuré.
- **Mise à jour intégrée** (page Paramètres, "comme sur VS Code") --
  **Windows uniquement pour l'instant**, voir la section dédiée plus bas.
- **Le projet est maintenant un vrai dépôt Git**, hébergé sur
  `https://github.com/AJTVIRTUAL/translax` -- simplifie énormément
  l'étape 4 (plus besoin de copier le dossier à la main).
- `TRANSLAX.spec` remplace la commande `pyinstaller --onefile --windowed
  ...` de l'ancienne version de ce guide -- inclut maintenant, de façon
  multiplateforme, tous les correctifs de packaging découverts pour
  PaddleOCR côté Windows (voir §7).

## Compatibilité Apple Silicon des dépendances compilées -- vérifiée réellement

Avant d'écrire ce guide, chaque dépendance non pure-Python de
`requirements.txt` a été vérifiée directement sur PyPI (liste réelle des
fichiers publiés pour la version EXACTE pinée dans ce projet, pas une
version au hasard) :

| Paquet | Version pinée | Wheel macOS arm64 (Python 3.13) |
|---|---|---|
| `paddlepaddle` | 3.3.1 | ✅ `paddlepaddle-3.3.1-cp313-cp313-macosx_11_0_arm64.whl` |
| `paddleocr` | 3.7.0 | ✅ Pur Python (`py3-none-any`), aucune question de plateforme |
| `ctranslate2` | 4.8.1 | ✅ `ctranslate2-4.8.1-cp313-cp313-macosx_11_0_arm64.whl` |
| `opencv-contrib-python` | 4.10.0.84 | ✅ `cp37-abi3-macosx_11_0_arm64` (ABI stable, couvre 3.13) |
| `pymupdf` | 1.28.2 | ✅ `cp310-abi3-macosx_11_0_arm64` (ABI stable, couvre 3.13) |

**Ce que ça veut dire concrètement** : `pip install -r requirements.txt`
ne devrait avoir besoin de RIEN compiler soi-même sur ce Mac pour ces
paquets-là -- que des wheels précompilées, déjà prêtes pour Apple
Silicon. Une compilation locale (lente, pouvant échouer sur des
dépendances système manquantes) serait le signe que quelque chose s'est
mal passé, pas le comportement normal attendu.

**Ce qui reste réellement une inconnue** (aucune garantie tirée de cette
vérification PyPI) : que le CODE lui-même (pas juste l'installation)
tourne correctement à l'exécution sur cette architecture -- en particulier
`paddlepaddle`, connu pour des différences de comportement CPU entre
architectures (voir le piège oneDNN déjà rencontré et documenté dans
`SPEC.md` §5 vicies, propre à x86_64 -- pourrait ne pas exister sur ARM,
ou en cacher un autre spécifique à ARM, aucun moyen de le savoir sans
tester réellement).

## 0. Déjà installé une fois ? Mettre à jour plutôt que tout refaire

Si Python 3.13, Xcode Command Line Tools et l'environnement `.venv` du
projet sont déjà en place sur ce Mac, passer directement à l'étape 4
(`git pull`), puis 5 (`pip install -r requirements.txt` -- ne fait rien
si tout est déjà à la bonne version, ajoute seulement ce qui manque), 6,
7 et 8.

---

## 1. Ouvrir le Terminal

- **Cmd + Espace** (Spotlight), taper `Terminal`, Entrée ;
- ou Finder → Applications → Utilitaires → Terminal.

**Réflexes utiles** : `pwd` (dossier actuel), `ls` (liste les fichiers),
`cd NomDuDossier` (entrer), `cd ..` (remonter). Astuce : taper `cd ` (avec
l'espace) puis glisser un dossier depuis le Finder dans le Terminal écrit
le chemin complet tout seul.

## 2. Xcode Command Line Tools

```bash
xcode-select --install
```

Cliquer **Installer**, accepter la licence. Si déjà installé, le Terminal
le dit, rien à faire.

## 3. Python 3.13

```bash
python3 --version
```

> **Si la réponse est `Python 3.14.x` ou plus récent** : ce n'est pas la
> version à utiliser -- `torch`/`PySide6` figés dans `requirements.txt`
> n'ont pas de wheel pour 3.14 sur PyPI. Les deux versions cohabitent
> très bien (`python3.14` et `python3.13`), pas besoin de désinstaller
> quoi que ce soit.

1. **https://www.python.org/downloads/macos/**, chercher la dernière
   révision de la branche **3.13** (pas 3.14).
2. Télécharger **« macOS 64-bit universal2 installer »**.
3. Ouvrir le `.pkg`, suivre l'installateur (mot de passe Mac demandé,
   normal).
4. **Ne pas sauter** : Finder → Applications → dossier **« Python 3.13 »**
   → double-clic sur **`Install Certificates.command`** (sans ça, tout
   téléchargement HTTPS de TRANSLAX -- modèles NLLB, MADLAD-400,
   OPUS-MT -- échouerait avec une erreur de certificat SSL, un piège
   connu et systématique de Python installé via python.org sur Mac).
5. Fermer et rouvrir le Terminal, puis :
   ```bash
   python3.13 --version
   ```
   Doit répondre `Python 3.13.x`. Si introuvable, redémarrer le Mac.

## 4. Récupérer le projet (Git, pas une copie manuelle)

Le dépôt est maintenant sur GitHub -- plus besoin de clé USB/AirDrop.

**Première fois sur ce Mac** :
```bash
git clone https://github.com/AJTVIRTUAL/translax.git
cd translax
```

**Déjà cloné une fois** (voir §0) :
```bash
cd chemin/vers/translax
git pull
```

## 5. Environnement Python du projet

```bash
python3.13 -m venv .venv
source .venv/bin/activate
```

**`python3.13`, pas `python3`** -- garantit cette version précise même si
3.14 est aussi installée. L'invite du Terminal doit afficher `(.venv)`
au début de la ligne une fois activé. **`source .venv/bin/activate` est à
retaper à chaque nouveau Terminal.**

```bash
python --version
pip install --upgrade pip
pip install -r requirements.txt
```

**Nettement plus volumineux qu'avant** (PaddleOCR/PaddlePaddle en plus de
torch/transformers) -- plusieurs Go au total, compter un vrai moment
selon la connexion. Voir la section de compatibilité plus haut : aucune
compilation locale n'est censée se déclencher pour les paquets qui y sont
listés -- si `pip install` tente de COMPILER l'un d'eux (pas juste
télécharger une wheel), c'est le premier signe à me signaler avec le
message d'erreur complet.

## 6. Vérifier que l'appli tourne avant de packager

```bash
python main.py
```

Si la fenêtre s'ouvre (pastilles rouge/jaune/verte en haut à gauche façon
Mac, thème sombre, hall d'accueil avec trois boutons), tout va bien.
Sinon, garder le message d'erreur complet affiché dans le Terminal.

**Test réel, pas juste visuel** -- avant de passer à l'empaquetage,
essaie au moins :
- **Extraire avec analyse** sur un petit PDF, avec le modèle OCR par
  défaut (PaddleOCR) -- c'est la plus grosse nouveauté jamais testée
  sur Mac. Une erreur ici ressemblerait probablement à l'un des trois
  pièges déjà rencontrés côté Windows et documentés dans `SPEC.md`
  (§5 vicies, vicies sexies, vicies septies) -- utile de me les
  transmettre tels quels, la cause est probablement analogue (fichier
  ou métadonnée que PyInstaller ne détecte pas tout seul), même si le
  correctif exact peut différer sur Mac.
- Une petite traduction avec le modèle **600M** par défaut.
- La page **Paramètres** → « Analyser » (diagnostic matériel) -- doit
  maintenant afficher « Metal/MPS » si ta puce Apple Silicon est
  utilisable par PyTorch (voir §5 tricies quater dans `SPEC.md`, ajouté
  le 27/08/2026 -- CUDA n'existe que sur du matériel NVIDIA, jamais sur
  Mac ; MPS est le vrai équivalent Apple Silicon, câblé pour la première
  fois ici, **non testé sur un vrai Mac au moment d'écrire ce guide**).
  Si ça affiche encore « aucun GPU détecté » sur ta machine, transmets le
  résultat exact -- utile pour ajuster.

## 7. Construire le `.app`

D'abord la date de build :
```bash
python scripts/stamp_build_date.py
```

Puis, **avec `TRANSLAX.spec`** (plus la commande `pyinstaller
--onefile --windowed ...` de l'ancienne version de ce guide -- ce fichier
est maintenant la seule source de vérité pour la construction, sur les
deux systèmes) :
```bash
pyinstaller TRANSLAX.spec --noconfirm
```

Ce fichier détecte automatiquement qu'il tourne sur macOS
(`sys.platform == 'darwin'`) et :
- choisit `ui/icon.icns` au lieu de `ui/icon.ico` pour l'icône ;
- ajoute un bloc `BUNDLE(...)` qui emballe le tout en un vrai
  `TRANSLAX.app` (Info.plist, icône Dock) -- **ce bloc précis n'a jamais
  tourné sur un vrai Mac, c'est la partie la plus susceptible de
  réclamer un ajustement**, transmets le message d'erreur complet s'il
  échoue ;
- reprend tels quels les trois correctifs de packaging PaddleOCR
  découverts côté Windows (fichiers de config manquants, métadonnées de
  dépendances, bibliothèques natives) -- ils s'appuient sur des
  utilitaires PyInstaller qui s'adaptent déjà à l'OS réel (ex. cherchent
  des `.dylib` sur Mac au lieu de `.dll`), donc aucune raison évidente
  qu'ils ne s'appliquent pas pareil ici, mais non confirmé avant un vrai
  test.

Résultat attendu : `dist/TRANSLAX.app`.

## 8. Tester le `.app`

```bash
open dist/TRANSLAX.app
```

Ou double-clic depuis le Finder. À vérifier :
- icône TX dans le Dock, thème sombre correctement appliqué ;
- en bas du hall d'accueil : **« TRANSLAX v1.19.0 »** et la date du jour
  de cette construction ;
- refaire les mêmes tests réels qu'à l'étape 6, mais cette fois depuis le
  `.app` construit (pas `python main.py`) -- extraction PaddleOCR,
  traduction 600M, page Paramètres ;
- **sélecteur de modèle** : 6 profils (600M, 1.3B, 3.3B, 600M-ct2 Turbo,
  OPUS-MT, MADLAD-400) -- Turbo (CTranslate2) et OPUS-MT (sacremoses)
  sont les deux autres dépendances compilées/spécifiques jamais testées
  sur Mac (voir tableau de compatibilité plus haut pour CTranslate2 --
  `sacremoses`, lui, est pur Python) ;
- la liste de reprise, Pause/Stop, les boutons ⓘ -- tout ce qui est pur
  Python/Qt et déjà testé côté Windows n'a aucune raison de se comporter
  différemment ici, mais un coup d'œil rapide ne coûte rien ;
- **GPU Metal/MPS** (§5 tricies quater) : lance une traduction avec
  Précis, OPUS-MT ou MADLAD-400 (jamais Turbo -- toujours CPU par
  conception, voir plus haut) et regarde si c'est sensiblement plus
  rapide qu'en CPU pur -- c'est le vrai test, la page Paramètres affichant
  « Metal/MPS » confirme seulement que PyTorch VOIT le GPU, pas qu'il
  s'en sert sans erreur pendant une vraie traduction.

## Une limite connue sur Mac, pas un bug à corriger maintenant

**Mise à jour intégrée** (« Chercher une mise à jour », page Paramètres)
: fonctionne aujourd'hui uniquement sur Windows -- elle cherche
spécifiquement un fichier `TRANSLAX-Setup-*.exe` dans les Releases
GitHub et lance des commandes propres à l'installeur Windows (Inno
Setup) pour se remplacer elle-même. Sur ce Mac, cliquer dessus dira
probablement qu'aucune mise à jour n'est trouvée (l'unique fichier publié
est le `.exe` Windows), ce qui est le comportement ACTUEL attendu, pas
une erreur à corriger dans l'immédiat. Whatever tu construis ici doit
pour l'instant être remplacé à la main (répéter §4 → §8) à chaque
nouvelle version, exactement comme avant.

*(L'ancienne deuxième limite listée ici -- le diagnostic matériel qui ne
détectait que CUDA, jamais MPS -- est corrigée depuis le 27/08/2026, voir
§5 tricies quater dans `SPEC.md`. Toujours à confirmer pour de vrai sur
cette machine, voir le test GPU Metal/MPS à l'étape 8 ci-dessus.)*

## En cas de blocage

Les pièges déjà rencontrés côté Windows sont dans `SPEC.md` (chercher
« PyInstaller », « packaging », ou les sections dédiées à PaddleOCR --
§5 vicies et suivantes) -- certains ne s'appliquent qu'à Windows
(verrouillage de fichier .exe), d'autres sont universels. Pour toute
erreur, copier le message COMPLET affiché dans le Terminal (pas un
résumé) -- que ce soit ici dans ce chat Windows, ou directement à Claude
dans la session VS Code de ce Mac, qui a accès au même code et peut
itérer sur place sans aller-retour.
