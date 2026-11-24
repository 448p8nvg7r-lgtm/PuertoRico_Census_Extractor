; Inno Setup script for Census Variable Extractor (requires Inno Setup 6.3+)
; Normally run by build_windows.ps1, which passes /DAppVersion=x.y.z

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define AppName "Census Variable Extractor"
#define ExeName "census-extract.exe"

[Setup]
AppId={{8C1F4E2A-5B7D-4C3E-9A61-2F0D3B7E9C14}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Census Extractor
DefaultDirName={autopf}\Census Extractor
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Installs for the current user without admin rights; admins can choose "all users".
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist\installer
OutputBaseFilename=CensusExtractor-Setup-{#AppVersion}
SetupIconFile=census.ico
UninstallDisplayIcon={app}\{#ExeName}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ChangesEnvironment=yes
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "addtopath"; Description: "Add the &census-extract command to PATH (for command-line use)"; GroupDescription: "Command line:"; Flags: unchecked

[Files]
Source: "..\dist\census-extract\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "census.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#ExeName}"; WorkingDir: "{userdocs}"; IconFilename: "{app}\census.ico"
Name: "{autoprograms}\{#AppName} - Command Prompt"; Filename: "{cmd}"; Parameters: "/k set ""PATH={app};%PATH%"" && census-extract --help"; WorkingDir: "{userdocs}"; IconFilename: "{app}\census.ico"
Name: "{autoprograms}\{#AppName} - Instructions"; Filename: "{win}\notepad.exe"; Parameters: """{app}\README.md"""
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#ExeName}"; WorkingDir: "{userdocs}"; IconFilename: "{app}\census.ico"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Environment"; ValueType: string; ValueName: "CENSUS_API_KEY"; ValueData: "{code:GetApiKey}"; Check: ApiKeyEntered; Flags: preservestringtype

[Run]
Filename: "{app}\{#ExeName}"; Description: "Launch {#AppName} now"; WorkingDir: "{userdocs}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
const
  EnvKey = 'Environment';

var
  KeyPage: TInputQueryWizardPage;

procedure InitializeWizard;
begin
  KeyPage := CreateInputQueryPage(wpSelectTasks,
    'Census API key', 'Optional: save your Census Data API key',
    'The Census Bureau requires a free API key to download data. Get one at' + #13#10 +
    'https://api.census.gov/data/key_signup.html and click the activation link in the email.' + #13#10 + #13#10 +
    'Paste it below to save it for your Windows account, or leave it blank and enter it in the app later.');
  KeyPage.Add('API key:', False);
  KeyPage.Values[0] := GetEnv('CENSUS_API_KEY');
end;

function GetApiKey(Param: String): String;
begin
  Result := Trim(KeyPage.Values[0]);
end;

function ApiKeyEntered: Boolean;
begin
  Result := Trim(KeyPage.Values[0]) <> '';
end;

procedure AddToPath(Dir: String);
var
  Paths: String;
begin
  if not RegQueryStringValue(HKCU, EnvKey, 'Path', Paths) then
    Paths := '';
  if Pos(';' + Uppercase(Dir) + ';', ';' + Uppercase(Paths) + ';') > 0 then
    exit;
  if (Paths <> '') and (Copy(Paths, Length(Paths), 1) <> ';') then
    Paths := Paths + ';';
  RegWriteExpandStringValue(HKCU, EnvKey, 'Path', Paths + Dir);
end;

procedure RemoveFromPath(Dir: String);
var
  Paths: String;
  P: Integer;
begin
  if not RegQueryStringValue(HKCU, EnvKey, 'Path', Paths) then
    exit;
  P := Pos(';' + Uppercase(Dir) + ';', ';' + Uppercase(Paths) + ';');
  if P = 0 then
    exit;
  if P > 1 then
    Delete(Paths, P - 1, Length(Dir) + 1)
  else
    Delete(Paths, P, Length(Dir) + 1);
  RegWriteExpandStringValue(HKCU, EnvKey, 'Path', Paths);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('addtopath') then
    AddToPath(ExpandConstant('{app}'));
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    RemoveFromPath(ExpandConstant('{app}'));
end;
