# Construire TRANSLAX sur macOS

Ce document existe parce que PyInstaller **ne fait pas de compilation
croisée** : il empaquette l'interpréteur Python natif et les bibliothèques
compilées (torch, Qt) de la plateforme sur laquelle il tourne. Le `.exe`
Windows a été construit sur une machine Windows ; ce `.app` macOS doit être
construit **sur un vrai Mac**, en suivant ces étapes.

> **Honnêteté avant tout** : ce guide est écrit à partir d'un code déjà
> validé sur Windows et de bibliothèques (Qt, PyInstaller) connues pour
> être multiplateformes — mais rien ici n'a été testé sur un vrai Mac,
> faute d'accès à un. Tu seras le premier vrai test. Si une étape échoue,
> garde le message d'erreur complet : c'est ce qu'il faut pour corriger.

**Ta machine** : MacBook Pro 14" 2026 (M5) → puce Apple Silicon (arm64).
Le build produit sur ce Mac ne tournera que sur des Mac Apple Silicon —
PyTorch publie des versions séparées pour Apple Silicon et Intel, et les
combiner en un seul binaire universel n'est pas fiable pour un projet avec
autant de dépendances lourdes. Pas un problème pour toi : tu construis et tu
utilises sur la même machine.

Ce guide part du principe que tu n'as **jamais utilisé Python ni le
Terminal sur Mac** — chaque étape est écrite pour ça, rien n'est supposé
acquis.

---

## 0. Déjà installé une fois ? Mettre à jour plutôt que tout refaire

Si Python 3.13, Xcode Command Line Tools et l'environnement `.venv` du
projet sont déjà en place sur ce Mac (première installation déjà faite en
suivant ce guide), **passer directement aux étapes 4 à 8** :

- **Étape 4** (récupérer le projet) : remplacer le dossier du projet par la
  nouvelle version — **sauf `.venv/`**, qui doit rester celui déjà créé sur
  ce Mac (celui d'une copie fraîche depuis Windows ne fonctionnerait pas
  ici). `build/` et `dist/` peuvent être écrasés sans risque, ils sont
  entièrement régénérés à l'étape 7.
