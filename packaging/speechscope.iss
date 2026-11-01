; Inno Setup 6: instalátor SpeechScope pro uživatele bez admin práv.
; Vstup: dist\SpeechScope\ (GUI + speechscope-lib), výstup: dist\SpeechScope-Setup-<verze>.exe
; Sestavuje packaging\build.ps1.

#define AppName "SpeechScope"
#define AppVersion "0.4.0"
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
; nahrávky ani modely instalátor nesahá; aplikace navrhne modely do {app}\models
; (uživatel může změnit na uvítání nebo v Prostředí), odinstalace je nemaže

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

[CustomMessages]
czech.DeleteData=Smazat i data aplikace?%n%nPacienti a hodnocení, vlastní normy a protokoly, rozpracovaná hodnocení a nastavení ve složce:%n%1%n%nVýsledky v Dokumentech a nahrávky se nemažou. Při přeinstalaci odpovězte Ne.
english.DeleteData=Also delete the application data?%n%nPatients and assessments, custom norms and protocols, unfinished assessments and settings in the folder:%n%1%n%nResults in Documents and recordings are not deleted. Answer No when reinstalling.

[Code]
// Data aplikace (katalog pacientů, normy, protokoly) leží mimo {app} a odinstalace je
// nechává; na dotaz (výchozí Ne) je smaže (1. 11. 2026).
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\SAMI\SpeechScopeApp');
    if DirExists(DataDir) and not UninstallSilent then
      if MsgBox(FmtMessage(CustomMessage('DeleteData'), [DataDir]), mbConfirmation,
                MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;

[UninstallDelete]
; __pycache__ vzniklé za běhu knihovny
Type: filesandordirs; Name: "{app}\speechscope-lib\Lib\site-packages\speechscope\__pycache__"
