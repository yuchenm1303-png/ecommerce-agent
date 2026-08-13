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

[Setup]
AppId={{84E09CC8-51F4-4409-BC73-B5EBC9A4D84A}
AppName={#MyAppName}
AppVersion={#AppVersion}
AppVerName={#MyAppName} {#AppVersion}
AppPublisher=ecommerce-agent
VersionInfoCompany=ecommerce-agent
VersionInfoDescription={#MyAppName} Setup
VersionInfoProductName={#MyAppName}
VersionInfoVersion={#AppVersion}
VersionInfoProductVersion={#AppVersion}
VersionInfoTextVersion={#AppVersion}
VersionInfoProductTextVersion={#AppVersion}
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
UninstallDisplayIcon={app}\{#MyAppExeName}
CloseApplications=yes
RestartApplications=yes
SetupLogging=yes
UsePreviousAppDir=yes

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式:"; Flags: unchecked

[InstallDelete]
Type: files; Name: "{app}\icons\EcommerceAgent-*.ico"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\EcommerceAgent Listing Studio"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; IconIndex: 0
Name: "{autodesktop}\EcommerceAgent Listing Studio"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; IconIndex: 0; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 EcommerceAgent Listing Studio"; Flags: nowait postinstall skipifsilent
Filename: "{app}\{#MyAppExeName}"; Flags: nowait skipifdoesntexist; Check: FileExists(ExpandConstant('{localappdata}\ListingStudio\update-complete.json'))

[Code]
const
  WM_CLOSE = $0010;
  ListingStudioWindowTitle = 'ecommerce-agent · Listing Automation';
  LegacyWindowTitle = 'ecommerce-agent · Acceptance Control Console';

function FindListingStudioWindow: HWND;
begin
  Result := FindWindowByWindowName(ListingStudioWindowTitle);
  if Result = 0 then
    Result := FindWindowByWindowName(LegacyWindowTitle);
end;

function CloseListingStudioBeforeUninstall: Boolean;
var
  Wnd: HWND;
  Attempt: Integer;
begin
  Wnd := FindListingStudioWindow;
  if Wnd = 0 then
  begin
    Result := True;
    exit;
  end;

  { Ask the Qt main window to follow its normal close path. This lets the GUI
    tear down owned QProcess workers before Uninstall touches the onedir files. }
  PostMessage(Wnd, WM_CLOSE, 0, 0);
  for Attempt := 1 to 50 do
  begin
    Sleep(100);
    if FindListingStudioWindow = 0 then
    begin
      { Give QApplication/QProcess destruction a short final settle before file deletion. }
      Sleep(1000);
      Result := True;
      exit;
    end;
  end;

  MsgBox(
    'Listing Studio 仍在运行，卸载已停止。' + #13#10 + #13#10 +
    '请先关闭程序并等待当前任务结束，然后重新卸载。' + #13#10 +
    '卸载器不会在程序仍占用安装文件时继续删除。',
    mbError,
    MB_OK
  );
  Result := False;
end;

function InitializeUninstall: Boolean;
begin
  Result := CloseListingStudioBeforeUninstall;
end;
