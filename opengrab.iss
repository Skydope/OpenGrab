; OpenGrab — Inno Setup script (wizard de instalación para Windows)
;
; Build: iscc opengrab.iss
; Requiere: dist\OpenGrab\ (onedir de PyInstaller), vendor\opengrab.ico,
;           vendor\MicrosoftEdgeWebview2Setup.exe (WebView2 bootstrapper)

[Setup]
AppId={{8F3A1C92-7B5E-4D12-A6F8-9E2B0D4C8A71}
AppName=OpenGrab
AppVersion=1.10.0
AppPublisher=OpenGrab
AppPublisherURL=https://github.com/Skydope/OpenGrab
AppSupportURL=https://github.com/Skydope/OpenGrab/issues
DefaultDirName={autopf}\OpenGrab
DefaultGroupName=OpenGrab
UninstallDisplayIcon={app}\OpenGrab.exe
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=dist
OutputBaseFilename=OpenGrab-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
WizardSmallImageFile=vendor\wizard-small.bmp
WizardImageFile=vendor\wizard-image.bmp

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "webview2"; Description: "Instalar &WebView2 Runtime (ventana nativa)"; \
  GroupDescription: "Dependencias:"

[Files]
Source: "dist\OpenGrab\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs
Source: "vendor\opengrab.ico"; DestDir: "{app}"
Source: "vendor\MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; \
  Flags: deleteafterinstall; Tasks: webview2

[Icons]
Name: "{group}\OpenGrab"; Filename: "{app}\OpenGrab.exe"; \
  IconFilename: "{app}\opengrab.ico"
Name: "{commondesktop}\OpenGrab"; Filename: "{app}\OpenGrab.exe"; \
  IconFilename: "{app}\opengrab.ico"; Tasks: desktopicon
Name: "{group}\{cm:UninstallProgram,OpenGrab}"; Filename: "{uninstallexe}"

[Run]
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; \
  Parameters: "/silent /install"; \
  StatusMsg: "{cm:InstallingWebView2}"; \
  Tasks: webview2
Filename: "{app}\OpenGrab.exe"; \
  Description: "{cm:LaunchProgram,OpenGrab}"; \
  Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/c taskkill /f /im OpenGrab.exe"; \
  Flags: runhidden; RunOnceId: "KillOpenGrab"

[CustomMessages]
spanish.CreateDesktopIcon=Crear acceso &directo en el escritorio
spanish.InstallingWebView2=Instalando WebView2 Runtime...
spanish.InstallType=Tipo de instalación
spanish.InstallTypeTitle=Configuración inicial
spanish.InstallTypeDesc=Elegí el modo de instalación.
spanish.Recommended=Recomendada
spanish.RecommendedDesc=Todo listo con los valores predeterminados. Ideal para la mayoría.
spanish.Advanced=Avanzada (personalizar)
spanish.AdvancedDesc=Personalizá carpeta de descargas, puerto y contraseña.
spanish.AdvancedConfigTitle=Configuración avanzada
spanish.AdvancedConfigDesc=Personalizá la configuración inicial. Podés cambiarla después desde %APPDATA%\OpenGrab\config.ini.
spanish.DownloadFolder=Carpeta de descargas:
spanish.Port=Puerto (0 = automático):
spanish.Password=Contraseña (vacío = sin contraseña):
spanish.DownloadFolderPageTitle=Carpeta de descargas
spanish.DownloadFolderPageDesc=Los videos se guardarán en esta carpeta.
spanish.AutoStart=Iniciar con &Windows

english.CreateDesktopIcon=Create &desktop shortcut
english.InstallingWebView2=Installing WebView2 Runtime...
english.InstallType=Installation type
english.InstallTypeTitle=Initial setup
english.InstallTypeDesc=Choose the installation mode.
english.Recommended=Recommended
english.RecommendedDesc=Ready to go with default settings. Best for most users.
english.Advanced=Advanced (customize)
english.AdvancedDesc=Customize download folder, port, and password.
english.AdvancedConfigTitle=Advanced configuration
english.AdvancedConfigDesc=Customize the initial settings. You can change them later in %APPDATA%\OpenGrab\config.ini.
english.DownloadFolder=Download folder:
english.Port=Port (0 = automatic):
english.Password=Password (empty = no password):
english.DownloadFolderPageTitle=Download folder
english.DownloadFolderPageDesc=Videos will be saved in this folder.
english.AutoStart=Start with &Windows
english.WebView2Failed=WebView2 Runtime was not installed correctly.%n%nOpenGrab will continue to work in your browser.%n%nFor the native window, reinstall WebView2 from:%nhttps://go.microsoft.com/fwlink/p/?LinkId=2124703
spanish.WebView2Failed=El WebView2 Runtime no se instaló correctamente.%n%nOpenGrab seguirá funcionando en el navegador.%n%nPara tener la ventana nativa, reinstalá WebView2 desde:%nhttps://go.microsoft.com/fwlink/p/?LinkId=2124703

[Code]
var
  PageTipo: TInputOptionWizardPage;
  PageAvanzada: TInputQueryWizardPage;
  PageDescargas: TInputDirWizardPage;
  PageAutoStart: TInputOptionWizardPage;

function IsRecommended: Boolean;
begin
  Result := PageTipo.Values[0];
end;

function WebView2Installed(): Boolean;
var
  Version: string;
  Major: Integer;
  Paths: array of string;
  I: Integer;
begin
  Result := False;
  Paths := [
    'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}',
    'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
  ];

  for I := 0 to GetArrayLength(Paths)-1 do
  begin
    if RegQueryStringValue(HKEY_LOCAL_MACHINE, Paths[I], 'pv', Version) or
       RegQueryStringValue(HKEY_CURRENT_USER, Paths[I], 'pv', Version) then
    begin
      Major := StrToIntDef(Copy(Version, 1, Pos('.', Version) - 1), 0);
      if Major >= 86 then
      begin
        Result := True;
        Exit;
      end;
    end;
  end;
end;

procedure InitializeWizard();
var
  DefaultDownloads: string;
begin
  DefaultDownloads := ExpandConstant('{userdocs}\Downloads\OpenGrab');

  { Página 3: Tipo de instalación }
  PageTipo := CreateInputOptionPage(wpLicense,
    CustomMessage('InstallTypeTitle'),
    CustomMessage('InstallTypeDesc'),
    '',
    True, False);
  PageTipo.Add(CustomMessage('Recommended') + #13#10 + '  ' + CustomMessage('RecommendedDesc'));
  PageTipo.Add(CustomMessage('Advanced') + #13#10 + '  ' + CustomMessage('AdvancedDesc'));
  PageTipo.Values[0] := True;

  { Página 4: Avanzada (visible solo si Avanzada) }
  PageAvanzada := CreateInputQueryPage(PageTipo.ID,
    CustomMessage('AdvancedConfigTitle'),
    CustomMessage('AdvancedConfigDesc'),
    '');
  PageAvanzada.Add(CustomMessage('DownloadFolder'), False);
  PageAvanzada.Add(CustomMessage('Port'), False);
  PageAvanzada.Add(CustomMessage('Password'), False);
  PageAvanzada.Values[0] := DefaultDownloads;
  PageAvanzada.Values[1] := '0';
  PageAvanzada.Values[2] := '';

  { Página 5: Carpeta de descargas (solo en Recomendada) }
  PageDescargas := CreateInputDirPage(PageAvanzada.ID,
    CustomMessage('DownloadFolderPageTitle'),
    CustomMessage('DownloadFolderPageDesc'),
    '',
    False, '');
  PageDescargas.Add('');
  PageDescargas.Values[0] := DefaultDownloads;

  { Página 6: Autostart (solo en Avanzada) }
  PageAutoStart := CreateInputOptionPage(PageDescargas.ID,
    CustomMessage('InstallTypeTitle'),
    CustomMessage('InstallTypeDesc'),
    '',
    True, False);
  PageAutoStart.Add(CustomMessage('AutoStart'));
  PageAutoStart.Values[0] := False;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  if (PageID = PageAvanzada.ID) and IsRecommended then
    Result := True
  else if (PageID = PageDescargas.ID) and not IsRecommended then
    Result := True
  else if (PageID = PageAutoStart.ID) and IsRecommended then
    Result := True
  else
    Result := False;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  DownloadDir: string;
begin
  Result := True;
  if CurPageID = PageDescargas.ID then
  begin
    DownloadDir := PageDescargas.Values[0];
    if not DirExists(DownloadDir) then
      CreateDir(DownloadDir);
  end;
end;

{ Escribe config.ini en %APPDATA%\OpenGrab\ }
procedure WriteConfig();
var
  ConfigDir, ConfigPath, DownloadDir, Port, Token, Content: string;
begin
  ConfigDir := ExpandConstant('{userappdata}\OpenGrab');
  ForceDirectories(ConfigDir);
  ConfigPath := ConfigDir + '\config.ini';

  if IsRecommended then
  begin
    DownloadDir := PageDescargas.Values[0];
    Port := '0';
    Token := '';
  end
  else
  begin
    DownloadDir := PageAvanzada.Values[0];
    Port := PageAvanzada.Values[1];
    Token := PageAvanzada.Values[2];
  end;

  Content := '[opengrab]' + #13#10 +
    'download_dir = ' + DownloadDir + #13#10 +
    'port = ' + Port + #13#10;

  if Token <> '' then
    Content := Content +
      'no_auth = false' + #13#10 +
      'token = ' + Token + #13#10
  else
    Content := Content + 'no_auth = true' + #13#10;

  SaveStringToFile(ConfigPath, Content, False);
end;

{ Registro de auto-start }
procedure SetAutoStart(Enable: Boolean);
var
  Key: string;
begin
  Key := 'Software\Microsoft\Windows\CurrentVersion\Run';
  if Enable then
    RegWriteStringValue(HKEY_CURRENT_USER, Key, 'OpenGrab',
      ExpandConstant('"{app}\OpenGrab.exe"'))
  else
    RegDeleteValue(HKEY_CURRENT_USER, Key, 'OpenGrab');
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    WriteConfig();
    if not IsRecommended then
      SetAutoStart(PageAutoStart.Values[0]);

    { Validar que el bootstrapper de WebView2 se instaló correctamente }
    if WizardIsTaskSelected('webview2') then
    begin
      if not WebView2Installed() then
        MsgBox(CustomMessage('WebView2Failed'), mbInformation, MB_OK);
    end;
  end;
end;
