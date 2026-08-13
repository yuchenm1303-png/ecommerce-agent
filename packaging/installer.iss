#define MyAppName "EcommerceAgent Listing Studio"
#define MyAppExeName "EcommerceAgent.exe"

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\EcommerceAgent"
#endif
#ifndef OutputDir
  #define OutputDir "..\artifacts"
#endif
#ifndef IconFile
  #define IconFile "app_icon.ico"
#endif

#define InstalledIconName "EcommerceAgent-" + AppVersion + ".ico"

[Setup]
AppId={{84E09CC8-51F4-4409-BC73-B5EBC9A4D84A}
AppName={#MyAppName}
AppVersion={#AppVersion}
AppPublisher=ecommerce-agent
DefaultDirName={localappdata}\Programs\EcommerceAgent
DefaultGroupName=EcommerceAgent
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename=EcommerceAgent-Setup-{#AppVersion}
SetupIconFile={#IconFile}
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
WizardStyle=modern
UninstallDisplayIcon={app}\icons\{#InstalledIconName}
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
UsePreviousAppDir=yes

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式:"; Flags: unchecked

[InstallDelete]
Type: files; Name: "{app}\icons\EcommerceAgent-*.ico"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#IconFile}"; DestDir: "{app}\icons"; DestName: "{#InstalledIconName}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\EcommerceAgent Listing Studio"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\icons\{#InstalledIconName}"; IconIndex: 0
Name: "{autodesktop}\EcommerceAgent Listing Studio"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\icons\{#InstalledIconName}"; IconIndex: 0; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 EcommerceAgent Listing Studio"; Flags: nowait postinstall skipifsilent
