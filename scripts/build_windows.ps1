param(
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($Version)) {
    $Version = (Get-Content (Join-Path $Root "packaging\VERSION") -Raw).Trim()
}
if ($Version -notmatch '^\d+\.\d+\.\d+([.-][0-9A-Za-z.-]+)?$') {
    throw "Invalid package version: $Version"
}

$DistRoot = Join-Path $Root "dist"
$AppDir = Join-Path $DistRoot "EcommerceAgent"
$WorkDir = Join-Path $Root "build\pyinstaller"
$ArtifactDir = Join-Path $Root "artifacts"
$PortableZip = Join-Path $ArtifactDir "EcommerceAgent-$Version-portable.zip"
$SetupExe = Join-Path $ArtifactDir "EcommerceAgent-Setup-$Version.exe"

foreach ($Path in @($AppDir, $WorkDir, $PortableZip, $SetupExe)) {
    if (Test-Path $Path) {
        Remove-Item $Path -Recurse -Force
    }
}
New-Item -ItemType Directory -Force -Path $DistRoot, $WorkDir, $ArtifactDir | Out-Null

Write-Host "[1/4] Building PyInstaller onedir package $Version"
& python -m PyInstaller `
    --noconfirm `
    --clean `
    --distpath $DistRoot `
    --workpath $WorkDir `
    (Join-Path $Root "packaging\EcommerceAgent.spec")
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$GuiExe = Join-Path $AppDir "EcommerceAgent.exe"
$WorkerExe = Join-Path $AppDir "EcommerceAgentWorker.exe"
foreach ($Required in @($GuiExe, $WorkerExe)) {
    if (-not (Test-Path $Required)) {
        throw "Packaging output missing: $Required"
    }
}

Write-Host "[2/4] Verifying packaged Playwright worker"
& $WorkerExe --self-test
if ($LASTEXITCODE -ne 0) {
    throw "Packaged worker self-test failed with exit code $LASTEXITCODE"
}

Write-Host "[3/4] Creating portable archive"
Compress-Archive -Path (Join-Path $AppDir "*") -DestinationPath $PortableZip -CompressionLevel Optimal

$IsccFromPath = Get-Command ISCC.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1
$IsccCandidates = @(
    $IsccFromPath,
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 7\ISCC.exe"),
    (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:ProgramFiles "Inno Setup 7\ISCC.exe")
) | Where-Object { $_ -and (Test-Path $_) }
$Iscc = $IsccCandidates | Select-Object -First 1
if (-not $Iscc) {
    throw "Inno Setup compiler (ISCC.exe) was not found. Install Inno Setup 6+ and rerun."
}

Write-Host "[4/4] Building installer"
& $Iscc `
    "/DAppVersion=$Version" `
    "/DSourceDir=$AppDir" `
    "/DOutputDir=$ArtifactDir" `
    (Join-Path $Root "packaging\installer.iss")
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup failed with exit code $LASTEXITCODE"
}
if (-not (Test-Path $SetupExe)) {
    throw "Installer output missing: $SetupExe"
}

Write-Host ""
Write-Host "Windows package ready:"
Write-Host "  Installer: $SetupExe"
Write-Host "  Portable : $PortableZip"
Write-Host "  App dir  : $AppDir"
