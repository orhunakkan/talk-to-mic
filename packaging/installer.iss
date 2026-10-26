; Inno Setup script: packs dist\talk-to-mic into dist\talk-to-mic-setup-<version>.exe.
; Installs per user (no admin prompt) to %LOCALAPPDATA%\Programs\talk-to-mic. Run via build.ps1.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6F1C2A4E-9B3D-4C8A-A7E5-2D4B8F0C1E93}
AppName=talk-to-mic
AppVersion={#AppVersion}
AppVerName=talk-to-mic {#AppVersion}
AppPublisher=Orhun Akkan
DefaultDirName={autopf}\talk-to-mic
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=talk-to-mic-setup-{#AppVersion}
SetupIconFile=..\build\icon.ico
UninstallDisplayIcon={app}\talk-to-mic.exe
UninstallDisplayName=talk-to-mic
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
; The running app is closed in [Code] below instead.
CloseApplications=no

[Tasks]
Name: "startup"; Description: "Start talk-to-mic when I sign in to Windows"
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "..\dist\talk-to-mic\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; Drop files left by an older version so stale DLLs are never picked up.
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\talk-to-mic"; Filename: "{app}\talk-to-mic.exe"
Name: "{autodesktop}\talk-to-mic"; Filename: "{app}\talk-to-mic.exe"; Tasks: desktopicon
Name: "{userstartup}\talk-to-mic"; Filename: "{app}\talk-to-mic.exe"; Tasks: startup

[Run]
Filename: "{app}\talk-to-mic.exe"; Description: "Start talk-to-mic now"; Flags: nowait postinstall skipifsilent

[Code]
// The tray app holds its files open, so stop it before upgrading or uninstalling.
procedure StopRunningApp;
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM talk-to-mic.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopRunningApp;
  Result := '';
end;

function InitializeUninstall: Boolean;
begin
  StopRunningApp;
  Result := True;
end;