- **Étape 5** (créer l'environnement) : réactiver l'environnement existant,
  **puis, cette fois-ci, relancer quand même `pip install`** -- plusieurs
  nouvelles bibliothèques ont été ajoutées à `requirements.txt` depuis ta
  dernière installation (`anthropic`/`httpx2` pour Traduire X, `ctranslate2`
  pour le moteur Turbo, `sacremoses` pour OPUS-MT -- voir plus bas) :
  ```bash
  cd chemin/vers/TRANSLAX
  source .venv/bin/activate
  pip install -r requirements.txt
  ```
  Cette commande ne retélécharge PAS ce qui est déjà installé et à la
  bonne version (torch, transformers...) -- seules les bibliothèques
  manquantes seront ajoutées. Rien à craindre à la relancer, même par
  prudence, à chaque mise à jour future.

  **Point d'attention réel, pas juste théorique, pour `ctranslate2`** :
  contrairement aux autres bibliothèques du projet, celle-ci embarque du
  code compilé (pas du pur Python) -- vérifié que des wheels précompilées
  existent bien pour macOS ARM64 sur PyPI (donc `pip install` devrait
  fonctionner sans compiler quoi que ce soit toi-même), mais ce point
  précis n'a jamais été testé sur un vrai Mac Apple Silicon. Si cette ligne
  de `pip install -r requirements.txt` échoue précisément sur `ctranslate2`
  (message contenant ce nom), c'est la première chose à me signaler avec le
  message d'erreur complet.
- **Étapes 6 à 8** : inchangées — vérifier que l'appli se lance
  (`python main.py`), reconstruire (§7, en n'oubliant pas
  `python scripts/stamp_build_date.py` avant, sinon la date en bas de la
  fenêtre resterait celle de l'ancienne version), puis remplacer l'ancien
  `TRANSLAX.app` par le nouveau dans `dist/`.

### Nouveautés à builder cette fois (25/08/2026 — version 1.6.0)

Depuis ta dernière version installée sur ce Mac (1.3.0), TRANSLAX a
gagné :
- un dossier de sortie et une reprise de traduction proposée
  automatiquement au démarrage, tous deux mémorisés d'un lancement à
  l'autre ;
- le titre du fichier traduit dès le premier segment écrit (plus
  seulement à la toute fin) ;
- un bouton **Reboost** pour vérifier qu'une traduction avance toujours,
  sans jamais rien interrompre ;
- un bouton **Traduire X**, qui corrige les scans PDF mal reconnus par
  vision IA (Claude) avant de traduire -- nécessite internet et une clé
  API Anthropic personnelle, à entrer dans Réglages une fois l'app
  lancée (§8 en reparle) ; une page bloquée par le filtre de contenu
  d'Anthropic n'interrompt plus tout le livre (corrigé depuis) ;
- une case **Extraction seulement**, qui réutilise Traduire/Traduire X
  pour juste récupérer un texte nettoyé, sans le traduire ;
- l'import de fichiers **`.epub`**, en plus de PDF/TXT/MD ;
- **trois nouveaux profils dans le sélecteur de modèle** (le sélecteur en
  propose maintenant 6 au total) :
  - **600M — Turbo (CTranslate2)** : les mêmes poids NLLB-200-600M, servis
    par un moteur d'inférence optimisé -- mesuré ~13× plus rapide sur la
    machine Windows de développement, jamais mesuré sur Apple Silicon ;
  - **OPUS-MT** et **MADLAD-400** : deux moteurs de traduction
    **différents** de NLLB (pas des variantes), ajoutés spécifiquement
    parce qu'ils ont une licence commerciale propre (CC-BY 4.0 / Apache
    2.0) -- **point important si ce Mac sert un jour à construire une
    version destinée à être vendue** : les 4 profils NLLB (dont Turbo)
    restent sous licence CC-BY-NC, usage commercial interdit, gardés
    uniquement pour un usage personnel (voir `SPEC.md` §5 quaterdecies/
    quindecies pour le détail complet). MADLAD-400 pèse à lui seul ~11,8 Go
    à télécharger au premier usage -- prévoir une bonne connexion avant de
    tester ce profil précis.

Tout ce code est en pur Python, portable comme le reste -- seule
`ctranslate2` (moteur Turbo) embarque du code compilé propre à chaque OS,
voir l'avertissement juste au-dessus.

## 1. Ouvrir le Terminal

Le Terminal est l'application où on tape les commandes de ce guide. Pour
l'ouvrir :

- **Cmd + Espace** (ouvre Spotlight), taper `Terminal`, appuyer sur Entrée ;
- ou Finder → Applications → Utilitaires → Terminal.

Une fenêtre noire (ou blanche selon les réglages) avec du texte s'ouvre :
c'est là que toutes les commandes `bash`/`zsh` de ce document se tapent,
une à la fois, suivies d'Entrée.

**Deux réflexes utiles pour la suite** :
- `pwd` affiche le dossier où tu te trouves actuellement.
- `ls` liste les fichiers de ce dossier.
- `cd NomDuDossier` entre dans un dossier ; `cd ..` remonte d'un niveau.
- **Astuce** : au lieu de taper un chemin à la main, tape `cd ` (avec
  l'espace) puis **glisse le dossier depuis le Finder directement dans la
  fenêtre du Terminal** — le chemin complet s'écrit tout seul.

## 2. Installer les outils de développement Apple (Xcode Command Line Tools)

Certains paquets Python ont besoin d'un compilateur pour s'installer. Une
seule commande, à taper dans le Terminal :

```bash
xcode-select --install
```

Une fenêtre s'ouvre pour proposer l'installation → cliquer **Installer**,
accepter la licence. Ça prend quelques minutes et une connexion internet.
Si le Terminal répond que c'est déjà installé, tant mieux, rien à faire.

## 3. Installer Python 3.13

macOS n'installe plus forcément Python par défaut — et même quand une
version est présente, elle n'est pas destinée à un usage comme celui-ci.
On installe une version dédiée.

**Vérifier d'abord ce qui existe déjà** (juste par curiosité, ça ne change
rien à la suite) :

```bash
python3 --version
```

> **Si cette commande répond `Python 3.14.x`** (ou toute version plus
> récente que 3.13) : ce n'est pas la version à utiliser pour TRANSLAX,
> et ce n'est **pas une question de préférence** — vérifié précisément :
> les versions exactes de `torch` et `PySide6` figées dans
> `requirements.txt` **n'ont tout simplement aucune version compilée pour
> Python 3.14** sur PyPI (`pip install` s'arrêterait en échec dessus,
> même en réessayant). Les autres bibliothèques du projet n'auraient posé
> aucun souci — seules ces deux-là bloquent, mais ça suffit à empêcher
> l'installation.
>
> **Pas la peine de désinstaller le 3.14** — les deux versions cohabitent
> très bien sur Mac, chacune sous son propre nom de commande
> (`python3.14`, `python3.13`). Il suffit d'installer le 3.13 en plus, et
> de dire explicitement au projet d'utiliser celui-là (fait à l'étape 5
> ci-dessous, avec `python3.13` au lieu de `python3`).

