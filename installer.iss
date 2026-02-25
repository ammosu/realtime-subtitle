[Setup]
AppName=Real-time Subtitle
AppVersion=1.0
AppPublisher=ammosu
DefaultDirName={autopf}\RealtimeSubtitle
DefaultGroupName=Real-time Subtitle
OutputDir=installer_output
OutputBaseFilename=RealtimeSubtitle-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "建立桌面捷徑"; GroupDescription: "額外選項"

[Files]
Source: "dist\RealtimeSubtitle\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Real-time Subtitle"; Filename: "{app}\RealtimeSubtitle.exe"
Name: "{group}\解除安裝"; Filename: "{uninstallexe}"
Name: "{userdesktop}\Real-time Subtitle"; Filename: "{app}\RealtimeSubtitle.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\RealtimeSubtitle.exe"; Description: "立即啟動 Real-time Subtitle"; Flags: nowait postinstall skipifsilent
