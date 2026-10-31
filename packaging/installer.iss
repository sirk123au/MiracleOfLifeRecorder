#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{C329B448-7EB3-4F38-AF89-16F58D02D302}
AppName=Miracle of Life Ultrasound Recorder
AppVersion={#AppVersion}
AppPublisher=Miracle of Life
DefaultDirName={localappdata}\Programs\MiracleOfLifeRecorder
DefaultGroupName=Miracle of Life Ultrasound Recorder
PrivilegesRequired=lowest
UsePreviousAppDir=yes
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\MiracleOfLifeRecorder.exe
AppMutex=Local\MiracleOfLifeRecorder
SetupMutex=MiracleOfLifeRecorderInstaller
CloseApplications=no
RestartApplications=no
OutputDir=..\dist\installer
OutputBaseFilename=MiracleOfLifeRecorder-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
MinVersion=10.0
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupLogging=no
SetupIconFile=..\build\application.ico

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\MiracleOfLifeRecorder.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Miracle of Life Ultrasound Recorder"; Filename: "{app}\MiracleOfLifeRecorder.exe"; WorkingDir: "{app}"
Name: "{userdesktop}\Miracle of Life Ultrasound Recorder"; Filename: "{app}\MiracleOfLifeRecorder.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\MiracleOfLifeRecorder.exe"; Description: "Open Miracle of Life Ultrasound Recorder"; Flags: nowait postinstall skipifsilent

; Settings, logos and recovery metadata live in APPDATA, not the install folder.
; Patient records and videos live on USB. Neither is installed or removed here.
; AppId stays constant across updates. AppMutex blocks update/uninstall while
; the recorder runs; CloseApplications=no prevents forced recording shutdown.
