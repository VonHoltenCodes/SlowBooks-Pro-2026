; Inno Setup script for SlowBooks Pro 2026 (Windows installer).
; Built in CI: iscc /DAppVersion=<x.y.z> SlowBooksPro.iss
; Paths are relative to this .iss file (packaging/windows).
;
; All user data (.env, company databases, uploads, backups, logs) lives in
; %LOCALAPPDATA%\SlowBooksPro — this installer never writes there, upgrades
; replace only {app}, and the uninstaller leaves the data intact.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{f67ccf1c-2072-4f4a-9102-4e3897758a97}
AppName=SlowBooks Pro 2026
AppVersion={#AppVersion}
AppPublisher=VonHoltenCodes
AppPublisherURL=https://github.com/VonHoltenCodes/SlowBooks-Pro-2026
DefaultDirName={autopf}\SlowBooks Pro 2026
DefaultGroupName=SlowBooks Pro 2026
UninstallDisplayIcon={app}\SlowBooksPro.exe
; The installer and the uninstaller show the app icon (the .ico the CI build writes beside this file).
SetupIconFile=slowbookspro.ico
OutputDir=.
OutputBaseFilename=SlowBooksPro-Setup-x64
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
WizardStyle=modern
; LICENSE section 13: the installer shows the license and requires acceptance.
LicenseFile=..\..\LICENSE
DisableProgramGroupPage=yes
; Ask running instances to close before upgrading. The uvicorn server child
; (--_serve) has no window, so [Code] below also stops it by name.
CloseApplications=yes
; Per user unless an administrator chooses all users (#699). Before, the
; default (admin) stopped a standard user at "You must be logged in as an
; administrator". The first page asks; /ALLUSERS and /CURRENTUSER answer it
; from the command line. An upgrade skips the question and keeps the mode
; the copy already installed was installed in (UsePreviousPrivileges): an
; all-users copy asks for an administrator's password (UAC), and
; InitializeSetup below stops a run that would otherwise end up beside it.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline dialog
UsePreviousPrivileges=yes

; The install-mode page, in plain words.
[Messages]
PrivilegesRequiredOverrideTitle=Who is SlowBooks Pro for?
PrivilegesRequiredOverrideInstruction=Install for you, or for everyone on this computer
PrivilegesRequiredOverrideText1=%1 can be installed for everyone who signs in to this computer, which needs an administrator's password, or for you only, which does not. Your books are kept in your own folder either way.
PrivilegesRequiredOverrideText2=%1 can be installed for you only, which needs no administrator, or for everyone who signs in to this computer, which needs an administrator's password. Your books are kept in your own folder either way.
PrivilegesRequiredOverrideAllUsers=Install for &all users (administrator)
PrivilegesRequiredOverrideAllUsersRecommended=Install for &all users (administrator, recommended)
PrivilegesRequiredOverrideCurrentUser=Install for &me only
PrivilegesRequiredOverrideCurrentUserRecommended=Install for &me only (no administrator needed, recommended)

; An upgrade must not layer today's bundle over last month's. Inno Setup
; only overwrites files it ships; everything else under {app} survives, so
; every upgrade left the previous build's *.dist-info trees (and any module
; the new build dropped) in _internal\. importlib.metadata then reported
; whichever version it found first — cryptography 48.0.1 while the 50.0.1
; extension was the code running (2.9.0 gate, skytech). User data is never
; under {app} (see the header), so clearing it is safe.
[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Files]
; ignoreversion: every build is stamped x.y.z.0 (version_info.py), so a
; same-version reinstall kept the old SlowBooksPro.exe ("Same version.
; Skipping.") while replacing everything else (#643).
Source: "dist\SlowBooksPro\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; The license the wizard showed, kept beside the program.
Source: "..\..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"
; Microsoft's Evergreen WebView2 bootstrapper (~2 MB), downloaded by CI.
; Only executed when the runtime is missing or older than the app's window
; needs (see [Run] Check) — Windows 11 and most Windows 10 machines already
; have a current one.
Source: "MicrosoftEdgeWebView2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall

[Icons]
Name: "{group}\SlowBooks Pro 2026"; Filename: "{app}\SlowBooksPro.exe"
Name: "{group}\Uninstall SlowBooks Pro 2026"; Filename: "{uninstallexe}"
Name: "{autodesktop}\SlowBooks Pro 2026"; Filename: "{app}\SlowBooksPro.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Run]
Filename: "{tmp}\MicrosoftEdgeWebView2Setup.exe"; Parameters: "/silent /install"; \
  StatusMsg: "Installing or updating the Microsoft WebView2 runtime (app window component)..."; \
  Check: not IsWebView2Current; AfterInstall: ReportWebView2; Flags: waituntilterminated
; A Server Edition host (the SlowBooksProServer task exists) gets its server
; started again instead of a desktop window: PrepareToInstall killed the
; server, nothing restarted the task before the next boot, and the window
; the launch box opened then held port 3001 against it (2.31.3). Only an
; all-users install: the task is an administrator's (it runs as SYSTEM), a
; standard user may not start it, and a per-user install did not stop it.
Filename: "{sys}\schtasks.exe"; Parameters: "/Run /TN SlowBooksProServer"; \
  StatusMsg: "Starting the Server Edition task..."; \
  Check: IsAdminInstallMode and ServerTaskExists; Flags: runhidden waituntilterminated
Filename: "{app}\SlowBooksPro.exe"; Description: "Launch SlowBooks Pro 2026"; Flags: nowait postinstall skipifsilent; \
  Check: not ServerTaskExists

[Code]
// The WebView2 runtime the app's window needs: MIN_WEBVIEW2 in
// desktop_launcher.py, which says why (tests/test_windows_packaging.py keeps
// the two equal). A runtime that is there but older opened a window that
// stayed white, and reinstalling did not help: the bootstrapper ran only
// when there was no runtime at all (a support email on 2.29.2).
const
  MinWebView2 = '101.0.1210.39';
  WebView2Client = 'Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  ServerTask = 'SlowBooksProServer';
  // Where an all-users install registers its uninstaller: Inno Setup's
  // key for the AppId in [Setup], in the 64-bit view (x64 install mode).
  AllUsersUninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{f67ccf1c-2072-4f4a-9102-4e3897758a97}_is1';
  WebView2Page = 'https://developer.microsoft.com/microsoft-edge/webview2/';

var
  ServerTaskChecked: Boolean;
  ServerTaskFound: Boolean;

// Whether this PC is a Server Edition host: the startup task the install
// script registers exists (schtasks /Query exits 0). Asked once per run.
function ServerTaskExists(): Boolean;
var
  ResultCode: Integer;
begin
  if not ServerTaskChecked then
  begin
    ServerTaskChecked := True;
    ServerTaskFound := Exec(ExpandConstant('{sys}\schtasks.exe'), '/Query /TN ' + ServerTask,
      '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
  end;
  Result := ServerTaskFound;
end;

// The number before the next dot of S, taken off the front of S; -1 when
// that part is not a number.
function NextVersionPart(var S: String): Integer;
var
  Dot: Integer;
begin
  Dot := Pos('.', S);
  if Dot = 0 then
  begin
    Result := StrToIntDef(Trim(S), -1);
    S := '';
  end
  else
  begin
    Result := StrToIntDef(Trim(Copy(S, 1, Dot - 1)), -1);
    Delete(S, 1, Dot);
  end;
end;

// Two dotted versions compared part by part, as numbers ('99' is lower than
// '130', which a comparison of the text gets backwards): -1 when A is lower
// than B, 0 when they are the same, 1 when A is higher. A part one of them
// does not have counts as 0.
function CompareDottedVersions(A, B: String): Integer;
var
  RestA, RestB: String;
  PartA, PartB: Integer;
begin
  RestA := A;
  RestB := B;
  Result := 0;
  while (Result = 0) and ((RestA <> '') or (RestB <> '')) do
  begin
    PartA := 0;
    PartB := 0;
    if RestA <> '' then
      PartA := NextVersionPart(RestA);
    if RestB <> '' then
      PartB := NextVersionPart(RestB);
    if PartA < PartB then
      Result := -1
    else if PartA > PartB then
      Result := 1;
  end;
end;

// Raises Highest to the runtime version under one registry key, when that
// key names a runtime that is there: 0.0.0.0 is what an uninstall leaves
// behind (Microsoft's docs), so it counts as none.
procedure TakeHigherVersion(var Highest: String; RootKey: Integer; SubKey: String);
var
  Version: String;
begin
  if RegQueryStringValue(RootKey, SubKey, 'pv', Version) then
    if CompareDottedVersions(Version, '0') > 0 then
      if CompareDottedVersions(Version, Highest) > 0 then
        Highest := Version;
end;

// The Evergreen runtime's version: the highest one its registry keys name,
// per machine (64-bit Windows, then 32-bit) or per user; '' when there is
// none. The keys Microsoft documents, and desktop_launcher.py reads.
function WebView2Version(): String;
var
  Highest: String;
begin
  Highest := '';
  TakeHigherVersion(Highest, HKLM, 'SOFTWARE\WOW6432Node\' + WebView2Client);
  TakeHigherVersion(Highest, HKLM, 'SOFTWARE\' + WebView2Client);
  TakeHigherVersion(Highest, HKCU, 'Software\' + WebView2Client);
  Result := Highest;
end;

// Installed AND at least MinWebView2. When it is not, [Run] runs Microsoft's
// bootstrapper: it installs a missing runtime, and run elevated, as this
// installer is, over an older one it installs the current version in its
// place (WebView2Feedback #3524; Microsoft documents the bootstrapper only
// for a missing runtime). Whatever it cannot fix (offline, a policy that
// holds updates back), the launcher still names at start, with the version.
function IsWebView2Current(): Boolean;
var
  Version: String;
begin
  Version := WebView2Version();
  Result := (Version <> '') and (CompareDottedVersions(Version, MinWebView2) >= 0);
end;

// The server runs as a second, windowless SlowBooksPro.exe process
// (--_serve). CloseApplications can't reach it (no window, no message
// loop), so stop every SlowBooksPro.exe by name before install/uninstall
// — otherwise file-in-use errors block the upgrade.
// After the bootstrapper ran: when the runtime is still missing or old, say
// so now rather than leave a window that cannot open. The bootstrapper run
// for one user (a per-user install is not elevated) installs a missing
// runtime for that user, but it cannot replace an old runtime installed for
// everyone; offline, neither mode can. SlowBooks itself is installed either
// way, and the launcher says the same at start and offers the browser.
procedure ReportWebView2();
var
  Found, Why: String;
begin
  if IsWebView2Current() then
    Exit;
  Found := WebView2Version();
  if Found = '' then
    Found := 'none';
  if IsAdminInstallMode then
    Why := 'The installer could not install or update it. It downloads it from Microsoft, ' +
      'so it needs the internet for that step.'
  else
    Why := 'Installing or updating it for everyone on this computer needs an administrator, ' +
      'and this install is for you only.';
  SuppressibleMsgBox('SlowBooks Pro 2026 is installed, but its window needs the Microsoft Edge ' +
    'WebView2 runtime version ' + MinWebView2 + ' or later, and this computer has ' + Found + '.' + #13#10#13#10 +
    Why + #13#10#13#10 +
    'Install or update it from Microsoft: ' + WebView2Page + ' (the Evergreen Bootstrapper), ' +
    'or ask an administrator to. Until then, SlowBooks offers to open in your web browser.',
    mbInformation, MB_OK, IDOK);
end;

// A copy installed for all users (Program Files) that this run, for one
// user, cannot replace: a standard user who declined or could not answer
// the administrator prompt, or /CURRENTUSER. Installing beside it would
// leave two copies, two Start-menu entries and the old one still current
// for everyone else, so Setup says so and stops before changing anything.
function AllUsersInstallExists(): Boolean;
begin
  Result := RegKeyExists(HKLM64, AllUsersUninstallKey) or RegKeyExists(HKLM32, AllUsersUninstallKey);
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if (not IsAdminInstallMode) and AllUsersInstallExists() then
  begin
    SuppressibleMsgBox('SlowBooks Pro 2026 is already installed for all users on this computer ' +
      '(in Program Files), and only an administrator can update that copy.' + #13#10#13#10 +
      'Ask an administrator to run this installer, or to uninstall SlowBooks Pro 2026 so you can ' +
      'install it for you only. Nothing has been changed, and your books are not touched either way.',
      mbError, MB_OK, IDOK);
    Result := False;
  end;
end;

procedure KillAppProcesses();
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM SlowBooksPro.exe /T',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  KillAppProcesses();
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  KillAppProcesses();
  Result := True;
end;
