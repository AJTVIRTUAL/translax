# Construire l'installeur Windows de TRANSLAX

Ce document explique comment produire `TRANSLAX-Setup-<version>.exe`, le
fichier qu'on donne à quelqu'un pour qu'il installe TRANSLAX sur sa propre
machine -- distinct de `dist\TRANSLAX.exe` (l'exécutable brut, qu'on lance
directement sans installation, celui utilisé pendant le développement).

## Pourquoi un installeur, et pourquoi Inno Setup

L'exécutable brut fonctionne très bien pour toi (tu sais où le mettre, comment
le relancer, où sont ses fichiers). Pour "que chacun puisse avoir ça sur leur
machine" (demande explicite, 26/08/2026), il faut quelque chose de plus
classique : un assistant d'installation avec des étapes (dossier de
destination, icône sur le Bureau, etc.), qui range proprement le programme,
crée ses raccourcis, et propose une vraie désinstallation depuis les
paramètres Windows.

**Inno Setup** a été choisi parmi les outils disponibles (l'alternative la
plus connue étant NSIS) : gratuit, open source, le plus utilisé pour ce genre
de logiciel, et son fichier de script (`.iss`) reste lisible même sans
connaître l'outil à l'avance. Il n'était pas installé sur cette machine -- il
l'a été spécifiquement pour ce projet (installeur officiel téléchargé depuis
`jrsoftware.org`, version 6.7.3), et reste disponible pour reconstruire
l'installeur à chaque nouvelle version de TRANSLAX.

## Ce que l'installeur fait (et ne fait pas)

- Installe TRANSLAX **pour l'utilisateur courant uniquement**, sans droit
  administrateur (`%LocalAppData%\Programs\TRANSLAX`) -- pour que n'importe
  qui puisse l'installer sans avoir à demander à un administrateur.
- Crée un raccourci dans le menu Démarrer, et propose (case à cocher) un
  raccourci sur le Bureau.
- Enregistre une vraie entrée de désinstallation (visible dans
  *Paramètres → Applications*).
- Propose de lancer TRANSLAX immédiatement après l'installation.
- Ne construit PAS l'exécutable lui-même : il faut que `dist\TRANSLAX.exe`
  existe déjà et soit à jour (voir plus bas) avant de lancer l'installeur.
- Ne signe PAS numériquement l'exécutable (voir *Windows SmartScreen*
  plus bas) -- une signature de code coûte de l'argent et n'est pas encore
  en place.

## Construire l'installeur, étape par étape

**Prérequis** : Inno Setup 6 installé (`ISCC.exe`, normalement dans
`C:\Program Files (x86)\Inno Setup 6\`). S'il manque, le télécharger depuis
<https://jrsoftware.org/isdl.php> et l'installer normalement (assistant
classique, aucune option particulière à changer).

1. **Construire l'exécutable, comme d'habitude** :
   ```
   python scripts/stamp_build_date.py --bump <patch|minor>
   python -m PyInstaller TRANSLAX.spec --noconfirm
   ```
   (fermer TRANSLAX au préalable s'il tourne déjà -- sinon la construction
   échoue, le fichier étant verrouillé).

2. **Construire l'installeur** :
   ```
   python scripts/build_installer.py
   ```
   Ce script vérifie que `dist\TRANSLAX.exe` existe, lit la version depuis
   `core/version.py` (jamais à recopier à la main), puis appelle Inno Setup.
   Le résultat apparaît dans `dist_installer\TRANSLAX-Setup-<version>.exe`.

3. **Tester l'installeur avant de le distribuer** -- le lancer normalement
   (double-clic), suivre l'assistant, vérifier que TRANSLAX se lance
   correctement une fois installé, puis le désinstaller depuis *Paramètres →
   Applications* et vérifier que le dossier d'installation a bien disparu.

## Windows SmartScreen : à quoi s'attendre

Un exécutable non signé numériquement (c'est le cas ici) déclenche souvent un
avertissement Windows SmartScreen ("Windows a protégé votre ordinateur") au
premier lancement de l'installeur, sur la machine de quelqu'un d'autre --
normal et attendu pour un logiciel distribué sans certificat de signature de
code (qui est payant, généralement plusieurs centaines d'euros par an). La
personne peut cliquer sur *Informations complémentaires → Exécuter quand
même*. Signer le programme supprimerait cet avertissement, mais c'est une
dépense à part, pas encore mise en place -- une piste pour plus tard si la
distribution s'élargit.

## Fichiers concernés

- `installer/translax.iss` -- le script Inno Setup lui-même.
- `scripts/build_installer.py` -- l'automatise (lit la version, vérifie
  que l'exe existe, appelle `ISCC.exe`).
- `dist_installer/` -- où atterrissent les installeurs construits (à
  exclure d'un futur dépôt Git, comme `dist/` et `build/`, si ce projet
  en obtient un un jour).
