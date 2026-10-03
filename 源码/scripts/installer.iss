; 在项目根目录运行 ISCC.exe scripts\installer.iss。
; 先执行 scripts\build.ps1，安装包会包含完整的目录版文件。
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{BD2CDA01-6B6C-4ECF-924A-36A38D8910CE}
AppName=ClipNest
AppVersion={#AppVersion}
AppPublisher=ClipNest contributors
DefaultDirName={localappdata}\Programs\ClipNest
DefaultGroupName=ClipNest
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist\installer
OutputBaseFilename=ClipNest-{#AppVersion}-Setup
SetupIconFile=..\build\assets\ClipNest.ico
UninstallDisplayIcon={app}\ClipNest.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "其他选项："; Flags: unchecked

[Files]
Source: "..\dist\ClipNest\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\ClipNest"; Filename: "{app}\ClipNest.exe"
Name: "{autodesktop}\ClipNest"; Filename: "{app}\ClipNest.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ClipNest.exe"; Description: "启动 ClipNest"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 历史数据在 %LOCALAPPDATA%\ClipNest，卸载保留数据，不自动删除用户历史。