**Installer Python 3.13** (même version que celle utilisée et testée côté
Windows, pour rester dans un terrain connu) :

1. Aller sur **https://www.python.org/downloads/macos/** dans un
   navigateur.
2. Cette page affiche en général la dernière version (3.14 ou plus) en
   avant — chercher plus bas un lien vers les **anciennes versions**
   (« Looking for a specific release? » / liste des releases), et prendre
   la dernière révision de la branche **3.13** (ex. 3.13.7 ou plus récent
   si disponible) — pas 3.14.
3. Sur la page de cette version, télécharger le lien **« macOS 64-bit
   universal2 installer »** (il fonctionne aussi bien sur ta puce M5 que
   sur un Mac Intel, pas besoin de choisir).
4. Ouvrir le fichier `.pkg` téléchargé (double-clic, généralement dans le
   dossier Téléchargements) et suivre l'installateur : Continuer →
   Continuer → Accepter la licence → Installer. Le mot de passe de session
   Mac sera demandé (normal, c'est une vraie installation système).

5. **Étape à ne surtout pas sauter** : une fois l'installation terminée,
   ouvrir le Finder → Applications → dossier **« Python 3.13 »**, et
   double-cliquer sur **`Install Certificates.command`**. Une fenêtre de
   Terminal s'ouvre toute seule, affiche du texte, puis se ferme.

   > Pourquoi c'est important : sans cette étape, Python installé via
   > python.org sur Mac n'a pas les certificats de sécurité nécessaires
   > pour se connecter en HTTPS — et c'est exactement ce que fait TRANSLAX
   > pour télécharger les modèles NLLB depuis huggingface.co. Sans ce
   > script, le téléchargement de modèle échouerait avec une erreur de
   > certificat SSL, uniquement sur Mac, jamais sur Windows. C'est un
   > piège connu et systématique de Python sur Mac, pas spécifique à
   > TRANSLAX — mais autant l'éviter dès maintenant.

6. **Vérifier que tout est bien installé** — fermer complètement le
   Terminal et le rouvrir (important, sinon il ne voit pas encore la
   nouvelle installation), puis, bien avec `python3.13` (pas `python3`,
   qui pointera probablement toujours vers le 3.14 déjà présent) :

   ```bash
   python3.13 --version
   ```

   Doit répondre `Python 3.13.x` sans erreur. Si `python3.13` reste
   introuvable après avoir rouvert le Terminal, redémarrer le Mac règle
   presque toujours le problème (le PATH du shell doit être rechargé).

## 4. Récupérer le projet sur le Mac

Copie tout le dossier `PROJECTS/TRANSLAX/` sur le Mac (clé USB, AirDrop,
partage réseau, peu importe le moyen), **sauf** ces trois dossiers propres
à Windows (à ne pas copier, ou à supprimer une fois copiés — ils seront
régénérés) :

```
.venv/      <- environnement Python Windows, inutilisable sur Mac
build/      <- cache de compilation PyInstaller, spécifique à l'OS
dist/       <- contient TRANSLAX.exe (Windows), pas utile ici
```

Tout le reste (`core/`, `ui/`, `main.py`, `cli.py`, `requirements.txt`,
`tests/`) est du code Python pur ou des ressources (`.qss`, `.ico`, `.icns`)
— déjà portable, aucune adaptation de code n'a été nécessaire.

Une fois copié, dans le Terminal, se déplacer dans ce dossier (rappel de
l'astuce du §1 : `cd ` + glisser le dossier depuis le Finder) :

```bash
cd chemin/vers/TRANSLAX
```

## 5. Créer l'environnement Python du projet

Un environnement virtuel isole les bibliothèques de TRANSLAX du reste du
système — bonne pratique standard, pas spécifique à ce projet.

```bash
python3.13 -m venv .venv
source .venv/bin/activate
```

**`python3.13` et pas `python3`** : c'est ce qui garantit que ce projet
utilise la version 3.13 même si Python 3.14 (ou une version plus récente)
est aussi installée sur la machine et serait sinon utilisée par défaut —
voir l'avertissement de l'étape 3.

Après la deuxième commande, l'invite du Terminal doit afficher `(.venv)`
au début de la ligne — signe que l'environnement est actif.
**Cette commande `source .venv/bin/activate` est à retaper à chaque fois
qu'un nouveau Terminal est ouvert pour retravailler sur ce projet.**

Une fois l'environnement activé, vérifier la version qu'il utilise
réellement (doit afficher 3.13.x, plus la peine de préciser `.13` ensuite
— tant que `(.venv)` reste affiché, `python` et `pip` pointent
automatiquement sur cet environnement) :

```bash
python --version
```

Puis installer les bibliothèques du projet (celles listées dans
`requirements.txt`, mêmes versions que celles testées côté Windows) :

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

Ça télécharge plusieurs centaines de Mo (torch et transformers sont gros)
— compter quelques minutes selon la connexion.

## 6. Vérifier que l'appli tourne avant de packager

```bash
python main.py
```

Si la fenêtre s'ouvre correctement (pastilles rouge/jaune/verte en haut à
gauche façon Mac, thème sombre, titre centré), tout va bien — passer à
l'étape 7. Si quelque chose cloche visuellement ou plante, garder le
message d'erreur affiché dans le Terminal.

## 7. Construire le `.app`

D'abord régénérer la date de build affichée en bas de la fenêtre (voir
SPEC.md §5 ter) — sinon elle garde la date du dernier `git pull`/copie du
projet, pas celle de cette construction :

