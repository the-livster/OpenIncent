; Inno Setup script for icm-engine desktop installer
; Requires Inno Setup 6+ (free: https://jrsoftware.org/isinfo.php)
;
; Usage:
;   1. Build the exe:   python desktop/build.py
;   2. Open this file in Inno Setup Compiler
;   3. Click Compile
;   4. Output: desktop/dist/icm-engine-setup.exe

#define MyAppName "ICM Engine"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "OpenIncent"
#define MyAppURL "https://github.com/openincent/icm-engine"
#define MyAppExeName "icm-engine.exe"

[Setup]
AppId={{B8F4A3D2-7E6C-4A1B-9D5F-8C3E2A7B1D4F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=dist
OutputBaseFilename=icm-engine-setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
Source: "dist\icm-engine\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\icm-engine\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
