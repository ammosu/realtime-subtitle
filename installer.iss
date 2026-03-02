[Setup]
AppName=LiveSub+
AppVersion=0.1-Beta
AppPublisher=Anfu Solutions
DefaultDirName={autopf}\RealtimeSubtitle
DefaultGroupName=LiveSub+
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
Name: "{group}\LiveSub+"; Filename: "{app}\RealtimeSubtitle.exe"
Name: "{group}\解除安裝"; Filename: "{uninstallexe}"
Name: "{userdesktop}\LiveSub+"; Filename: "{app}\RealtimeSubtitle.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\RealtimeSubtitle.exe"; Description: "立即啟動 LiveSub+"; Flags: nowait postinstall skipifsilent