```bash
python scripts/stamp_build_date.py
```

Puis construire :

```bash
pyinstaller --onefile --windowed --name TRANSLAX --icon ui/icon.icns \
    --add-data "ui/styles.qss:ui" --add-data "ui/icon.ico:ui" main.py
```

**Piège à ne pas reproduire** : le séparateur de `--add-data` est un **`:`
(deux-points) sur Mac/Linux**, pas un `;` (point-virgule) comme sur
Windows. C'est la seule vraie différence avec la commande utilisée côté
Windows (documentée dans `SPEC.md` §8).

Pourquoi `icon.icns` **et** `icon.ico` ensemble :
- `--icon ui/icon.icns` : icône du `.app` vue dans le Finder/Dock — doit
  être au format `.icns`, c'est une exigence d'Apple.
- `--add-data ".../icon.ico"` : c'est le fichier que l'appli **elle-même**
  charge au lancement pour `app.setWindowIcon(...)` (`main.py`). Qt sait
  lire un `.ico` sur n'importe quel OS (ce n'est pas une icône système
  Windows, juste un format d'image que Qt décode lui-même) — pas besoin de
  dupliquer cette logique pour le `.icns`.

Résultat attendu : `dist/TRANSLAX.app` (PyInstaller crée automatiquement un
vrai bundle `.app` quand `--windowed` est utilisé sur macOS, pas un simple
exécutable nu).

