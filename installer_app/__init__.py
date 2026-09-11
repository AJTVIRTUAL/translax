"""
Installeur Windows de TRANSLAX -- entièrement codé pour ce projet, PySide6,
demande explicite de l'utilisateur (11/09/2026) : « je veux pas que ce soit
celui natif de Windows, je veux que ce soit codé tout par [nous] ».

Remplace l'installeur Inno Setup précédent (`installer/translax.iss`,
toujours dans le dépôt mais plus utilisé -- voir SPEC.md) : plus de fenêtre
Windows classique (barre de titre native, boutons système, coins arrondis
gris) -- la même identité visuelle que TRANSLAX lui-même (thème sombre,
accent bleu, coins carrés, barre de titre personnalisée, voir
`ui/titlebar.py` et `ui/styles.qss`, réellement réutilisés ici, pas
réinventés).

Deux exécutables distincts, construits depuis ce même code (voir
`scripts/build_installer.py`) :
  - le SETUP (« TRANSLAX-Setup-X.Y.Z.exe ») embarque `dist/TRANSLAX.exe`
    (~500 Mo) comme donnée PyInstaller ;
  - le DÉSINSTALLEUR (« TRANSLAX-Uninstall.exe »), sans ce payload, copié
    dans le dossier d'installation par le Setup et référencé par l'entrée
    de désinstallation Windows (Panneau de configuration / Paramètres).

Voir `installer_app/operations.py` pour la mécanique réelle (copie de
fichiers, raccourcis, registre), `installer_app/ui.py` pour les fenêtres,
`installer_app/main.py` pour le point d'entrée et les modes (interactif /
silencieux pour la mise à jour intégrée / désinstallation).
"""
