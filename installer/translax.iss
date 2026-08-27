; Installeur Windows de TRANSLAX (Inno Setup 6) -- demande explicite de
; l'utilisateur, 26/08/2026 : "un installateur multi-étape simple mais
; nécessaire à ce que chacun puisse avoir ça sur leur machine".
;
; Installation PAR UTILISATEUR (pas par machine) : aucun droit
; administrateur requis (PrivilegesRequired=lowest) -- pour que n'importe
; qui puisse installer TRANSLAX sans avoir à demander à un administrateur,
; conformément à "que chacun puisse avoir ça sur leur machine".
;
; Ne construit PAS l'exe lui-même : suppose dist\TRANSLAX.exe déjà
; construit et à jour (voir scripts/build_installer.py, qui vérifie ça
; avant d'appeler ce script). La version vient du paramètre de
; préprocesseur MyAppVersion (passé par scripts/build_installer.py depuis
; core/version.py -- une seule source de vérité, jamais recopiée à la
; main ici).
;
; Étapes de l'assistant, dans l'ordre (le comportement standard d'Inno
; Setup EST déjà l'installeur "multi-étape" demandé, pas besoin de pages
; personnalisées pour un premier jet simple) : Bienvenue -> Dossier de
; destination -> Icône sur le Bureau (case à cocher) -> Prêt à installer
; -> Installation -> Fin (avec case "lancer maintenant").

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

#define MyAppName "TRANSLAX"
#define MyAppPublisher "AJTWS - Amilcar Joao"
#define MyAppExeName "TRANSLAX.exe"

[Setup]
; GUID fixe et propre à ce projet (généré une seule fois) -- permet à
; Inno Setup de reconnaître une INSTALLATION EXISTANTE lors d'une mise à
; jour (même AppId), plutôt que d'en installer une deuxième à côté.
AppId={{58A331BB-FF3E-45BC-931E-B02E02038A70}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
VersionInfoVersion={#MyAppVersion}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist_installer
OutputBaseFilename=TRANSLAX-Setup-{#MyAppVersion}
SetupIconFile=..\ui\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
; L'exe fait plus de 500 Mo (moteurs de traduction + PaddleOCR embarqués,
; voir SPEC.md) -- Inno Setup découpe normalement au-delà de 2 Go, sans
; effet ici, mais réglé explicitement pour ne jamais surprendre si les
; modèles embarqués grossissent encore.
DiskSpanning=no

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\TRANSLAX.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
