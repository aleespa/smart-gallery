; Inno Setup 6 script for the Smart Gallery desktop app.
;
; Packages the PyInstaller folder dist\SmartGallery\ into
; dist\SmartGallery-Setup-<version>.exe. A per-user install: no admin rights,
; installed under %LOCALAPPDATA%\Programs\SmartGallery.
;
; Build it with packaging\build.ps1, which passes the version from
; pyproject.toml:  iscc /DVersion=0.1.0 packaging\installer.iss

#ifndef Version
  #error Version is not defined. Run packaging\build.ps1, or pass /DVersion=x.y.z to iscc.
#endif

#define AppName "Smart Gallery"
#define AppExe "SmartGallery.exe"

[Setup]
; Keep this AppId fixed: it is how upgrades and the uninstaller find the app.
AppId={{5566F90C-E4AB-4592-B5BE-D9E777D476F3}
AppName={#AppName}
AppVersion={#Version}
DefaultDirName={localappdata}\Programs\SmartGallery
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=SmartGallery-Setup-{#Version}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}

[InstallDelete]
; Drop the previous version's runtime so upgrades leave no stale modules.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\SmartGallery\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
