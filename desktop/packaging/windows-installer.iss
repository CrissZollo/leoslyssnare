; Inno Setup script for Leos Lyssnare. Called by build-windows.ps1, which
; passes AppVersion and SourceDir (the PyInstaller output folder).
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\build\windows\dist\LeosLyssnare"
#endif

[Setup]
AppId={{6C1F3B0E-5B57-4C2B-9E0A-1E6C3D2A9F41}
AppName=Leos Lyssnare
AppVersion={#AppVersion}
AppPublisher=Leos Lyssnare
DefaultDirName={autopf}\Leos Lyssnare
DefaultGroupName=Leos Lyssnare
DisableProgramGroupPage=yes
; Installs for the current user without needing administrator rights.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputBaseFilename=Leos_Lyssnare-{#AppVersion}-windows-x64-setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\LeosLyssnare.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Leos Lyssnare"; Filename: "{app}\LeosLyssnare.exe"
Name: "{autodesktop}\Leos Lyssnare"; Filename: "{app}\LeosLyssnare.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\LeosLyssnare.exe"; Description: "{cm:LaunchProgram,Leos Lyssnare}"; Flags: nowait postinstall skipifsilent