> **Trois modules importés seulement à l'intérieur d'une fonction, pas en
> haut du fichier** (`anthropic`/`httpx2` dans `core/vision_ocr.py`,
> `ctranslate2` dans `core/translate.py::FastEngine`, `sacremoses` utilisé
> en interne par le tokenizer d'OPUS-MT) -- même principe que
> `torch`/`transformers`, déjà importés ainsi depuis le début et qui
> s'empaquettent sans souci. PyInstaller analyse le code de tout le
> fichier, pas seulement son début, donc ça devrait s'empaqueter pareil,
> sans rien ajouter à la commande ci-dessus.
>
> **Vérifié pour de vrai côté Windows avant d'écrire ce paragraphe** (pas
> juste supposé, l'inverse de ce que dit ce genre d'avertissement pour
> les versions précédentes) : `PyInstaller.utils.cliutils.archive_viewer`
> sur le `.exe` construit aujourd'hui montre `anthropic`, `httpx2`,
> `sacremoses` et `joblib` bien présents dans l'archive Python compressée,
> et le fichier binaire compilé `ctranslate2.dll` bien présent au
> décompactage à l'exécution -- les quatre sans avoir eu besoin d'un seul
> `--hidden-import`. Ce qui reste réellement une inconnue sur Mac : le
> binaire compilé de `ctranslate2` est un `.dylib` sur Mac, PAS le même
> fichier que le `.dll` Windows vérifié ici (même code source, compilé
> différemment) -- c'est ce point précis, et lui seul, qui n'a aucune
> garantie tirée du test Windows. Si le test de l'étape 8 plus bas échoue
> avec un `ModuleNotFoundError` (n'importe lequel de ces quatre noms),
> relancer la construction avec le ou les `--hidden-import` correspondants :
> ```bash
> pyinstaller --onefile --windowed --name TRANSLAX --icon ui/icon.icns \
>     --add-data "ui/styles.qss:ui" --add-data "ui/icon.ico:ui" \
>     --hidden-import anthropic --hidden-import httpx2 \
>     --hidden-import ctranslate2 --hidden-import sacremoses main.py
> ```

## 8. Tester le `.app`

```bash
open dist/TRANSLAX.app
```

Ou double-clic depuis le Finder. Vérifier :
- l'icône TX apparaît dans le Dock ;
- le thème sombre est bien appliqué (si l'appli s'ouvre en gris/blanc
  générique, le `--add-data` du `.qss` n'a pas fonctionné) ;
- tout en bas de la fenêtre, le numéro de version (1.6.0) et la date du
  jour de cette construction (pas une ancienne date) ;
- glisser un fichier PDF/EPUB/TXT/MD, choisir un modèle, traduire — et si
  un modèle non téléchargé est choisi, vérifier que le téléchargement
  démarre sans erreur de certificat SSL (voir §3, point 4, si ça bloque) ;
- **le sélecteur de modèle propose bien 6 profils** : 600M, 1.3B, 3.3B,
  600M Turbo, OPUS-MT, MADLAD-400 ;
- **choisir le profil Turbo (600M-ct2)** pour un petit texte de test : la
  conversion locale au format CTranslate2 se déclenche au premier usage
  (message affiché, ~1 minute), puis la traduction se termine sans
  `ModuleNotFoundError: ctranslate2` -- c'est le test le plus important de
  cette liste, le seul point non couvert par la vérification Windows
  (voir le paragraphe du §7 sur `.dylib` vs `.dll`) ;
- **choisir OPUS-MT** pour un texte anglais → français : pas de
  `ModuleNotFoundError: sacremoses`, traduction obtenue (le phrasé peut
  légitimement différer de NLLB, voir `SPEC.md` §5 quaterdecies -- ce n'est
  pas un défaut) ;
- **MADLAD-400** : à tester seulement si tu as une bonne connexion et le
  temps -- ~11,8 Go à télécharger au premier usage, jamais mesuré en
  vitesse réelle même côté Windows ;
- pendant une traduction, le bandeau anti-veille apparaît, et le bouton
  **Reboost** répond sans rien interrompre ;
- la case **Extraction seulement** relibelle bien Traduire en Extraire
  (et Traduire X en Extraire X) ;
- pour **Traduire X** : coller une clé API Anthropic personnelle dans
  Réglages (créée sur console.anthropic.com si tu n'en as pas) avant de
  cliquer dessus -- sans clé, un message l'indique clairement plutôt que
  de planter. Voir le paragraphe du §7 si l'erreur est
  `ModuleNotFoundError: anthropic`.

## 9. Barre de titre : adaptée à la convention Mac

`ui/titlebar.py` détecte `sys.platform == "darwin"` et choisit une
présentation différente **sans toucher au code Windows** :

- pastilles rouge (fermer) / jaune (réduire) / verte (agrandir) alignées
  à **gauche**, dans l'ordre natif Mac ;
- glyphe (×, −, +) qui apparaît au survol de chaque pastille ;
- titre « TRANSLAX » **centré**, pas d'icône dans la barre (le Dock en
  tient déjà lieu sur Mac).

Glisser la barre, redimensionner depuis les bords et agrandir/restaurer en
double-cliquant fonctionnent avec le même code que sous Windows (API Qt
multiplateformes `startSystemMove`/`startSystemResize`) — rien de
spécifique à écrire pour ça.

**Simplification assumée** : le vrai macOS révèle les trois glyphes en
même temps dès qu'on survole le groupe de pastilles ; ici chaque pastille
ne révèle que la sienne au survol direct. Si ça te gêne visuellement une
fois testé, dis-le — c'est un ajustement possible, pas fait par défaut
pour rester simple.

**Vérifié uniquement par simulation sur une machine Windows** (en forçant
`IS_MAC = True` pour prévisualiser) : positionnement des pastilles, titre
centré, et clics fonctionnels confirmés par capture d'écran et test
automatisé. Le survol réel (glyphe qui apparaît sous le curseur) n'a pas pu
être capturé de façon fiable par automatisation à distance sur ce Windows,
mais le mécanisme sous-jacent (`enterEvent`/`leaveEvent` → repeinture) a
été confirmé correct via un journal de débogage. **Premier vrai test sur
un Mac réel : le tien.**

## 10. Où TRANSLAX range ses fichiers sur Mac

Le téléchargement des modèles fonctionne exactement comme sur Windows :
`huggingface_hub` va chercher en HTTPS sur huggingface.co au premier usage
d'un modèle (NLLB, OPUS-MT, MADLAD-400 — les trois passent par ce même
mécanisme), range le résultat dans `~/.cache/huggingface/hub` (chemin Mac
équivalent au `C:\Users\...\...\huggingface` de Windows), et le réutilise
ensuite. Aucune étape ni fichier supplémentaire à gérer — à condition
d'avoir fait l'étape 3.4 (certificats SSL) plus haut.

**Nouveau depuis la 1.3.0, propre au profil Turbo** : la conversion locale
au format CTranslate2 (voir `core/translate.py::FastEngine`) range son
résultat dans `~/Library/Application Support/TRANSLAX/ctranslate2-models/`
(équivalent Mac du `%APPDATA%\TRANSLAX\ctranslate2-models\` de Windows,
voir `core/settings.py`) -- un dossier séparé du cache HuggingFace
ci-dessus, propre à TRANSLAX. Les réglages (dossier de sortie mémorisé,
clé API Anthropic, dernier job) vivent dans le même dossier de base
(`.../TRANSLAX/settings.json`).

## En cas de blocage

Les pièges déjà rencontrés côté Windows (et leurs solutions) sont dans
`SPEC.md` §10 — certains ne s'appliquent qu'à Windows (verrouillage de
fichier .exe), d'autres sont universels (batching qui dégrade la
traduction, etc.). Si PyInstaller échoue sur une dépendance précise
(souvent `torch` ou `transformers`, très gourmands en hooks), le message
d'erreur nomme presque toujours le module en cause — utile à me
transmettre pour que je propose le bon correctif. Pareil pour toute erreur
affichée dans le Terminal à n'importe quelle étape : copier le message en
entier plutôt que de le résumer, ça évite les allers-retours.
