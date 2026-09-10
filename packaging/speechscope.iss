; Inno Setup 6: instalátor SpeechScope pro uživatele bez admin práv.
; Vstup: dist\SpeechScope\ (GUI + speechscope-lib), výstup: dist\SpeechScope-Setup-<verze>.exe
; Sestavuje packaging\build.ps1.

#define AppName "SpeechScope"
#define AppVersion "0.1.0"
#define AppPublisher "SAMI, FEL ČVUT v Praze"
#define AppExe "SpeechScope.exe"

[Setup]
AppId={{7D3C2B1E-5A44-4F0C-9E7B-3A1F6C2D9B10}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\{#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename={#AppName}-Setup-{#AppVersion}
SetupIconFile=..\src\speechscope_app\assets\speechscope.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; nahrávky ani modely instalátor nesahá; modely jdou do %LOCALAPPDATA%\SAMI\SpeechScopeApp\models

[Languages]
Name: "czech"; MessagesFile: "compiler:Languages\Czech.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\SpeechScope\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; __pycache__ vzniklé za běhu knihovny
Type: filesandordirs; Name: "{app}\speechscope-lib\Lib\site-packages\speechscope\__pycache__"
